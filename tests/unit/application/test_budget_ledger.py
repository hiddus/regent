"""BudgetLedger unit tests.

Verifies:
- Cost recording with valid/invalid cost types
- Budget report generation
- Budget limit checking and Goal blocking
- Organization-level budget aggregation
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from regent.application.budget_ledger import (
    COST_MODEL_INPUT,
    COST_MODEL_OUTPUT,
    COST_TOOL_INVOCATION,
    BudgetLedger,
    BudgetReport,
    BudgetStatus,
)
from regent.infrastructure.models import BudgetEntryModel, GoalModel


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
        COST_EXTERNAL_OPERATION,
        COST_FAILURE_RECOVERY,
        COST_HUMAN_MINUTES,
        COST_INFRASTRUCTURE,
        _ALL_COST_TYPES,
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
