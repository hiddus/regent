"""BudgetLedger unit tests.

Verifies:
- Cost recording with valid/invalid cost types
- Budget report generation
- Budget limit checking and Goal blocking
- Organization-level budget aggregation
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.application.budget_ledger import (
    COST_MODEL_INPUT,
    COST_MODEL_OUTPUT,
    COST_TOOL_INVOCATION,
    BudgetExceededError,
    BudgetLedger,
    BudgetReport,
    BudgetReservationError,
    BudgetStatus,
)
from regent.infrastructure.models import (
    BudgetAccountModel,
    BudgetEntryModel,
    BudgetReservationModel,
    GoalModel,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def budget_db_sessions():
    """Focused schema, isolated from unrelated in-flight model additions."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: GoalModel.__table__.create(sync_conn, checkfirst=True)
        )
        for table in (
            BudgetEntryModel.__table__,
            BudgetAccountModel.__table__,
            BudgetReservationModel.__table__,
        ):
            await conn.run_sync(lambda sync_conn, table=table: table.create(sync_conn))
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


def test_budget_report_within_limit_when_no_limit() -> None:
    report = BudgetReport(goal_id=uuid.uuid4(), total_cost=100.0)
    assert report.within_limit is True


def test_budget_report_within_limit_when_under() -> None:
    report = BudgetReport(goal_id=uuid.uuid4(), total_cost=50.0, limit=100.0)
    assert report.within_limit is True
    assert report.remaining == 50.0


def test_budget_report_exceeds_limit() -> None:
    report = BudgetReport(goal_id=uuid.uuid4(), total_cost=150.0, limit=100.0)
    assert report.within_limit is False
    assert report.remaining == 0.0  # Clamped to 0


def test_budget_status_remaining() -> None:
    status = BudgetStatus(goal_id=uuid.uuid4(), total_cost=30.0, limit=100.0, is_blocked=False)
    assert status.remaining == 70.0


# ---------------------------------------------------------------------------
# Cost recording
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_cost_creates_entry() -> None:
    goal_id = uuid.uuid4()
    run_id = uuid.uuid4()

    session = AsyncMock()
    session.add = MagicMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    transaction_context = AsyncMock()
    transaction_context.__aenter__.return_value = None
    transaction_context.__aexit__.return_value = None
    session.begin = MagicMock(return_value=transaction_context)
    factory = MagicMock(return_value=session_context)

    ledger = BudgetLedger(factory)
    entry = await ledger.record_cost(
        goal_id,
        run_id,
        cost_type=COST_MODEL_INPUT,
        amount=0.005,
        description="test input tokens",
    )
    session.add.assert_called_once()
    assert entry.goal_id == goal_id
    assert entry.run_id == run_id
    assert entry.cost_type == COST_MODEL_INPUT
    assert entry.amount == 0.005


@pytest.mark.asyncio
async def test_record_cost_rejects_invalid_type() -> None:
    factory = MagicMock()
    ledger = BudgetLedger(factory)
    with pytest.raises(ValueError, match="unknown cost type"):
        await ledger.record_cost(uuid.uuid4(), None, cost_type="invalid", amount=1.0)


@pytest.mark.asyncio
async def test_record_cost_nulls_dangling_run_id() -> None:
    """Defect #12: FK violation on missing runs row must not abort accounting."""
    goal_id = uuid.uuid4()
    dangling = uuid.uuid4()

    session = AsyncMock()
    session.add = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    transaction_context = AsyncMock()
    transaction_context.__aenter__.return_value = None
    transaction_context.__aexit__.return_value = None
    session.begin = MagicMock(return_value=transaction_context)
    factory = MagicMock(return_value=session_context)

    ledger = BudgetLedger(factory)
    entry = await ledger.record_cost(
        goal_id,
        dangling,
        cost_type=COST_MODEL_INPUT,
        amount=1.5,
        description="generation input tokens",
    )
    assert entry.goal_id == goal_id
    assert entry.run_id is None
    assert entry.amount == 1.5


@pytest.mark.asyncio
async def test_record_cost_rejects_negative_amount() -> None:
    factory = MagicMock()
    ledger = BudgetLedger(factory)
    with pytest.raises(ValueError, match="non-negative"):
        await ledger.record_cost(uuid.uuid4(), None, cost_type=COST_MODEL_INPUT, amount=-1.0)


