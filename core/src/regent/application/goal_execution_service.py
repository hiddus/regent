import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AppProjectModel,
    AuditRecordModel,
    ConversationMessageModel,
    ConversationModel,
    GoalModel,
    GoalSpecModel,
    OutboxEventModel,
)


@dataclass(frozen=True, slots=True)
class GoalExecutionReceipt:
    goal_id: uuid.UUID
    project_id: uuid.UUID
    status: str
    stage: str
    event_id: uuid.UUID | None


class GoalExecutionService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def start(
        self,
        goal_id: uuid.UUID,
        *,
        actor: str,
        idempotency_key: str,
    ) -> GoalExecutionReceipt:
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None or goal.app_project_id is None:
                raise DomainError(ErrorCode.NOT_FOUND, "app goal not found")
            project = await session.get(AppProjectModel, goal.app_project_id)
            spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == goal_id)
                .order_by(GoalSpecModel.version.desc())
            )
            if project is None or spec is None:
                raise DomainError(ErrorCode.NOT_FOUND, "app goal context not found")
            metadata = dict(goal.metadata_json)
            current_key = metadata.get("execution_idempotency_key")
            current_stage = str(metadata.get("execution_stage", "NOT_STARTED"))
            if current_key == idempotency_key and goal.status == "ACTIVE":
                return GoalExecutionReceipt(goal.id, project.id, goal.status, current_stage, None)
            retryable = goal.status == "ACTIVE" and current_stage == "FAILED"
            if (
                (goal.status != "READY" and not retryable)
                or spec.status != "FROZEN"
                or project.status != "ACTIVE"
            ):
                raise DomainError(
                    ErrorCode.INVALID_STATE, "startable or retryable app goal required"
                )
            event_id = uuid.uuid4()
            goal.status = "ACTIVE"
            goal.version += 1
            goal.metadata_json = {
                **metadata,
                "execution_idempotency_key": idempotency_key,
                "execution_event_id": str(event_id),
            }
            payload = {
                "goal_id": str(goal.id),
                "app_project_id": str(project.id),
                "actor": actor,
                "idempotency_key": idempotency_key,
            }
            session.add_all(
                (
                    AuditRecordModel(
                        id=uuid.uuid4(),
                        aggregate_type="goal",
                        aggregate_id=goal.id,
                        aggregate_version=goal.version,
                        action="START_GOAL_EXECUTION",
                        actor=actor,
                        payload=payload,
                        correlation_id=goal.correlation_id,
                    ),
                    OutboxEventModel(
                        id=event_id,
                        event_type="GoalExecutionRequested",
                        aggregate_type="goal",
                        aggregate_id=goal.id,
                        aggregate_version=goal.version,
                        payload=payload,
                        correlation_id=goal.correlation_id,
                    ),
                )
            )
            await self._append_event(
                session,
                project.id,
                "GOAL_EXECUTION_QUEUED",
                "目标已固化。Core 已开始执行。你无需继续操作。",
                {"goal_id": str(goal.id), "event_id": str(event_id)},
            )
            await session.flush()
            return GoalExecutionReceipt(goal.id, project.id, goal.status, "QUEUED", event_id)

    async def update_stage(
        self,
        goal_id: uuid.UUID,
        stage: str,
        *,
        message: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None or goal.app_project_id is None:
                raise DomainError(ErrorCode.NOT_FOUND, "app goal not found")
            goal.metadata_json = {**goal.metadata_json, "execution_stage": stage}
            await self._append_event(
                session,
                goal.app_project_id,
                f"GOAL_EXECUTION_{stage}",
                message,
                {"goal_id": str(goal.id), "stage": stage, **(metadata or {})},
            )

    @staticmethod
    async def _append_event(
        session: AsyncSession,
        project_id: uuid.UUID,
        message_type: str,
        content: str,
        metadata: dict[str, str],
    ) -> None:
        conversation = await session.scalar(
            select(ConversationModel).where(ConversationModel.app_project_id == project_id)
        )
        if conversation is None:
            return
        last = await session.scalar(
            select(ConversationMessageModel.ordinal)
            .where(ConversationMessageModel.conversation_id == conversation.id)
            .order_by(ConversationMessageModel.ordinal.desc())
            .limit(1)
        )
        session.add(
            ConversationMessageModel(
                id=uuid.uuid4(),
                conversation_id=conversation.id,
                ordinal=(last or 0) + 1,
                role="EVENT",
                message_type=message_type,
                content=content,
                metadata_json=metadata,
                created_by="regent-core",
            )
        )
