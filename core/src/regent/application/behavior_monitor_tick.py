"""Periodic behavior-monitoring tick for the worker main loop.

The orchestrator runs the behavior monitor once when a preview deployment
succeeds.  That is a one-shot observation: after the pipeline goes idle,
nobody re-observes the deployed application, so regressions and unfixed
anomalies go unnoticed.  This module provides ``tick_behavior_monitoring``
for the worker's periodic loop (same pattern as host_guard /
reconciliation_worker): it re-observes ACTIVE goals that opted into
monitoring (``org_mode.enable_monitoring``) using the preview URL persisted
by the orchestrator (``behavior_monitor_preview_url``), and — when the
repair loop is enabled — feeds observations into ``BehaviorRepairLoop``
with execution re-trigger enabled.

Safety properties:
- Observation-only per goal unless ``org_mode.enable_repair_loop`` is set.
- Per-goal minimum re-observation interval (``MONITOR_MIN_INTERVAL_SECONDS``)
  prevents tight observe loops even if the worker tick is frequent.
- All per-goal failures are caught and logged; the tick never raises.
- Repair re-trigger is bounded by the repair loop's own caps
  (cooldown, ``max_iterations``, budget, live-run check).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.infrastructure.models import GoalModel

logger = logging.getLogger(__name__)

# Minimum time between observations of the same goal.
MONITOR_MIN_INTERVAL_SECONDS = 600.0

# Upper bound on goals examined per tick (post-status filter).
_MAX_GOAL_SCAN = 100

# Upper bound on goals actually observed per tick.
_MAX_GOALS_PER_TICK = 20


async def tick_behavior_monitoring(
    sessions: async_sessionmaker[AsyncSession],
    *,
    budget_ledger: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one behavior-monitoring sweep over ACTIVE monitored goals.

    Returns a stats dict: ``{"scanned", "monitored", "observed", "repairs"}``.
    Never raises — per-goal errors are logged and skipped.
    """
    clock = now or datetime.now(UTC)
    stats: dict[str, Any] = {
        "scanned": 0,
        "monitored": 0,
        "observed": 0,
        "repairs": [],
    }

    from regent.application.runtime_behavior_monitor import RuntimeBehaviorMonitor

    monitor = RuntimeBehaviorMonitor()

    try:
        async with sessions() as session:
            goals = list(
                await session.scalars(
                    select(GoalModel)
                    .where(GoalModel.status == "ACTIVE")
                    .order_by(GoalModel.updated_at.desc())
                    .limit(_MAX_GOAL_SCAN)
                )
            )
    except Exception:
        logger.exception("behavior monitor tick: goal scan failed")
        return stats

    stats["scanned"] = len(goals)
    candidates: list[tuple[uuid.UUID, dict[str, Any]]] = []
    for goal in goals:
        meta = dict(goal.metadata_json or {})
        org_mode = meta.get("org_mode") or {}
        if not org_mode.get("enable_monitoring"):
            continue
        preview_url = str(meta.get("behavior_monitor_preview_url") or "")
        if not preview_url.startswith(("http://", "https://")):
            continue
        ran_at = _parse_iso(meta.get("behavior_monitor_ran_at"))
        if ran_at is not None and (clock - ran_at) < timedelta(
            seconds=MONITOR_MIN_INTERVAL_SECONDS
        ):
            continue
        candidates.append((goal.id, meta))

    stats["monitored"] = len(candidates)

    for goal_id, meta in candidates[:_MAX_GOALS_PER_TICK]:
        try:
            await _observe_and_repair(
                sessions,
                monitor,
                goal_id,
                preview_url=str(meta.get("behavior_monitor_preview_url") or ""),
                goal_profile=meta.get("goal_profile") or {},
                enable_repair_loop=bool(
                    (meta.get("org_mode") or {}).get("enable_repair_loop")
                ),
                budget_ledger=budget_ledger,
            )
            stats["observed"] += 1
        except Exception:
            logger.exception(
                "behavior monitor tick: goal observation failed",
                extra={"goal_id": str(goal_id)},
            )

    return stats


async def _observe_and_repair(
    sessions: async_sessionmaker[AsyncSession],
    monitor: Any,
    goal_id: uuid.UUID,
    *,
    preview_url: str,
    goal_profile: dict[str, Any],
    enable_repair_loop: bool,
    budget_ledger: Any | None,
) -> None:
    """Observe one goal and (optionally) run the repair loop for it."""
    observations = await monitor.observe(
        goal_id, preview_url, goal_profile=goal_profile
    )
    obs_dicts = [
        {
            "metric_name": o.metric_name,
            "metric_value": o.metric_value,
            "anomaly": o.anomaly,
            "severity": o.severity,
            "detail": o.detail,
        }
        for o in observations
    ]

    async with sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id)
        if goal is not None:
            meta = dict(goal.metadata_json or {})
            meta["last_behavior_observations"] = obs_dicts[-10:]
            meta["behavior_monitor_ran_at"] = datetime.now(UTC).isoformat()
            goal.metadata_json = meta

    if not enable_repair_loop:
        return

    from regent.application.behavior_repair_loop import BehaviorRepairLoop

    repair = BehaviorRepairLoop()
    decision = await repair.evaluate_and_repair(
        sessions,
        goal_id,
        obs_dicts,
        budget_ledger=budget_ledger,
        retrigger_execution=True,
    )
    if decision.action == "REPAIR":
        logger.info(
            "behavior monitor tick repair: %s (retriggered=%s, %s)",
            decision.reason,
            decision.retriggered,
            decision.retrigger_reason,
            extra={"goal_id": str(goal_id)},
        )


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