# ---------------------------------------------------------------------------
# Budget limit enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_budget_limit_blocks_goal_when_over() -> None:
    goal_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="test",
        status="ACTIVE",
        version=1,
        created_by="test",
        correlation_id=uuid.uuid4(),
        metadata_json={"budget_limit": 10.0},
    )

    session = AsyncMock()
    session.get = AsyncMock(return_value=goal)
    # Return two entries totaling 15.0 (over the 10.0 limit)
    entry1 = MagicMock()
    entry1.id = uuid.uuid4()
    entry1.run_id = None
    entry1.cost_type = COST_MODEL_INPUT
    entry1.amount = 10.0
    entry1.price_book_version = "price-book-v1"
    entry1.recorded_at = None
    entry1.description = ""
    entry2 = MagicMock()
    entry2.id = uuid.uuid4()
    entry2.run_id = None
    entry2.cost_type = COST_MODEL_OUTPUT
    entry2.amount = 5.0
    entry2.price_book_version = "price-book-v1"
    entry2.recorded_at = None
    entry2.description = ""

    scalars_result = MagicMock()
    scalars_result.all.return_value = [entry1, entry2]
    session.scalars = AsyncMock(return_value=scalars_result)

    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    transaction_context = AsyncMock()
    transaction_context.__aenter__.return_value = None
    transaction_context.__aexit__.return_value = None
    session.begin = MagicMock(return_value=transaction_context)
    factory = MagicMock(return_value=session_context)

    ledger = BudgetLedger(factory)
    status = await ledger.check_budget_limit(goal_id)
    assert status.is_blocked is True
    assert status.total_cost == 15.0
    assert status.limit == 10.0
    assert goal.status == "BLOCKED"
    assert goal.metadata_json["blocked_reason"] == "budget_limit_exceeded"


# ---------------------------------------------------------------------------
# Cost type constants
# ---------------------------------------------------------------------------


def test_all_cost_types_defined() -> None:
    from regent.application.budget_ledger import (
        _ALL_COST_TYPES,
        COST_EXTERNAL_OPERATION,
        COST_FAILURE_RECOVERY,
        COST_HUMAN_MINUTES,
        COST_INFRASTRUCTURE,
    )

    required = {
        COST_MODEL_INPUT,
        COST_MODEL_OUTPUT,
        COST_TOOL_INVOCATION,
        COST_INFRASTRUCTURE,
        COST_EXTERNAL_OPERATION,
        COST_HUMAN_MINUTES,
        COST_FAILURE_RECOVERY,
    }
    assert required <= _ALL_COST_TYPES


async def _goal_with_budget(db_sessions, limit: float) -> tuple[uuid.UUID, BudgetLedger]:
    goal_id = uuid.uuid4()
    async with db_sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                original_input="budget reservation test",
                status="ACTIVE",
                version=1,
                created_by="test",
                correlation_id=uuid.uuid4(),
                metadata_json={"budget_limit": limit},
            )
        )
    return goal_id, BudgetLedger(db_sessions)


@pytest.mark.asyncio
async def test_reserve_claim_settle_refunds_unused_hold(budget_db_sessions) -> None:
    goal_id, ledger = await _goal_with_budget(budget_db_sessions, 10.0)
    hold = await ledger.reserve(
        goal_id, None, reservation_key="run-1:model", cost_type=COST_MODEL_INPUT, amount=8.0
    )
    claimed = await ledger.claim(hold.id)
    settled = await ledger.settle(claimed.id, claim_token=claimed.claim_token, actual_amount=3.0)
    assert settled.status == "SETTLED"
    assert settled.settled_amount == 3.0
    next_hold = await ledger.reserve(
        goal_id, None, reservation_key="run-2:model", cost_type=COST_MODEL_INPUT, amount=7.0
    )
    assert next_hold.status == "RESERVED"
    assert (await ledger.get_goal_budget(goal_id)).total_cost == 3.0


@pytest.mark.asyncio
async def test_reservations_cannot_collectively_exceed_goal_limit(budget_db_sessions) -> None:
    goal_id, ledger = await _goal_with_budget(budget_db_sessions, 10.0)
    await ledger.reserve(
        goal_id, None, reservation_key="parallel-a", cost_type=COST_MODEL_INPUT, amount=6.0
    )
    with pytest.raises(BudgetExceededError):
        await ledger.reserve(
            goal_id, None, reservation_key="parallel-b", cost_type=COST_MODEL_OUTPUT, amount=5.0
        )


@pytest.mark.asyncio
async def test_concurrent_reservations_admit_only_one_overlapping_hold(budget_db_sessions) -> None:
    goal_id, ledger = await _goal_with_budget(budget_db_sessions, 10.0)
    # Initialize the per-Goal atomic counter before contending transactions.
    seed = await ledger.reserve(
        goal_id, None, reservation_key="counter-init", cost_type=COST_MODEL_INPUT, amount=0.0
    )
    await ledger.release(seed.id)

    async def attempt(key: str):
        try:
            return await ledger.reserve(
                goal_id, None, reservation_key=key, cost_type=COST_MODEL_INPUT, amount=6.0
            )
        except BudgetExceededError as exc:
            return exc

    outcomes = await asyncio.gather(attempt("concurrent-a"), attempt("concurrent-b"))
    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, BudgetExceededError) for item in outcomes) == 1


