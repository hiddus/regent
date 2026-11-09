import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    ConversationMessageModel,
    ConversationModel,
    GoalModel,
)


@dataclass(frozen=True, slots=True)
class CreateConversation:
    title: str
    actor: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AppendConversationMessage:
    conversation_id: uuid.UUID
    role: str
    message_type: str
    content: str
    actor: str
    metadata: dict[str, Any] = field(default_factory=dict)


async def append_project_message(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    role: str,
    message_type: str,
    content: str,
    metadata: Mapping[str, object],
) -> None:
    """Append a message to a project's conversation within the caller's transaction."""
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
            role=role,
            message_type=message_type,
            content=content,
            metadata_json=dict(metadata),
            created_by="regent-core",
        )
    )


async def append_project_event(
    session: AsyncSession,
    project_id: uuid.UUID,
    message_type: str,
    content: str,
    metadata: dict[str, str],
) -> None:
    await append_project_message(
        session,
        project_id,
        role="EVENT",
        message_type=message_type,
        content=content,
        metadata=metadata,
    )


async def append_project_timeline_event(
    session: AsyncSession,
    project_id: uuid.UUID,
    message_type: str,
    content: str,
    metadata: dict[str, object],
) -> None:
    """Append an event and reflect it in the goal's live-action projection."""
    await append_project_message(
        session,
        project_id,
        role="EVENT",
        message_type=message_type,
        content=content,
        metadata=metadata,
    )
    goal_raw = metadata.get("goal_id")
    if not goal_raw:
        return
    try:
        goal_id = uuid.UUID(str(goal_raw))
    except ValueError:
        return
    goal = await session.get(GoalModel, goal_id)
    if goal is None:
        return
    from regent.application.live_action import apply_live_action_on_goal, summary_for_event

    stage = None
    if isinstance(goal.metadata_json, dict):
        stage = goal.metadata_json.get("execution_stage")
    apply_live_action_on_goal(
        goal,
        summary_for_event(message_type, content),
        stage=str(stage) if stage else None,
        detail=content[:240] if content else None,
        event_type=message_type,
    )


class ConversationService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(self, command: CreateConversation) -> ConversationModel:
        model = ConversationModel(
            id=uuid.uuid4(),
            title=command.title,
            status="ACTIVE",
            created_by=command.actor,
            metadata_json=command.metadata,
        )
        async with self._sessions() as session, session.begin():
            session.add(model)
            await session.flush()
        return model

    async def list_conversations(self, *, limit: int = 100) -> list[ConversationModel]:
        async with self._sessions() as session:
            return list(
                await session.scalars(
                    select(ConversationModel)
                    .order_by(ConversationModel.updated_at.desc())
                    .limit(limit)
                )
            )

    async def get(self, conversation_id: uuid.UUID) -> ConversationModel:
        async with self._sessions() as session:
            model = await session.get(ConversationModel, conversation_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "conversation not found")
            return model

    async def bind_goal(self, conversation_id: uuid.UUID, goal_id: uuid.UUID) -> ConversationModel:
        async with self._sessions() as session, session.begin():
            model = await session.get(ConversationModel, conversation_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "conversation not found")
            if await session.get(GoalModel, goal_id) is None:
                raise DomainError(ErrorCode.NOT_FOUND, "goal not found")
            if model.goal_id is not None and model.goal_id != goal_id:
                raise DomainError(ErrorCode.INVALID_STATE, "conversation already has a goal")
            model.goal_id = goal_id
            await session.flush()
            return model

    async def append(self, command: AppendConversationMessage) -> ConversationMessageModel:
        if command.role not in {"USER", "ASSISTANT", "SYSTEM", "EVENT"}:
            raise DomainError(ErrorCode.INVALID_STATE, "unsupported conversation role")
        async with self._sessions() as session:
            conversation = await session.get(ConversationModel, command.conversation_id)
            if conversation is None:
                raise DomainError(ErrorCode.NOT_FOUND, "conversation not found")
            goal_id = getattr(conversation, "goal_id", None)

        content = command.content
        if goal_id is not None:
            from regent.application.privacy_service import PrivacyService

            privacy = PrivacyService(self._sessions)
            await privacy.require_consent_for_scope(goal_id, scope="conversation")
            content = privacy.reject_restricted_payload(content, context="conversation.content")

        async with self._sessions() as session, session.begin():
            conversation = await session.get(ConversationModel, command.conversation_id)
            if conversation is None:
                raise DomainError(ErrorCode.NOT_FOUND, "conversation not found")
            if conversation.status != "ACTIVE":
                raise DomainError(ErrorCode.INVALID_STATE, "conversation is archived")
            last = await session.scalar(
                select(func.max(ConversationMessageModel.ordinal)).where(
                    ConversationMessageModel.conversation_id == command.conversation_id
                )
            )
            model = ConversationMessageModel(
                id=uuid.uuid4(),
                conversation_id=command.conversation_id,
                ordinal=(last or 0) + 1,
                role=command.role,
                message_type=command.message_type,
                content=content,
                metadata_json=command.metadata,
                created_by=command.actor,
            )
            session.add(model)
            conversation.updated_at = func.now()
            await session.flush()
            return model

    async def messages(
        self, conversation_id: uuid.UUID, *, after: int = 0, limit: int = 500
    ) -> list[ConversationMessageModel]:
        async with self._sessions() as session:
            if await session.get(ConversationModel, conversation_id) is None:
                raise DomainError(ErrorCode.NOT_FOUND, "conversation not found")
            return list(
                await session.scalars(
                    select(ConversationMessageModel)
                    .where(
                        ConversationMessageModel.conversation_id == conversation_id,
                        ConversationMessageModel.ordinal > after,
                    )
                    .order_by(ConversationMessageModel.ordinal)
                    .limit(limit)
                )
            )
