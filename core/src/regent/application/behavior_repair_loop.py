"""Behavior repair loop — connect monitoring observations to agent repair.

When the ``RuntimeBehaviorMonitor`` detects anomalies, this module decides
whether and how to inject repair instructions into the agent's conversation.
The agent then picks up the steering on its next turn and fixes the behavior.

Flow:
    Monitor observes → anomalies detected →
    BehaviorRepairLoop evaluates →
      severity >= MEDIUM → inject steering into conversation →
        re-trigger execution (within budget & max_iterations caps) →
          agent picks up steering on next run → fixes behavior →
            monitor re-observes → confirms fix

Design:
- Only injects steering when anomalies are substantive (MEDIUM+).
- Aggregates multiple observations into a single steering note.
- Respects a cooldown to avoid spamming the agent.
- Stores repair history in goal metadata for audit.
- Re-trigger is bounded: goal must be ACTIVE with no live run, repair
  iteration count must stay under ``org_mode.max_iterations``, and the
  goal budget must not be blocked. Uses the same ``guidance-continue:``
  execution channel as user-initiated resume, so no new event types.

Concurrency hardening (2026-08-23):
- Steering is MERGED, never overwritten: a foreign ``session_steer_brief``
  (user console guidance, QA failures, host guard) is preserved in front of
  the repair note. The loop's own previous note is replaced, not accumulated
  (tracked via ``behavior_repair_own_brief``).
- Guard checks (ACTIVE / iteration cap / live run) execute inside the same
  row-locked transaction as the steering write, so two concurrent repairs
  serialize and the second observes the first's history.
- A retrigger claim (``behavior_repair_retrigger_claim``, TTL-bounded) is
  test-and-set under the same lock, preventing double machine-triggered
  execution when tick and deployment callback race. The claim is cleared
  after ``start()`` returns; a stale claim self-expires via the TTL.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Minimum time between repair injections (avoid agent spam).
REPAIR_COOLDOWN_MINUTES = 15

# Minimum severity to trigger a repair.
MIN_REPAIR_SEVERITY = "MEDIUM"

# Fallback cap when org_mode.max_iterations is absent from goal metadata.
DEFAULT_MAX_REPAIR_ITERATIONS = 3

# Run statuses that indicate a live execution will consume the steering.
_LIVE_RUN_STATUSES = frozenset({"CREATED", "PERMIT_PENDING", "QUEUED", "RUNNING"})

# Cap for the merged steering brief (matches other session_steer_brief writers).
_STEER_BRIEF_MAX = 4000

# A retrigger claim older than this is considered stale and re-claimable.
RETRIGGER_CLAIM_TTL_SECONDS = 300.0

_SEVERITY_ORDER = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


@dataclass(frozen=True, slots=True)
class RepairDecision:
    """Outcome of the repair evaluation."""

    action: str               # REPAIR / WAIT / NO_ACTION
    reason: str
    anomalies_injected: int = 0
    steering_text: str = ""
    cooldown_remaining_seconds: float = 0
    retriggered: bool = False
    retrigger_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "anomalies_injected": self.anomalies_injected,
            "steering_text": self.steering_text[:400] if self.steering_text else "",
            "cooldown_remaining_seconds": self.cooldown_remaining_seconds,
            "retriggered": self.retriggered,
            "retrigger_reason": self.retrigger_reason[:200],
        }


@dataclass(frozen=True, slots=True)
class _InjectOutcome:
    """Result of the locked steering-inject + claim transaction."""

    injected: bool
    retrigger_claimed: bool
    reason: str


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
        *,
        budget_ledger: Any | None = None,
        retrigger_execution: bool = False,
    ) -> RepairDecision:
        """Evaluate observations and decide on repair action.

        Args:
            sessions: DB session factory.
            goal_id: The goal to evaluate.
            observations: List of observation dicts from the monitor.
            budget_ledger: Optional BudgetLedger for budget-limit checks.
            retrigger_execution: When True, attempt to re-queue goal
                execution after injecting steering (bounded by budget,
                ``org_mode.max_iterations`` and live-run checks).

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

        # Inject steering (merged, row-locked) and — when requested —
        # evaluate retrigger guards + claim inside the same transaction.
        outcome = await self._inject_steering_and_claim(
            sessions, goal_id, anomalies, steering,
            want_retrigger=retrigger_execution,
        )

        # Optionally re-trigger execution so the steering is actually
        # consumed instead of waiting for a coincidental future run.
        retriggered = False
        if not retrigger_execution:
            retrigger_reason = "retrigger disabled"
        elif not outcome.retrigger_claimed:
            retrigger_reason = outcome.reason or "retrigger not allowed"
        else:
            retriggered, retrigger_reason = await self._start_execution(
                sessions, goal_id, budget_ledger=budget_ledger
            )
            await self._clear_retrigger_claim(sessions, goal_id)

        return RepairDecision(
            action="REPAIR",
            reason=f"Injected repair steering for {len(anomalies)} anomalies.",
            anomalies_injected=len(anomalies),
            steering_text=steering,
            retriggered=retriggered,
            retrigger_reason=retrigger_reason,
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

    async def _inject_steering_and_claim(
        self,
        sessions: Any,
        goal_id: uuid.UUID,
        anomalies: list[dict[str, Any]],
        steering: str,
        *,
        want_retrigger: bool,
    ) -> _InjectOutcome:
        """Write merged repair steering under the goal row lock.

        All retrigger guards (ACTIVE status, iteration cap, live run) and the
        retrigger claim are evaluated inside this same transaction, so a
        concurrent repair serializes on the row lock and observes the updated
        history/claim instead of racing past the checks.
        """
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from regent.infrastructure.models import GoalModel, RunModel, WorkModel

        async with sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                return _InjectOutcome(False, False, "goal not found")
            meta = dict(goal.metadata_json or {})

            # --- Merge steering: preserve foreign (user/system) briefs.
            existing = str(meta.get("session_steer_brief") or "")
            own_previous = str(meta.get("behavior_repair_own_brief") or "")
            if own_previous and own_previous in existing:
                # Drop our previous note before merging; whatever remains
                # is foreign steering (user guidance / QA / host guard).
                existing = existing.replace(own_previous, "").strip("\n")
            # Foreign steering present — keep it FIRST, append ours.
            merged = (
                existing + "\n\n" + steering if existing else steering
            )
            meta["session_steer_brief"] = merged[:_STEER_BRIEF_MAX]
            meta["behavior_repair_own_brief"] = steering[:_STEER_BRIEF_MAX]

            # --- Record repair history (pre-append length feeds the cap).
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

            # --- Retrigger guards, evaluated under the row lock.
            claim_reason = ""
            claim = False
            if want_retrigger:
                if goal.status != "ACTIVE":
                    claim_reason = (
                        f"goal status {goal.status} not retriggerable"
                    )
                else:
                    org_mode = meta.get("org_mode") or {}
                    try:
                        max_iterations = int(
                            org_mode.get("max_iterations")
                            or DEFAULT_MAX_REPAIR_ITERATIONS
                        )
                    except (TypeError, ValueError):
                        max_iterations = DEFAULT_MAX_REPAIR_ITERATIONS
                    if len(history) - 1 >= max_iterations:
                        claim_reason = (
                            f"repair iteration cap reached "
                            f"({len(history) - 1}/{max_iterations})"
                        )
                    else:
                        run = await session.scalar(
                            select(RunModel)
                            .join(WorkModel, RunModel.work_id == WorkModel.id)
                            .where(WorkModel.goal_id == goal_id)
                            .order_by(RunModel.created_at.desc())
                            .limit(1)
                        )
                        if run is not None and run.status in _LIVE_RUN_STATUSES:
                            claim_reason = (
                                f"run {run.id} still {run.status}; steering "
                                "will be consumed by the live run"
                            )
                        else:
                            held = self._claim_age_seconds(
                                meta.get("behavior_repair_retrigger_claim")
                            )
                            if held is not None and (
                                held < RETRIGGER_CLAIM_TTL_SECONDS
                            ):
                                claim_reason = (
                                    "retrigger already in flight (claim held)"
                                )
                            else:
                                claim = True
                                meta["behavior_repair_retrigger_claim"] = {
                                    "at": datetime.now(UTC).isoformat(),
                                    "actor": "regent-behavior-repair",
                                }

            goal.metadata_json = meta
            flag_modified(goal, "metadata_json")

        logger.info(
            "behavior repair steering injected",
            extra={
                "goal_id": str(goal_id),
                "anomaly_count": len(anomalies),
                "steering_length": len(steering),
                "retrigger_claimed": claim,
            },
        )
        return _InjectOutcome(True, claim, claim_reason)

    @staticmethod
    def _claim_age_seconds(claim: Any) -> float | None:
        """Age of a retrigger claim in seconds; None when absent/invalid."""
        if not isinstance(claim, dict) or not claim.get("at"):
            return None
        try:
            at = datetime.fromisoformat(str(claim["at"]))
        except (TypeError, ValueError):
            return None
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        return (datetime.now(UTC) - at).total_seconds()

    async def _start_execution(
        self,
        sessions: Any,
        goal_id: uuid.UUID,
        *,
        budget_ledger: Any | None = None,
    ) -> tuple[bool, str]:
        """Re-queue goal execution so the injected steering is consumed.

        Called only after the locked transaction claimed the retrigger, so
        guards (ACTIVE / cap / live run / claim) have already passed. Budget
        is re-checked here (best-effort) and again by ``start()`` itself.
        Uses ``GoalExecutionService.start`` with a ``guidance-continue:``
        idempotency key — the same channel as user-initiated resume, so
        start-time policy checks (budget_limit, boundary lock, spec lock)
        still apply.
        """
        if budget_ledger is not None:
            try:
                budget_status = await budget_ledger.check_budget_limit(goal_id)
                if budget_status.is_blocked:
                    return False, "goal budget blocked"
            except Exception:
                # Budget check is best-effort; start() re-checks limits.
                logger.warning(
                    "budget check failed before repair retrigger",
                    extra={"goal_id": str(goal_id)},
                    exc_info=True,
                )

        from regent.application.goal_execution_service import GoalExecutionService
        from regent.domain.errors import DomainError

        try:
            receipt = await GoalExecutionService(sessions).start(
                goal_id,
                actor="regent-behavior-repair",
                idempotency_key=(
                    f"guidance-continue:behavior-repair:{goal_id}:{uuid.uuid4()}"
                ),
            )
            logger.info(
                "behavior repair retriggered execution",
                extra={
                    "goal_id": str(goal_id),
                    "stage": receipt.stage,
                    "event_id": str(receipt.event_id)
                    if receipt.event_id
                    else None,
                },
            )
            return True, f"execution re-queued (stage={receipt.stage})"
        except DomainError as exc:
            return False, f"retrigger rejected: {exc.message}"
        except Exception as exc:
            return False, f"retrigger failed: {type(exc).__name__}: {str(exc)[:120]}"

    async def _clear_retrigger_claim(
        self, sessions: Any, goal_id: uuid.UUID
    ) -> None:
        """Release the retrigger claim after ``start()`` returned.

        A crashed call leaves the claim behind; it self-expires via the TTL,
        but clearing eagerly keeps the next repair unblocked.
        """
        from sqlalchemy.orm.attributes import flag_modified

        from regent.infrastructure.models import GoalModel

        try:
            async with sessions() as session, session.begin():
                goal = await session.get(GoalModel, goal_id, with_for_update=True)
                if goal is None:
                    return
                meta = dict(goal.metadata_json or {})
                if meta.pop("behavior_repair_retrigger_claim", None) is not None:
                    goal.metadata_json = meta
                    flag_modified(goal, "metadata_json")
        except Exception:
            logger.warning(
                "failed to clear behavior repair retrigger claim",
                extra={"goal_id": str(goal_id)},
                exc_info=True,
            )