@pytest.mark.asyncio
async def test_release_refunds_full_hold_and_claim_token_is_enforced(budget_db_sessions) -> None:
    goal_id, ledger = await _goal_with_budget(budget_db_sessions, 5.0)
    hold = await ledger.reserve(
        goal_id, None, reservation_key="released", cost_type=COST_TOOL_INVOCATION, amount=5.0
    )
    claimed = await ledger.claim(hold.id)
    with pytest.raises(BudgetReservationError):
        await ledger.release(claimed.id, claim_token=uuid.uuid4())
    await ledger.release(claimed.id, claim_token=claimed.claim_token)
    replacement = await ledger.reserve(
        goal_id, None, reservation_key="replacement", cost_type=COST_TOOL_INVOCATION, amount=5.0
    )
    assert replacement.status == "RESERVED"


@pytest.mark.asyncio
async def test_reservation_key_retry_is_idempotent(budget_db_sessions) -> None:
    goal_id, ledger = await _goal_with_budget(budget_db_sessions, 5.0)
    first = await ledger.reserve(
        goal_id, None, reservation_key="same", cost_type=COST_MODEL_INPUT, amount=4.0
    )
    second = await ledger.reserve(
        goal_id, None, reservation_key="same", cost_type=COST_MODEL_INPUT, amount=4.0
    )
    assert second.id == first.id
    with pytest.raises(BudgetReservationError):
        await ledger.reserve(
            goal_id, None, reservation_key="same", cost_type=COST_MODEL_INPUT, amount=3.0
        )


@pytest.mark.asyncio
async def test_reserving_consumed_key_after_settle_derives_fresh_hold(
    budget_db_sessions,
) -> None:
    """Regression: a resumed epoch re-reserves the same turn key.

    The old row is SETTLED, so claim used to fail forever with
    `cannot claim reservation from SETTLED` and the resumed run looped.
    """
    goal_id, ledger = await _goal_with_budget(budget_db_sessions, 50.0)
    key = "resume:run-1:turn:0"
    first = await ledger.reserve(
        goal_id, None, reservation_key=key, cost_type=COST_MODEL_INPUT, amount=8.0
    )
    claimed = await ledger.claim(first.id)
    await ledger.settle(claimed.id, claim_token=claimed.claim_token, actual_amount=8.0)

    # Resumed epoch: same key must yield a fresh, claimable reservation.
    second = await ledger.reserve(
        goal_id, None, reservation_key=key, cost_type=COST_MODEL_INPUT, amount=8.0
    )
    assert second.id != first.id
    assert second.status == "RESERVED"
    reclaimed = await ledger.claim(second.id)
    assert reclaimed.status == "CLAIMED"


@pytest.mark.asyncio
async def test_reserving_consumed_key_after_release_derives_fresh_hold(
    budget_db_sessions,
) -> None:
    goal_id, ledger = await _goal_with_budget(budget_db_sessions, 50.0)
    key = "resume:released:turn:0"
    first = await ledger.reserve(
        goal_id, None, reservation_key=key, cost_type=COST_TOOL_INVOCATION, amount=5.0
    )
    claimed = await ledger.claim(first.id)
    await ledger.release(claimed.id, claim_token=claimed.claim_token)

    second = await ledger.reserve(
        goal_id, None, reservation_key=key, cost_type=COST_TOOL_INVOCATION, amount=5.0
    )
    assert second.id != first.id
    assert second.status == "RESERVED"


@pytest.mark.asyncio
async def test_many_epochs_reusing_turn_key_do_not_exhaust_namespace(
    budget_db_sessions,
) -> None:
    """Regression: each resumed epoch re-reserves the same per-turn key.

    Terminal rows accumulate one per epoch; bounded probing used to raise
    `reservation key namespace exhausted` after ~16 epochs and blocked all
    further generation for the goal.
    """
    goal_id, ledger = await _goal_with_budget(budget_db_sessions, 1000.0)
    key = "resume:many:turn:0"
    for _ in range(20):
        hold = await ledger.reserve(
            goal_id, None, reservation_key=key, cost_type=COST_MODEL_INPUT, amount=8.0
        )
        assert hold.status == "RESERVED"
        claimed = await ledger.claim(hold.id)
        await ledger.settle(claimed.id, claim_token=claimed.claim_token, actual_amount=8.0)
    # One more epoch after 20 settled rows must still reserve cleanly.
    final = await ledger.reserve(
        goal_id, None, reservation_key=key, cost_type=COST_MODEL_INPUT, amount=8.0
    )
    assert final.status == "RESERVED"
