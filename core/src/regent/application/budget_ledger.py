"""BudgetLedger — real-time cost accounting for Goal/Run execution.

Tracks model token costs, tool invocations, and infrastructure spend
per Goal and Run.  Supports budget-limit checks that transition Goals
into BLOCKED when spend exceeds the configured ceiling.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.infrastructure.models import (
    BudgetAccountModel,
    BudgetEntryModel,
    BudgetReservationModel,
    GoalModel,
    RunModel,
)

LOGGER = logging.getLogger(__name__)


class BudgetExceededError(RuntimeError):
    """The requested hold cannot fit inside the Goal's hard budget."""


class BudgetReservationError(RuntimeError):
    """A reservation transition or ownership check failed."""


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


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    id: uuid.UUID
    reservation_key: str
    goal_id: uuid.UUID
    run_id: uuid.UUID | None
    amount: float
    settled_amount: float
    status: str
    claim_token: uuid.UUID | None


# ---------------------------------------------------------------------------
# Cost types
# ---------------------------------------------------------------------------

COST_MODEL_INPUT = "model_input_tokens"
COST_MODEL_OUTPUT = "model_output_tokens"
COST_TOOL_INVOCATION = "tool_invocation"
COST_INFRASTRUCTURE = "infrastructure"
COST_EXTERNAL_OPERATION = "external_operation"
COST_HUMAN_MINUTES = "human_minutes"
COST_FAILURE_RECOVERY = "failure_recovery"

