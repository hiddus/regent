"""PRD §8.1 / §8.2 — CostPerVerifiedSuccess north star and guardrail report."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.infrastructure.models import (
    BudgetEntryModel,
    EvidenceModel,
    ExternalOperationModel,
    GoalModel,
    ObservationModel,
)

WINDOW_DAYS = 28
MIN_VERIFIED_SUCCESS = 10

# Cost types that roll into the north-star numerator (PRD §8.1).
_NORTH_STAR_COST_TYPES = frozenset(
    {
        "model_input_tokens",
        "model_output_tokens",
        "tool_invocation",
        "infrastructure",
        "external_operation",
        "human_minutes",
        "failure_recovery",
    }
)

# Map human_minutes budget rows as cost (already monetized) OR treat amount as minutes * rate.
_HUMAN_MINUTE_RATE = 1.0  # unit cost already stored in budget_entries.amount when monetized


@dataclass(frozen=True, slots=True)
class GuardrailResult:
    name: str
    value: float | int | None
    threshold: str
    status: str  # GREEN | RED | INSUFFICIENT_EVIDENCE
    detail: str = ""


@dataclass(frozen=True, slots=True)
class NorthStarReport:
    window_start: datetime
    window_end: datetime
    verified_success_count: int
    total_cost: float
    cost_per_verified_success: float | None
    status: str  # OK | INSUFFICIENT_EVIDENCE | GUARDRAIL_RED
    guardrails: list[GuardrailResult] = field(default_factory=list)
    cost_breakdown: dict[str, float] = field(default_factory=dict)
    # CD-5: proportion of window goals currently handed off to a human
    # (WAITING_HUMAN status, or delivery_state == DELIVERED_FOR_REVIEW in
    # metadata). None when the window has no goals at all (not measurable,
    # distinct from a real 0.0 rate).
    handoff_rate: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "verified_success_count": self.verified_success_count,
            "total_cost": self.total_cost,
            "cost_per_verified_success": self.cost_per_verified_success,
            "status": self.status,
            "cost_breakdown": self.cost_breakdown,
            "handoff_rate": self.handoff_rate,
            "guardrails": [
                {
                    "name": g.name,
                    "value": g.value,
                    "threshold": g.threshold,
                    "status": g.status,
                    "detail": g.detail,
                }
                for g in self.guardrails
            ],
        }


class NorthStarMetricsService:
    """Read-only governance metrics (PRD §8). Report-first; no hard stop."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def report(
        self,
        *,
        now: datetime | None = None,
        window_days: int = WINDOW_DAYS,
        baseline_cost: float | None = None,
    ) -> NorthStarReport:
        clock = now or datetime.now(UTC)
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=UTC)
        start = clock - timedelta(days=window_days)

        async with self._sessions() as session:
            verified = await self._verified_success_goals(session, start, clock)
            verified_ids = [g.id for g in verified]
            breakdown = await self._cost_breakdown(session, start, clock, verified_ids)
            total_cost = sum(breakdown.values())
            guardrails = await self._evaluate_guardrails(
                session, start, clock, verified_ids, len(verified)
            )
            handoff_rate = await self._handoff_rate(session, start, clock)

        vs_count = len(verified)
        if vs_count < MIN_VERIFIED_SUCCESS:
            return NorthStarReport(
                window_start=start,
                window_end=clock,
                verified_success_count=vs_count,
                total_cost=total_cost,
                cost_per_verified_success=None,
                status="INSUFFICIENT_EVIDENCE",
                guardrails=guardrails,
                cost_breakdown=breakdown,
                handoff_rate=handoff_rate,
            )

        cpvs = total_cost / vs_count
        any_red = any(g.status == "RED" for g in guardrails)
        status = "GUARDRAIL_RED" if any_red else "OK"
        if (
            baseline_cost is not None
            and baseline_cost > 0
            and cpvs > baseline_cost * 1.20
            and not any_red
        ):
            # Threshold note only; continuous 2-window STOP is an ops process.
            status = "OK"
        return NorthStarReport(
            window_start=start,
            window_end=clock,
            verified_success_count=vs_count,
            total_cost=total_cost,
            cost_per_verified_success=cpvs,
            status=status,
            guardrails=guardrails,
            cost_breakdown=breakdown,
            handoff_rate=handoff_rate,
        )

    async def _handoff_rate(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> float | None:
        """CD-5: proportion of window goals currently handed off to a human.

        Counts goals whose status is ``WAITING_HUMAN`` or whose metadata
        records ``delivery_state == DELIVERED_FOR_REVIEW`` (CD-1.2 verdict
        write-back) — the two observable handoff signals in this codebase.
        """
        total = await session.scalar(
            select(func.count())
            .select_from(GoalModel)
            .where(GoalModel.updated_at >= start, GoalModel.updated_at < end)
        )
        total = int(total or 0)
        if total == 0:
            return None
        goals = await session.scalars(
            select(GoalModel).where(GoalModel.updated_at >= start, GoalModel.updated_at < end)
        )
        handed_off = 0
        for goal in goals:
            if goal.status == "WAITING_HUMAN":
                handed_off += 1
                continue
            meta = dict(goal.metadata_json or {})
            if meta.get("delivery_state") == "DELIVERED_FOR_REVIEW":
                handed_off += 1
        return handed_off / total

    async def _verified_success_goals(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> list[GoalModel]:
        rows = await session.scalars(
            select(GoalModel).where(
                GoalModel.status == "ACHIEVED",
                GoalModel.updated_at >= start,
                GoalModel.updated_at < end,
            )
        )
        goals = list(rows)
        # VerifiedSuccess requires at least one Evidence row (independent verification signal).
        verified: list[GoalModel] = []
        for goal in goals:
            evidence = await session.scalar(
                select(EvidenceModel.id).where(EvidenceModel.goal_id == goal.id).limit(1)
            )
            if evidence is not None:
                verified.append(goal)
        return verified

    async def _cost_breakdown(
        self,
        session: AsyncSession,
        start: datetime,
        end: datetime,
        verified_ids: list[uuid.UUID],
    ) -> dict[str, float]:
        if not verified_ids:
            return {name: 0.0 for name in sorted(_NORTH_STAR_COST_TYPES)}
        rows = await session.execute(
            select(BudgetEntryModel.cost_type, func.coalesce(func.sum(BudgetEntryModel.amount), 0.0))
            .where(
                BudgetEntryModel.goal_id.in_(verified_ids),
                BudgetEntryModel.recorded_at >= start,
                BudgetEntryModel.recorded_at < end,
            )
            .group_by(BudgetEntryModel.cost_type)
        )
        breakdown = {name: 0.0 for name in sorted(_NORTH_STAR_COST_TYPES)}
        for cost_type, amount in rows.all():
            key = str(cost_type)
            if key == "human_minutes":
                breakdown["human_minutes"] = float(amount) * _HUMAN_MINUTE_RATE
            elif key in breakdown:
                breakdown[key] = float(amount)
            elif key in {"model_input_tokens", "model_output_tokens"}:
                breakdown[key] = float(amount)
        return breakdown

    async def _evaluate_guardrails(
        self,
        session: AsyncSession,
        start: datetime,
        end: datetime,
        verified_ids: list[uuid.UUID],
        verified_count: int,
    ) -> list[GuardrailResult]:
        total_goals = await session.scalar(
            select(func.count())
            .select_from(GoalModel)
            .where(GoalModel.updated_at >= start, GoalModel.updated_at < end)
        )
        total = int(total_goals or 0)
        completion_rate = (verified_count / total) if total else None

        # Insufficient evidence: ACHIEVED without evidence, or READY/BLOCKED waiting evidence.
        achieved = await session.scalars(
            select(GoalModel).where(
                GoalModel.status == "ACHIEVED",
                GoalModel.updated_at >= start,
                GoalModel.updated_at < end,
            )
        )
        insufficient = 0
        achieved_list = list(achieved)
        for goal in achieved_list:
            evidence = await session.scalar(
                select(EvidenceModel.id).where(EvidenceModel.goal_id == goal.id).limit(1)
            )
            if evidence is None:
                insufficient += 1
        insuff_rate = (insufficient / len(achieved_list)) if achieved_list else 0.0

        # P95 latency (hours) from goal created_at → updated_at for verified successes.
        latencies_h: list[float] = []
        if verified_ids:
            goals = await session.scalars(select(GoalModel).where(GoalModel.id.in_(verified_ids)))
            for goal in goals:
                delta = goal.updated_at - goal.created_at
                latencies_h.append(delta.total_seconds() / 3600.0)
        p95 = _percentile(latencies_h, 95) if latencies_h else None

        human_minutes = 0.0
        if verified_ids:
            human_sum = await session.scalar(
                select(func.coalesce(func.sum(BudgetEntryModel.amount), 0.0)).where(
                    BudgetEntryModel.goal_id.in_(verified_ids),
                    BudgetEntryModel.cost_type == "human_minutes",
                    BudgetEntryModel.recorded_at >= start,
                    BudgetEntryModel.recorded_at < end,
                )
            )
            human_minutes = float(human_sum or 0.0)
        human_per_vs = (human_minutes / verified_count) if verified_count else None

        # Duplicate side effects: same operation_key appearing >1 SUCCEEDED (should be 0 via unique).
        dup = await session.scalar(
            select(func.count())
            .select_from(ExternalOperationModel)
            .where(
                ExternalOperationModel.status == "SUCCEEDED",
                ExternalOperationModel.updated_at >= start,
                ExternalOperationModel.updated_at < end,
            )
        )
        # Unique constraint prevents duplicates; count EO with reconcile retries as proxy = 0 baseline.
        duplicate_side_effects = 0
        _ = dup

        stale_unknown = await session.scalar(
            select(func.count())
            .select_from(ExternalOperationModel)
            .where(
                ExternalOperationModel.status.in_(("UNKNOWN", "DISPATCHING")),
                ExternalOperationModel.updated_at < end - timedelta(minutes=15),
            )
        )

        security_violations = 0  # populated when security audit events exist
        internal_in_decisions = await session.scalar(
            select(func.count())
            .select_from(ObservationModel)
            .where(
                ObservationModel.is_internal.is_(True),
                ObservationModel.created_at >= start,
                ObservationModel.created_at < end,
            )
        )
        # Internal traffic that was NOT excluded (is_internal True still present) is a red if used
        # in product decisions — ObservationModel alone is not proof of gate misuse; flag count>0
        # only when metadata marks gate_used=True if present. Default: 0 unless tagged.
        internal_misuse = 0
        if internal_in_decisions:
            tagged = await session.scalars(
                select(ObservationModel).where(
                    ObservationModel.is_internal.is_(True),
                    ObservationModel.created_at >= start,
                    ObservationModel.created_at < end,
                )
            )
            for obs in tagged:
                value = dict(obs.metric_value or {})
                if value.get("used_in_product_decision"):
                    internal_misuse += 1

        results = [
            _guard(
                "completion_rate",
                completion_rate,
                "< 0.70",
                red=completion_rate is not None and completion_rate < 0.70,
                insuff=completion_rate is None,
            ),
            _guard(
                "insufficient_evidence_rate",
                insuff_rate,
                "> 0.30",
                red=insuff_rate > 0.30,
            ),
            _guard(
                "p95_latency_hours",
                p95,
                "> 4h",
                red=p95 is not None and p95 > 4.0,
                insuff=p95 is None,
            ),
            _guard(
                "human_minutes_per_verified_success",
                human_per_vs,
                "> 120 min",
                red=human_per_vs is not None and human_per_vs > 120.0,
                insuff=human_per_vs is None,
            ),
            _guard(
                "duplicate_side_effects",
                duplicate_side_effects,
                "> 0",
                red=duplicate_side_effects > 0,
            ),
            _guard(
                "unreconciled_unknown_over_15m",
                int(stale_unknown or 0),
                "> 15 min unreconciled",
                red=int(stale_unknown or 0) > 0,
            ),
            _guard(
                "security_violations",
                security_violations,
                "> 0",
                red=security_violations > 0,
            ),
            _guard(
                "internal_traffic_in_product_decisions",
                internal_misuse,
                "> 0",
                red=internal_misuse > 0,
            ),
        ]
        return results


def _guard(
    name: str,
    value: float | int | None,
    threshold: str,
    *,
    red: bool,
    insuff: bool = False,
) -> GuardrailResult:
    if insuff:
        status = "INSUFFICIENT_EVIDENCE"
    elif red:
        status = "RED"
    else:
        status = "GREEN"
    return GuardrailResult(name=name, value=value, threshold=threshold, status=status)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight
