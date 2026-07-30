"""Outbox dispatcher: retryable infra vs non-retryable business INVALID_STATE."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.domain.errors import DomainError, ErrorCode
from regent.runtime.dispatcher import OutboxDispatcher, is_retryable_handler_error


def test_invalid_state_is_not_retryable() -> None:
    assert is_retryable_handler_error(
        DomainError(ErrorCode.INVALID_STATE, "frozen generation plan is required")
    ) is False


def test_lease_conflict_is_retryable() -> None:
    assert is_retryable_handler_error(
        DomainError(ErrorCode.LEASE_CONFLICT, "generation run already in progress")
    ) is True


def test_infrastructure_exception_is_retryable() -> None:
    assert is_retryable_handler_error(TimeoutError("db timeout")) is True


@pytest.mark.asyncio
async def test_fail_dead_letters_non_retryable_immediately() -> None:
    event_id = uuid.uuid4()
    event = MagicMock()
    event.id = event_id
    event.attempt = 1
    event.status = "DISPATCHING"

    session = AsyncMock()
    session.get = AsyncMock(return_value=event)
    captured: dict[str, object] = {}

    async def _execute(stmt: object) -> MagicMock:
        # Capture values from Update._values (Column -> BindParameter).
        raw_values = getattr(stmt, "_values", {}) or {}
        for key, bind in raw_values.items():
            name = getattr(key, "key", str(key))
            captured[name] = getattr(bind, "value", bind)
        return MagicMock(rowcount=1)

    session.execute = AsyncMock(side_effect=_execute)
    session.scalar = AsyncMock(return_value=datetime.now(UTC))
    begin = AsyncMock()
    begin.__aenter__.return_value = None
    begin.__aexit__.return_value = None
    session.begin = MagicMock(return_value=begin)
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = None
    factory = MagicMock(return_value=session_cm)

    dispatcher = OutboxDispatcher(factory, handlers={})
    await dispatcher.fail(
        event_id,
        "worker-1",
        "DomainError: INVALID_STATE: frozen generation plan is required",
        retryable=False,
    )

    assert captured.get("status") == "DEAD_LETTER"
    assert "[non-retryable]" in str(captured.get("last_error") or "")


@pytest.mark.asyncio
async def test_dispatch_once_dead_letters_invalid_state_without_retry_loop() -> None:
    event_id = uuid.uuid4()
    payload = {"goal_id": str(uuid.uuid4())}

    claimed = MagicMock()
    claimed.id = event_id
    claimed.event_type = "GenerationRunRequested"
    claimed.payload = payload
    claimed.attempt = 1
    claimed.correlation_id = uuid.uuid4()

    async def boom(_payload: dict) -> None:
        raise DomainError(ErrorCode.INVALID_STATE, "frozen generation plan is required")

    dispatcher = OutboxDispatcher(
        MagicMock(),
        handlers={"GenerationRunRequested": boom},
        max_attempts=8,
    )
    dispatcher.claim = AsyncMock(return_value=[claimed])  # type: ignore[method-assign]
    dispatcher.fail = AsyncMock()  # type: ignore[method-assign]
    dispatcher.ack = AsyncMock()  # type: ignore[method-assign]

    count = await dispatcher.dispatch_once("worker-1")
    assert count == 1
    dispatcher.ack.assert_not_awaited()
    dispatcher.fail.assert_awaited_once()
    _args, kwargs = dispatcher.fail.await_args
    assert kwargs.get("retryable") is False
    assert "INVALID_STATE" in _args[2]
