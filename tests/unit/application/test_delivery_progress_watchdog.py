"""Unit tests for delivery_progress_watchdog."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.application.delivery_progress_watchdog import tick_stale_delivery_progress
from regent.infrastructure.models import GoalModel


def _goal(*, age_minutes: int, app_project_id: uuid.UUID | None = None) -> GoalModel:
    now = datetime.now(UTC)
    goal = GoalModel(
        id=uuid.uuid4(),
        app_project_id=app_project_id,
        original_input="build something",
        status="ACTIVE",
        version=1,
        created_by="test",
        correlation_id=uuid.uuid4(),
        metadata_json={
            "live_action": {
                "summary": "working",
                "updated_at": (now - timedelta(minutes=age_minutes)).isoformat(),
            }
        },
    )
    goal.updated_at = now - timedelta(minutes=age_minutes)
    return goal


@pytest.mark.asyncio
async def test_watchdog_warns_between_5_and_15_minutes() -> None:
    goal = _goal(age_minutes=8)
    session = AsyncMock()
    session.scalar = AsyncMock(side_effect=[True, None])  # lock, then unused
    result = MagicMock()
    result.scalars.return_value.all.return_value = [goal]
    session.execute = AsyncMock(return_value=result)
    begin = AsyncMock()
    begin.__aenter__.return_value = None
    begin.__aexit__.return_value = None
    session.begin = MagicMock(return_value=begin)
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = None
    factory = MagicMock(return_value=session_cm)

    stats = await tick_stale_delivery_progress(factory)
    assert stats["warned"] == 1
    assert stats["handed_off"] == 0
    assert goal.metadata_json.get("stale_progress_warned_at")


@pytest.mark.asyncio
async def test_watchdog_handoff_opens_task_with_confirmation_message() -> None:
    project_id = uuid.uuid4()
    goal = _goal(age_minutes=20, app_project_id=project_id)
    session = AsyncMock()
    # lock ok, no open task, no conversation (append no-ops)
    session.scalar = AsyncMock(side_effect=[True, None, None])
    session.add = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [goal]
    session.execute = AsyncMock(return_value=result)
    begin = AsyncMock()
    begin.__aenter__.return_value = None
    begin.__aexit__.return_value = None
    session.begin = MagicMock(return_value=begin)
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = None
    factory = MagicMock(return_value=session_cm)

    stats = await tick_stale_delivery_progress(factory)
    assert stats["handed_off"] == 1
    assert goal.metadata_json.get("awaiting_human_intervention") is True
    assert goal.metadata_json.get("stale_progress_handoff_at")
    assert session.add.call_count >= 1
