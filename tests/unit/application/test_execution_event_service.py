from __future__ import annotations

import uuid

import pytest
from regent.application.execution_event_service import (
    AppendExecutionEvent,
    ExecutionEventService,
)
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import GoalModel


async def seed_goal(db_sessions) -> uuid.UUID:
    goal_id = uuid.uuid4()
    async with db_sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                original_input="operate",
                status="ACTIVE",
                version=0,
                created_by="test",
                correlation_id=uuid.uuid4(),
                metadata_json={},
            )
        )
    return goal_id


def command(goal_id: uuid.UUID, event_key: str, **overrides) -> AppendExecutionEvent:
    values = {
        "event_key": event_key,
        "event_type": "AGENT_STEP_COMPLETED",
        "goal_id": goal_id,
        "input_hash": "a" * 64,
        "output_hash": "b" * 64,
        "permission_snapshot": {"scope": ["workspace:read"]},
        "budget_reservation_ref": "reservation:1",
        "model_version": "model-v1",
        "tool_versions": {"search": "v2"},
    }
    values.update(overrides)
    return AppendExecutionEvent(**values)


@pytest.mark.asyncio
async def test_append_is_idempotent_for_same_event_key_and_payload(db_sessions) -> None:
    goal_id = await seed_goal(db_sessions)
    service = ExecutionEventService(db_sessions)
    first = await service.append(command(goal_id, "step:1"))
    replay = await service.append(command(goal_id, "step:1"))
    assert replay.event_id == first.event_id
    assert replay.goal_sequence == 1


@pytest.mark.asyncio
async def test_event_key_collision_with_changed_payload_is_rejected(db_sessions) -> None:
    goal_id = await seed_goal(db_sessions)
    service = ExecutionEventService(db_sessions)
    await service.append(command(goal_id, "step:1"))
    with pytest.raises(DomainError) as exc:
        await service.append(command(goal_id, "step:1", output_hash="c" * 64))
    assert exc.value.code is ErrorCode.VERSION_CONFLICT


@pytest.mark.asyncio
async def test_goal_replay_has_deterministic_sequence_and_lineage(db_sessions) -> None:
    goal_id = await seed_goal(db_sessions)
    service = ExecutionEventService(db_sessions)
    parent = await service.append(command(goal_id, "step:1"))
    child = await service.append(
        command(
            goal_id,
            "step:2",
            parent_event_id=parent.event_id,
            causation_event_id=parent.event_id,
        )
    )
    events = await service.replay_goal(goal_id)
    assert [event.event_key for event in events] == ["step:1", "step:2"]
    assert [event.goal_sequence for event in events] == [1, 2]
    assert child.parent_event_id == parent.event_id
    assert child.causation_event_id == parent.event_id


@pytest.mark.asyncio
async def test_cross_goal_lineage_is_rejected(db_sessions) -> None:
    first_goal = await seed_goal(db_sessions)
    second_goal = await seed_goal(db_sessions)
    service = ExecutionEventService(db_sessions)
    parent = await service.append(command(first_goal, "first:1"))
    with pytest.raises(DomainError) as exc:
        await service.append(
            command(second_goal, "second:1", parent_event_id=parent.event_id)
        )
    assert exc.value.code is ErrorCode.INVALID_STATE
