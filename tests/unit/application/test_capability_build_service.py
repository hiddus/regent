"""Unit tests for GAC-B2 BUILD materialization."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.application.capability_build_service import materialize_build_items
from regent.application.capability_resolution_service import (
    ResolutionItem,
    ResolutionMethod,
)


@pytest.mark.asyncio
async def test_materialize_build_registers_capability_id() -> None:
    goal_id = uuid.uuid4()
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.flush = AsyncMock()

    items = (
        ResolutionItem(
            requirement_key="news.aggregator",
            capability_name="news-aggregator-v1",
            gap_type="MISSING",
            method=ResolutionMethod.BUILD,
        ),
        ResolutionItem(
            requirement_key="ui.surface",
            capability_name="product-surface-v1",
            gap_type="NONE",
            method=ResolutionMethod.REUSE,
            capability_id=uuid.uuid4(),
        ),
    )
    out = await materialize_build_items(session, goal_id=goal_id, items=items)
    assert out[0].method is ResolutionMethod.BUILD
    assert out[0].capability_id is not None
    assert out[0].gap_type == "BUILT"
    assert out[1].capability_id == items[1].capability_id
    session.add.assert_called_once()
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_materialize_build_reuses_existing_goal_scoped() -> None:
    goal_id = uuid.uuid4()
    existing_id = uuid.uuid4()
    existing = MagicMock()
    existing.id = existing_id
    existing.status = "REVOKED"
    existing.verification = {}
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=existing)
    session.add = MagicMock()
    session.flush = AsyncMock()

    items = (
        ResolutionItem(
            requirement_key="x",
            capability_name="custom-cap",
            gap_type="MISSING",
            method=ResolutionMethod.BUILD,
        ),
    )
    out = await materialize_build_items(session, goal_id=goal_id, items=items)
    assert out[0].capability_id == existing_id
    assert existing.status == "GOAL_CERTIFIED"
    session.add.assert_not_called()