_ALL_COST_TYPES = frozenset(
    {
        COST_MODEL_INPUT,
        COST_MODEL_OUTPUT,
        COST_TOOL_INVOCATION,
        COST_INFRASTRUCTURE,
        COST_EXTERNAL_OPERATION,
        COST_HUMAN_MINUTES,
        COST_FAILURE_RECOVERY,
    }
)


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

    async def reserve(
        self,
        goal_id: uuid.UUID,
        run_id: uuid.UUID | None,
        *,
        reservation_key: str,
        cost_type: str,
        amount: float,
        price_book_version: str = "price-book-v1",
        description: str = "",
    ) -> BudgetReservation:
        """Atomically hold worst-case spend before billable work starts.

        Locking the Goal serializes all reservations for that Goal in PostgreSQL.
        The unique reservation key makes retries idempotent.
        """
        self._validate_cost(cost_type, amount)
        if not reservation_key.strip():
            raise ValueError("reservation_key must not be empty")
        async with self._sessions() as session, session.begin():
            reservation_key, live = await self._resolve_live_key(
                session,
                reservation_key,
                goal_id=goal_id,
                run_id=run_id,
                cost_type=cost_type,
                amount=amount,
            )
            if live is not None:
                return live

            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                raise BudgetReservationError("goal not found")
            limit_raw = (goal.metadata_json or {}).get("budget_limit")
            limit = float(limit_raw) if limit_raw is not None else None
            account = await session.get(BudgetAccountModel, goal_id)
            if account is None:
                spent = float(
                    await session.scalar(
                        select(func.coalesce(func.sum(BudgetEntryModel.amount), 0.0)).where(
                            BudgetEntryModel.goal_id == goal_id
                        )
                    )
                    or 0.0
                )
                account = BudgetAccountModel(
                    goal_id=goal_id,
                    spent_amount=spent,
                    reserved_amount=0.0,
                    updated_at=datetime.now(UTC),
                )
                session.add(account)
                await session.flush()
            held = float(account.reserved_amount)
            spent = float(account.spent_amount)
            if limit is not None:
                result = await session.execute(
                    update(BudgetAccountModel)
                    .where(
                        BudgetAccountModel.goal_id == goal_id,
                        BudgetAccountModel.spent_amount
                        + BudgetAccountModel.reserved_amount
                        + amount
                        <= limit,
                    )
                    .values(
                        reserved_amount=BudgetAccountModel.reserved_amount + amount,
                        updated_at=datetime.now(UTC),
                    )
                )
                admitted = getattr(result, "rowcount", 0) == 1
            else:
                await session.execute(
                    update(BudgetAccountModel)
                    .where(BudgetAccountModel.goal_id == goal_id)
                    .values(
                        reserved_amount=BudgetAccountModel.reserved_amount + amount,
                        updated_at=datetime.now(UTC),
                    )
                )
                admitted = True
            if not admitted:
                raise BudgetExceededError(
                    f"budget reservation exceeds limit: spent={spent}, held={held}, "
                    f"requested={amount}, limit={limit}"
                )
            row = BudgetReservationModel(
                id=uuid.uuid4(),
                reservation_key=reservation_key,
                goal_id=goal_id,
                run_id=run_id,
                cost_type=cost_type,
                amount=amount,
                settled_amount=0.0,
                status="RESERVED",
                claim_token=None,
                price_book_version=price_book_version,
                description=description,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(row)
            await session.flush()
            return self._reservation(row)

    async def _resolve_live_key(
        self,
        session: AsyncSession,
        reservation_key: str,
        *,
        goal_id: uuid.UUID,
        run_id: uuid.UUID | None,
        cost_type: str,
        amount: float,
    ) -> tuple[str, BudgetReservation | None]:
        """Return ``(key, live_reservation)`` where the key's row is live.

        Retries are idempotent while a reservation is RESERVED/CLAIMED (the
        live row is returned). Once the row is terminal (SETTLED/RELEASED)
        the key is consumed — a resumed epoch re-reserving the same turn
        derives a fresh key instead of deadlocking on
        `cannot claim reservation from SETTLED`.

        Terminal rows accumulate across epochs (every resumed epoch reuses
        the same per-turn keys), so the next free suffix is computed from
        all existing rows in one query. Bounded probing previously raised
        `reservation key namespace exhausted` after ~16 epochs and blocked
        all further generation for the goal.
        """
        rows = (
            await session.scalars(
                select(BudgetReservationModel).where(
                    or_(
                        BudgetReservationModel.reservation_key == reservation_key,
                        BudgetReservationModel.reservation_key.like(
                            f"{reservation_key}#r%"
                        ),
                    )
                )
            )
        ).all()
        live = [r for r in rows if r.status in ("RESERVED", "CLAIMED")]
        if live:
            existing = live[-1]
            if (
                existing.goal_id != goal_id
                or existing.run_id != run_id
                or existing.cost_type != cost_type
                or existing.amount != amount
            ):
                raise BudgetReservationError("reservation key reused with different binding")
            return existing.reservation_key, self._reservation(existing)
        base_used = False
        used_suffixes: set[int] = set()
        for row in rows:
            if row.reservation_key == reservation_key:
                base_used = True
                continue
            suffix = row.reservation_key.rsplit("#r", 1)[-1]
            if suffix.isdigit():
                used_suffixes.add(int(suffix))
        if not base_used:
            return reservation_key, None
        attempt = 2
        while attempt in used_suffixes:
            attempt += 1
        return f"{reservation_key}#r{attempt}", None

    async def claim(self, reservation_id: uuid.UUID) -> BudgetReservation:
        async with self._sessions() as session, session.begin():
            row = await self._locked_reservation(session, reservation_id)
            if row.status == "CLAIMED":
                return self._reservation(row)
            if row.status != "RESERVED":
                raise BudgetReservationError(f"cannot claim reservation from {row.status}")
            row.status = "CLAIMED"
            row.claim_token = uuid.uuid4()
            row.updated_at = datetime.now(UTC)
            await session.flush()
            return self._reservation(row)

    async def settle(
        self, reservation_id: uuid.UUID, *, claim_token: uuid.UUID, actual_amount: float
    ) -> BudgetReservation:
        if actual_amount < 0:
            raise ValueError("actual_amount must be non-negative")
        async with self._sessions() as session, session.begin():
            row = await self._locked_reservation(session, reservation_id)
            self._require_claim(row, claim_token)
            if actual_amount > row.amount:
                raise BudgetExceededError("actual cost exceeds reserved hard budget")
            session.add(
                BudgetEntryModel(
                    id=uuid.uuid4(),
                    goal_id=row.goal_id,
                    run_id=await self._existing_run_id(session, row.run_id),
                    cost_type=row.cost_type,
                    amount=actual_amount,
                    price_book_version=row.price_book_version,
                    description=row.description,
                    recorded_at=datetime.now(UTC),
                )
            )
            await session.execute(
                update(BudgetAccountModel)
                .where(
                    BudgetAccountModel.goal_id == row.goal_id,
                    BudgetAccountModel.reserved_amount >= row.amount,
                )
                .values(
                    reserved_amount=BudgetAccountModel.reserved_amount - row.amount,
                    spent_amount=BudgetAccountModel.spent_amount + actual_amount,
                    updated_at=datetime.now(UTC),
                )
            )
            row.settled_amount = actual_amount
            row.status = "SETTLED"
            row.updated_at = datetime.now(UTC)
            await session.flush()
            return self._reservation(row)

    async def release(
        self, reservation_id: uuid.UUID, *, claim_token: uuid.UUID | None = None
    ) -> BudgetReservation:
        async with self._sessions() as session, session.begin():
            row = await self._locked_reservation(session, reservation_id)
            if row.status == "RELEASED":
                return self._reservation(row)
            if row.status == "CLAIMED":
                self._require_claim(row, claim_token)
            elif row.status != "RESERVED":
                raise BudgetReservationError(f"cannot release reservation from {row.status}")
            await session.execute(
                update(BudgetAccountModel)
                .where(
                    BudgetAccountModel.goal_id == row.goal_id,
                    BudgetAccountModel.reserved_amount >= row.amount,
                )
                .values(
                    reserved_amount=BudgetAccountModel.reserved_amount - row.amount,
                    updated_at=datetime.now(UTC),
                )
            )
            row.status = "RELEASED"
            row.updated_at = datetime.now(UTC)
            await session.flush()
            return self._reservation(row)

    @staticmethod
    def _validate_cost(cost_type: str, amount: float) -> None:
        if cost_type not in _ALL_COST_TYPES:
            raise ValueError(f"unknown cost type: {cost_type}")
        if amount < 0:
            raise ValueError("cost amount must be non-negative")

    @staticmethod
    def _reservation(row: BudgetReservationModel) -> BudgetReservation:
        return BudgetReservation(
            id=row.id,
            reservation_key=row.reservation_key,
            goal_id=row.goal_id,
            run_id=row.run_id,
            amount=row.amount,
            settled_amount=row.settled_amount,
            status=row.status,
            claim_token=row.claim_token,
        )

    @staticmethod
    async def _locked_reservation(
        session: AsyncSession, reservation_id: uuid.UUID
    ) -> BudgetReservationModel:
        row = await session.get(BudgetReservationModel, reservation_id, with_for_update=True)
        if row is None:
            raise BudgetReservationError("reservation not found")
        return row

    @staticmethod
    def _require_claim(row: BudgetReservationModel, claim_token: uuid.UUID | None) -> None:
        if row.status != "CLAIMED" or claim_token is None or row.claim_token != claim_token:
            raise BudgetReservationError("claimed reservation token mismatch")

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
        self._validate_cost(cost_type, amount)
        async with self._sessions() as session, session.begin():
            # Defect #12: agentic epochs sometimes reference run ids that never
            # materialized a runs row; the FK violation aborted cost recording.
            # Ship-first: keep the cost entry, drop the dangling run reference.
            run_id = await self._existing_run_id(session, run_id)
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

    @staticmethod
    async def _existing_run_id(
        session: AsyncSession, run_id: uuid.UUID | None
    ) -> uuid.UUID | None:
        if run_id is None:
            return None
        exists = await session.scalar(select(RunModel.id).where(RunModel.id == run_id))
        if exists is None:
            LOGGER.warning("dangling run_id %s in cost entry; nulling reference", run_id)
            return None
        return run_id

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
                select(BudgetEntryModel).where(BudgetEntryModel.goal_id.in_(goal_ids))
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
