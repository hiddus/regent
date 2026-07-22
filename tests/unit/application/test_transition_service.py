import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.application.transition_service import TransitionContext, TransitionService
from regent.domain.errors import DomainError, ErrorCode
from regent.domain.states import GoalState
from regent.domain.transitions import GoalCommand
from regent.infrastructure.models import (
    AuditRecordModel,
    GoalModel,
    OutboxEventModel,
)
from sqlalchemy.ext.asyncio import AsyncSession


def make_service(
    goal: GoalModel | None, *, rowcount: int = 1
) -> tuple[TransitionService, AsyncMock]:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = goal
    session.execute.return_value = MagicMock(rowcount=rowcount)

    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    transaction_context = AsyncMock()
    transaction_context.__aenter__.return_value = None
    transaction_context.__aexit__.return_value = None
    session.begin = MagicMock(return_value=transaction_context)

    factory = MagicMock(return_value=session_context)
    return TransitionService(factory), session  # type: ignore[arg-type]


def context(goal_id: uuid.UUID, *, expected_version: int = 0) -> TransitionContext:
    return TransitionContext(
        aggregate_id=goal_id,
        expected_version=expected_version,
        actor="test",
        correlation_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_transition_persists_state_audit_and_outbox_in_one_session() -> None:
    goal_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="test",
        status=GoalState.DRAFT.value,
        version=0,
        created_by="test",
        correlation_id=uuid.uuid4(),
        metadata_json={},
    )
    service, session = make_service(goal)

    receipt = await service.transition_goal(context(goal_id), GoalCommand.QUALIFY)

    assert receipt.state == GoalState.READY.value
    assert receipt.version == 1
    session.execute.assert_awaited_once()
    added = [call.args[0] for call in session.add.call_args_list]
    assert len(added) == 2
    assert isinstance(added[0], AuditRecordModel)
    assert isinstance(added[1], OutboxEventModel)
    assert added[0].aggregate_version == added[1].aggregate_version == 1
    assert added[0].correlation_id == added[1].correlation_id


@pytest.mark.asyncio
async def test_missing_aggregate_has_stable_error() -> None:
    goal_id = uuid.uuid4()
    service, session = make_service(None)

    with pytest.raises(DomainError) as raised:
        await service.transition_goal(context(goal_id), GoalCommand.QUALIFY)

    assert raised.value.code == ErrorCode.NOT_FOUND
    session.execute.assert_not_awaited()
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_update_does_not_append_audit_or_outbox() -> None:
    goal_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="test",
        status=GoalState.DRAFT.value,
        version=0,
        created_by="test",
        correlation_id=uuid.uuid4(),
        metadata_json={},
    )
    service, session = make_service(goal, rowcount=0)

    with pytest.raises(DomainError) as raised:
        await service.transition_goal(context(goal_id), GoalCommand.QUALIFY)

    assert raised.value.code == ErrorCode.VERSION_CONFLICT
    session.add.assert_not_called()
