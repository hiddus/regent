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


def _session_factory(goal: GoalModel, *, execute_side_effect) -> MagicMock:
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=True)
    session.add = MagicMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    begin = AsyncMock()
    begin.__aenter__.return_value = None
    begin.__aexit__.return_value = None
    session.begin = MagicMock(return_value=begin)
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = None
    factory = MagicMock(return_value=session_cm)
    factory._session = session
    return factory


@pytest.mark.asyncio
async def test_watchdog_warns_between_5_and_15_minutes() -> None:
    goal = _goal(age_minutes=8)
    goals_result = MagicMock()
    goals_result.scalars.return_value.all.return_value = [goal]
    factory = _session_factory(goal, execute_side_effect=[goals_result])

    stats = await tick_stale_delivery_progress(factory)
    assert stats["warned"] == 1
    assert stats["handed_off"] == 0
    assert stats["auto_continued"] == 0
    assert goal.metadata_json.get("stale_progress_warned_at")


@pytest.mark.asyncio
async def test_watchdog_auto_continues_without_human_task() -> None:
    project_id = uuid.uuid4()
    goal = _goal(age_minutes=20, app_project_id=project_id)
    goals_result = MagicMock()
    goals_result.scalars.return_value.all.return_value = [goal]
    empty_tasks = MagicMock()
    empty_tasks.scalars.return_value.all.return_value = []
    factory = _session_factory(
        goal, execute_side_effect=[goals_result, empty_tasks]
    )

    stats = await tick_stale_delivery_progress(factory)
    assert stats["auto_continued"] == 1
    assert stats["handed_off"] == 0
    assert goal.metadata_json.get("awaiting_human_intervention") is False
    assert goal.metadata_json.get("stale_progress_nudge_count") == 1
    assert factory._session.add.call_count >= 1
