import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import Update, func, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.domain.states import GoalState, RunState, WorkState
from regent.domain.transitions import (
    GoalCommand,
    RunCommand,
    Transitioned,
    WorkCommand,
    transition_goal,
    transition_run,
    transition_work,
)
from regent.infrastructure.models import (
    AuditRecordModel,
    GoalModel,
    OutboxEventModel,
    RunModel,
    WorkModel,
)


@dataclass(frozen=True, slots=True)
class TransitionContext:
    aggregate_id: uuid.UUID
    expected_version: int
    actor: str
    correlation_id: uuid.UUID
    causation_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class TransitionReceipt:
    aggregate_id: uuid.UUID
    previous_state: str
    state: str
    version: int
    audit_id: uuid.UUID
    event_id: uuid.UUID


class TransitionService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def transition_goal(
        self, context: TransitionContext, command: GoalCommand
    ) -> TransitionReceipt:
        async with self._sessions() as session, session.begin():
            model = await session.get(GoalModel, context.aggregate_id, with_for_update=True)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"goal {context.aggregate_id} not found")
            result = transition_goal(
                GoalState(model.status),
                command,
                version=model.version,
                expected_version=context.expected_version,
            )
            return await self._persist(
                session=session,
                model=GoalModel,
                context=context,
                command=command,
                previous_state=model.status,
                result=result,
                aggregate_type="goal",
                event_type="GoalStateChanged",
            )

    async def transition_work(
        self, context: TransitionContext, command: WorkCommand
    ) -> TransitionReceipt:
        async with self._sessions() as session, session.begin():
            model = await session.get(WorkModel, context.aggregate_id, with_for_update=True)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"work {context.aggregate_id} not found")
            result = transition_work(
                WorkState(model.status),
                command,
                version=model.version,
                expected_version=context.expected_version,
            )
            return await self._persist(
                session=session,
                model=WorkModel,
                context=context,
                command=command,
                previous_state=model.status,
                result=result,
                aggregate_type="work",
                event_type="WorkStateChanged",
            )

    async def transition_run(
        self, context: TransitionContext, command: RunCommand
    ) -> TransitionReceipt:
        async with self._sessions() as session, session.begin():
            model = await session.get(RunModel, context.aggregate_id, with_for_update=True)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"run {context.aggregate_id} not found")
            result = transition_run(
                RunState(model.status),
                command,
                version=model.version,
                expected_version=context.expected_version,
            )
            return await self._persist(
                session=session,
                model=RunModel,
                context=context,
                command=command,
                previous_state=model.status,
                result=result,
                aggregate_type="run",
                event_type="RunStateChanged",
            )

    async def _persist(
        self,
        *,
        session: AsyncSession,
        model: type[GoalModel] | type[WorkModel] | type[RunModel],
        context: TransitionContext,
        command: StrEnum,
        previous_state: str,
        result: Transitioned[Any],
        aggregate_type: str,
        event_type: str,
    ) -> TransitionReceipt:
        statement: Update = (
            update(model)
            .where(
                model.id == context.aggregate_id,
                model.version == context.expected_version,
                model.status == previous_state,
            )
            .values(
                status=result.state.value,
                version=result.version,
                updated_at=func.now() if hasattr(model, "updated_at") else None,
            )
        )
        if model is RunModel:
            statement = (
                update(model)
                .where(
                    model.id == context.aggregate_id,
                    model.version == context.expected_version,
                    model.status == previous_state,
                )
                .values(status=result.state.value, version=result.version)
            )
        execution = cast(CursorResult[Any], await session.execute(statement))
        if execution.rowcount != 1:
            raise DomainError(
                ErrorCode.VERSION_CONFLICT,
                f"{aggregate_type} changed during transition",
            )

        audit_id = uuid.uuid4()
        event_id = uuid.uuid4()
        payload = {
            "command": command.value,
            "from": previous_state,
            "to": result.state.value,
            "version": result.version,
        }
        session.add(
            AuditRecordModel(
                id=audit_id,
                aggregate_type=aggregate_type,
                aggregate_id=context.aggregate_id,
                aggregate_version=result.version,
                action=command.value,
                actor=context.actor,
                payload=payload,
                correlation_id=context.correlation_id,
                causation_id=context.causation_id,
            )
        )
        session.add(
            OutboxEventModel(
                id=event_id,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=context.aggregate_id,
                aggregate_version=result.version,
                payload=payload,
                status="PENDING",
                attempt=0,
                correlation_id=context.correlation_id,
                causation_id=context.causation_id,
            )
        )
        return TransitionReceipt(
            aggregate_id=context.aggregate_id,
            previous_state=previous_state,
            state=result.state.value,
            version=result.version,
            audit_id=audit_id,
            event_id=event_id,
        )
