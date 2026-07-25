"""Unit tests for GAC-C1 CREATED run advancement."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from regent.application.run_advancement import advance_created_run, reclaim_stale_created_runs


@pytest.mark.asyncio
async def test_advance_created_run_skips_non_created() -> None:
    run_id = uuid.uuid4()
    run = MagicMock(status="RUNNING", version=3, correlation_id=uuid.uuid4())
    session = AsyncMock()
    session.get = AsyncMock(return_value=run)
    ctx = AsyncMock()
    ctx.__aenter__.return_value = session
    ctx.__aexit__.return_value = None
    factory = MagicMock(return_value=ctx)

    status = await advance_created_run(factory, run_id, actor="test")
    assert status == "RUNNING"


@pytest.mark.asyncio
async def test_advance_created_run_transitions_chain() -> None:
    run_id = uuid.uuid4()
    correlation = uuid.uuid4()
    run = MagicMock(status="CREATED", version=0, correlation_id=correlation)
    session = AsyncMock()
    session.get = AsyncMock(return_value=run)
    ctx = AsyncMock()
    ctx.__aenter__.return_value = session
    ctx.__aexit__.return_value = None
    factory = MagicMock(return_value=ctx)

    receipts = [MagicMock(version=i) for i in (1, 2, 3)]
    with patch(
        "regent.application.run_advancement.TransitionService"
    ) as ts_cls:
        ts = ts_cls.return_value
        ts.transition_run = AsyncMock(side_effect=receipts)
        status = await advance_created_run(factory, run_id, actor="test")

    assert status == "RUNNING"
    assert ts.transition_run.await_count == 3


@pytest.mark.asyncio
async def test_reclaim_stale_created_runs_counts_successes() -> None:
    runs = [MagicMock(id=uuid.uuid4()) for _ in range(2)]
    session = AsyncMock()
    session.scalars = AsyncMock(return_value=runs)
    ctx = AsyncMock()
    ctx.__aenter__.return_value = session
    ctx.__aexit__.return_value = None
    factory = MagicMock(return_value=ctx)

    with patch(
        "regent.application.run_advancement.advance_created_run",
        AsyncMock(side_effect=["RUNNING", Exception("boom")]),
    ):
        n = await reclaim_stale_created_runs(factory, actor="test", limit=5)
    assert n == 1
