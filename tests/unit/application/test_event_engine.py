"""EventEngine unit tests.

Verifies:
- Handler registration and bulk registration
- Event dispatch to correct handlers
- Handler failure doesn't crash the engine
- P1 coverage reporting
- Lifecycle management
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.application.event_engine import DispatchRecord, EventEngine
from regent.application.execution_events import (
    GOAL_EXECUTION_REQUESTED,
    DISCOVERY_ROUND_REQUESTED,
    P1_MAIN_CHAIN_EVENTS,
)
from regent.infrastructure.models import OutboxEventModel


def _make_event(event_type: str = "TestEvent") -> OutboxEventModel:
    return OutboxEventModel(
        id=uuid.uuid4(),
        event_type=event_type,
        aggregate_type="goal",
        aggregate_id=uuid.uuid4(),
        aggregate_version=1,
        payload={"test": True},
        status="PENDING",
    )


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------


def test_register_handler() -> None:
    engine = EventEngine(MagicMock())
    handler = AsyncMock()
    engine.register_handler("TestEvent", handler)
    assert "TestEvent" in engine.registered_event_types


def test_register_handlers_bulk() -> None:
    engine = EventEngine(MagicMock())
    h1 = AsyncMock()
    h2 = AsyncMock()
    engine.register_handlers({"EventA": h1, "EventB": h2})
    assert engine.registered_event_types == {"EventA", "EventB"}


def test_multiple_handlers_per_event() -> None:
    engine = EventEngine(MagicMock())
    h1 = AsyncMock()
    h2 = AsyncMock()
    engine.register_handler("TestEvent", h1)
    engine.register_handler("TestEvent", h2)
    assert len(engine._handlers["TestEvent"]) == 2


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_calls_handler() -> None:
    engine = EventEngine(MagicMock())
    handler = AsyncMock()
    engine.register_handler("TestEvent", handler)
    event = _make_event("TestEvent")
    await engine.dispatch(event)
    handler.assert_called_once()
    assert len(engine.dispatch_log) == 1
    assert engine.dispatch_log[0].handler_ok is True


@pytest.mark.asyncio
async def test_dispatch_no_handler_is_noop() -> None:
    engine = EventEngine(MagicMock())
    event = _make_event("UnhandledEvent")
    await engine.dispatch(event)
    assert len(engine.dispatch_log) == 0


@pytest.mark.asyncio
async def test_dispatch_handler_failure_recorded() -> None:
    engine = EventEngine(MagicMock())
    handler = AsyncMock(side_effect=RuntimeError("boom"))
    engine.register_handler("TestEvent", handler)
    event = _make_event("TestEvent")
    await engine.dispatch(event)
    assert len(engine.dispatch_log) == 1
    assert engine.dispatch_log[0].handler_ok is False


@pytest.mark.asyncio
async def test_dispatch_multiple_handlers_all_called() -> None:
    engine = EventEngine(MagicMock())
    h1 = AsyncMock()
    h2 = AsyncMock()
    engine.register_handler("TestEvent", h1)
    engine.register_handler("TestEvent", h2)
    event = _make_event("TestEvent")
    await engine.dispatch(event)
    h1.assert_called_once()
    h2.assert_called_once()
    assert len(engine.dispatch_log) == 2


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_stop_lifecycle() -> None:
    engine = EventEngine(MagicMock())
    assert engine.is_running is False
    await engine.start()
    assert engine.is_running is True
    await engine.stop()
    assert engine.is_running is False


# ---------------------------------------------------------------------------
# P1 coverage
# ---------------------------------------------------------------------------


def test_p1_coverage_empty_engine() -> None:
    engine = EventEngine(MagicMock())
    assert engine.p1_coverage() == 0.0


def test_p1_coverage_full() -> None:
    engine = EventEngine(MagicMock())
    handler = AsyncMock()
    for event_type in P1_MAIN_CHAIN_EVENTS:
        engine.register_handler(event_type, handler)
    assert engine.p1_coverage() == 1.0


def test_p1_coverage_partial() -> None:
    engine = EventEngine(MagicMock())
    handler = AsyncMock()
    engine.register_handler(GOAL_EXECUTION_REQUESTED, handler)
    coverage = engine.p1_coverage()
    assert 0.0 < coverage < 1.0


# ---------------------------------------------------------------------------
# DispatchRecord
# ---------------------------------------------------------------------------


def test_dispatch_record_immutable() -> None:
    record = DispatchRecord(
        event_id=uuid.uuid4(),
        event_type="Test",
        handler_ok=True,
        duration_ms=12.5,
        timestamp="2024-01-01T00:00:00",
    )
    assert record.event_type == "Test"
    assert record.handler_ok is True
    with pytest.raises(AttributeError):
        record.event_type = "Changed"  # type: ignore[misc]
