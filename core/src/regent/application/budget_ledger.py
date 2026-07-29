"""BudgetLedger — real-time cost accounting for Goal/Run execution.

Tracks model token costs, tool invocations, and infrastructure spend
per Goal and Run.  Supports budget-limit checks that transition Goals
into BLOCKED when spend exceeds the configured ceiling.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.infrastructure.models import BudgetEntryModel, GoalModel

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetReport:
    goal_id: uuid.UUID
    total_cost: float
    entries: list[dict[str, Any]] = field(default_factory=list)
    limit: float | None = None

    @property
    def within_limit(self) -> bool:
        if self.limit is None:
            return True
        return self.total_cost <= self.limit

    @property
    def remaining(self) -> float | None:
        if self.limit is None:
            return None
        return max(0.0, self.limit - self.total_cost)


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    goal_id: uuid.UUID
    total_cost: float
    limit: float | None
    is_blocked: bool

    @property
    def remaining(self) -> float | None:
        if self.limit is None:
            return None
        return max(0.0, self.limit - self.total_cost)


# ---------------------------------------------------------------------------
# Cost types
# ---------------------------------------------------------------------------

COST_MODEL_INPUT = "model_input_tokens"
COST_MODEL_OUTPUT = "model_output_tokens"
COST_TOOL_INVOCATION = "tool_invocation"
COST_INFRASTRUCTURE = "infrastructure"
COST_EXTERNAL_OPERATION = "external_operation"

_ALL_COST_TYPES = frozenset({
    COST_MODEL_INPUT,
    COST_MODEL_OUTPUT,
    COST_TOOL_INVOCATION,
    COST_INFRASTRUCTURE,
    COST_EXTERNAL_OPERATION,
})


# ---------------------------------------------------------------------------
# BudgetLedger service
# ---------------------------------------------------------------------------


class BudgetLedger:
    """Real-time cost ledger — tracks per-Goal/Run model/tool/infra spend."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions

    async def record_cost(
        self,
        goal_id: uuid.UUID,
        run_id: uuid.UUID | None,
        *,
        cost_type: str,
        amount: float,
        price_book_version: str = "price-book-v1",
        description: str = "",
    ) -> BudgetEntryModel:
        if cost_type not in _ALL_COST_TYPES:
            raise ValueError(f"unknown cost type: {cost_type}")
        if amount < 0:
            raise ValueError("cost amount must be non-negative")
        async with self._sessions() as session, session.begin():
            entry = BudgetEntryModel(
                id=uuid.uuid4(),
                goal_id=goal_id,
                run_id=run_id,
                cost_type=cost_type,
                amount=amount,
                price_book_version=price_book_version,
                description=description,
                recorded_at=datetime.now(UTC),
            )
            session.add(entry)
            return entry

    async def get_goal_budget(self, goal_id: uuid.UUID) -> BudgetReport:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(BudgetEntryModel)
                .where(BudgetEntryModel.goal_id == goal_id)
                .order_by(BudgetEntryModel.recorded_at)
            )
            entries = [
                {
                    "id": str(e.id),
                    "run_id": str(e.run_id) if e.run_id else None,
                    "cost_type": e.cost_type,
                    "amount": e.amount,
                    "price_book_version": e.price_book_version,
                    "recorded_at": e.recorded_at.isoformat() if e.recorded_at else None,
                    "description": e.description,
                }
                for e in rows.all()
            ]
            total = sum(e["amount"] for e in entries)
            goal = await session.get(GoalModel, goal_id)
            limit = None
            if goal and goal.metadata_json:
                limit = goal.metadata_json.get("budget_limit")
            return BudgetReport(
                goal_id=goal_id,
                total_cost=total,
                entries=entries,
                limit=float(limit) if limit is not None else None,
            )

    async def check_budget_limit(self, goal_id: uuid.UUID) -> BudgetStatus:
        report = await self.get_goal_budget(goal_id)
        is_blocked = not report.within_limit
        if is_blocked:
            async with self._sessions() as session, session.begin():
                goal = await session.get(GoalModel, goal_id)
                if goal and goal.status == "ACTIVE":
                    goal.status = "BLOCKED"
                    goal.metadata_json = {
                        **(goal.metadata_json or {}),
                        "blocked_reason": "budget_limit_exceeded",
                        "blocked_at": datetime.now(UTC).isoformat(),
                    }
        return BudgetStatus(
            goal_id=goal_id,
            total_cost=report.total_cost,
            limit=report.limit,
            is_blocked=is_blocked,
        )

    async def get_org_budget(self, org_id: uuid.UUID) -> BudgetReport:
        async with self._sessions() as session:
            goals = await session.scalars(
                select(GoalModel).where(GoalModel.organization_id == org_id)
            )
            goal_ids = [g.id for g in goals.all()]
            if not goal_ids:
                return BudgetReport(goal_id=org_id, total_cost=0.0, entries=[])
            rows = await session.scalars(
                select(BudgetEntryModel).where(
                    BudgetEntryModel.goal_id.in_(goal_ids)
                )
            )
            entries = [
                {
                    "id": str(e.id),
                    "goal_id": str(e.goal_id),
                    "cost_type": e.cost_type,
                    "amount": e.amount,
                }
                for e in rows.all()
            ]
            total = sum(e["amount"] for e in entries)
            return BudgetReport(goal_id=org_id, total_cost=total, entries=entries)
