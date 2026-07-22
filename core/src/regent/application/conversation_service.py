import uuid
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
                content=command.content,
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
