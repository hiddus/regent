"""Behavior repair loop — connect monitoring observations to agent repair.

When the ``RuntimeBehaviorMonitor`` detects anomalies, this module decides
whether and how to inject repair instructions into the agent's conversation.
The agent then picks up the steering on its next turn and fixes the behavior.

Flow:
    Monitor observes → anomalies detected →
    BehaviorRepairLoop evaluates →
      severity >= MEDIUM → inject steering into conversation →
        agent picks up steering on next turn → fixes behavior →
          monitor re-observes → confirms fix

Design:
- Only injects steering when anomalies are substantive (MEDIUM+).
- Aggregates multiple observations into a single steering note.
- Respects a cooldown to avoid spamming the agent.
- Stores repair history in goal metadata for audit.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Minimum time between repair injections (avoid agent spam).
REPAIR_COOLDOWN_MINUTES = 15

# Minimum severity to trigger a repair.
MIN_REPAIR_SEVERITY = "MEDIUM"

_SEVERITY_ORDER = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


@dataclass(frozen=True, slots=True)
class RepairDecision:
    """Outcome of the repair evaluation."""

    action: str               # REPAIR / WAIT / NO_ACTION
    reason: str
    anomalies_injected: int = 0
    steering_text: str = ""
    cooldown_remaining_seconds: float = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "anomalies_injected": self.anomalies_injected,
            "steering_text": self.steering_text[:400] if self.steering_text else "",
            "cooldown_remaining_seconds": self.cooldown_remaining_seconds,
        }


class BehaviorRepairLoop:
    """Evaluate monitoring observations and inject repair steering.

    Usage:
        repair = BehaviorRepairLoop()
        decision = await repair.evaluate_and_repair(
            sessions, goal_id, observations
        )
    """

    async def evaluate_and_repair(
        self,
        sessions: Any,
        goal_id: uuid.UUID,
        observations: list[dict[str, Any]],
    ) -> RepairDecision:
        """Evaluate observations and decide on repair action.

        Args:
            sessions: DB session factory.
            goal_id: The goal to evaluate.
            observations: List of observation dicts from the monitor.

        Returns:
            RepairDecision with the chosen action.
        """
        # Filter to actionable anomalies.
        anomalies = [
            o for o in observations
            if o.get("anomaly")
            and _SEVERITY_ORDER.get(o.get("severity", "NONE"), 0)
            >= _SEVERITY_ORDER[MIN_REPAIR_SEVERITY]
        ]

        if not anomalies:
            return RepairDecision(
                action="NO_ACTION",
                reason="No actionable anomalies detected.",
            )

        # Check cooldown.
        cooldown_remaining = await self._check_cooldown(sessions, goal_id)
        if cooldown_remaining > 0:
            return RepairDecision(
                action="WAIT",
                reason=f"Repair cooldown active ({cooldown_remaining:.0f}s remaining).",
                cooldown_remaining_seconds=cooldown_remaining,
            )

        # Build steering text.
        steering = self._build_steering_text(anomalies)

        # Inject steering into goal metadata.
        await self._inject_repair_steering(sessions, goal_id, anomalies, steering)

        return RepairDecision(
            action="REPAIR",
            reason=f"Injected repair steering for {len(anomalies)} anomalies.",
            anomalies_injected=len(anomalies),
            steering_text=steering,
        )

    def _build_steering_text(self, anomalies: list[dict[str, Any]]) -> str:
        """Build a concise steering note from anomalies."""
        lines = [
            "【运行时行为监控 — 自动修复指令】",
            "以下问题被独立监控发现，请在下一轮修改中修复：",
        ]
        for anomaly in anomalies[:6]:  # Cap at 6 to avoid overwhelming the agent.
            metric = anomaly.get("metric_name", "unknown")
            detail = anomaly.get("detail", "")[:200]
            severity = anomaly.get("severity", "MEDIUM")
            lines.append(f"- [{severity}] {metric}: {detail}")
        lines.append(
            "修复后不要重复提交，等下一轮监控确认后再继续。"
        )
        return "\n".join(lines)[:2000]

    async def _check_cooldown(
        self, sessions: Any, goal_id: uuid.UUID
    ) -> float:
        """Check if we're still in cooldown from the last repair."""
        from sqlalchemy import select

        from regent.infrastructure.models import GoalModel

        async with sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                return 0
            meta = dict(goal.metadata_json or {})
            repair_history = meta.get("behavior_repair_history") or []
            if not repair_history:
                return 0
            last_repair = repair_history[-1]
            last_at_str = last_repair.get("repaired_at")
            if not last_at_str:
                return 0
            try:
                last_at = datetime.fromisoformat(last_at_str)
                elapsed = (datetime.now(UTC) - last_at).total_seconds()
                cooldown = REPAIR_COOLDOWN_MINUTES * 60
                remaining = max(0, cooldown - elapsed)
                return remaining
            except (ValueError, TypeError):
                return 0

    async def _inject_repair_steering(
        self,
        sessions: Any,
        goal_id: uuid.UUID,
        anomalies: list[dict[str, Any]],
        steering: str,
    ) -> None:
        """Write repair steering into goal metadata for the agent to pick up."""
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from regent.infrastructure.models import GoalModel

        async with sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                return
            meta = dict(goal.metadata_json or {})

            # Write steering note (agent reads this on next turn).
            meta["session_steer_brief"] = steering

            # Record repair history.
            history = list(meta.get("behavior_repair_history") or [])
            history.append({
                "repaired_at": datetime.now(UTC).isoformat(),
                "anomaly_count": len(anomalies),
                "anomaly_metrics": [
                    a.get("metric_name", "?") for a in anomalies[:8]
                ],
                "steering_length": len(steering),
            })
            # Keep last 20 repairs only.
            meta["behavior_repair_history"] = history[-20:]

            # Store latest observations for audit.
            meta["behavior_latest_observations"] = anomalies[:12]

            goal.metadata_json = meta
            flag_modified(goal, "metadata_json")

        logger.info(
            "behavior repair steering injected",
            extra={
                "goal_id": str(goal_id),
                "anomaly_count": len(anomalies),
                "steering_length": len(steering),
            },
        )
