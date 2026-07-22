import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field

from regent.application.conversation_service import (
    AppendConversationMessage,
    ConversationService,
    CreateConversation,
)

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


def service(request: Request) -> ConversationService:
    return ConversationService(request.app.state.sessions)


ServiceDep = Annotated[ConversationService, Depends(service)]


class CreateConversationBody(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    actor: str = Field(min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AppendMessageBody(BaseModel):
    role: str = Field(pattern=r"^USER$")
    message_type: str = Field(default="TEXT", min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=100_000)
    actor: str = Field(min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationResponse(BaseModel):
    id: uuid.UUID
    app_project_id: uuid.UUID | None
    goal_id: uuid.UUID | None
    title: str
    status: str
    created_by: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    ordinal: int
    role: str
    message_type: str
    content: str
    metadata: dict[str, Any]
    created_by: str
    created_at: datetime


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: CreateConversationBody, conversations: ServiceDep
) -> ConversationResponse:
    return conversation_response(
        await conversations.create(CreateConversation(**payload.model_dump()))
    )


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    conversations: ServiceDep, limit: int = Query(default=100, ge=1, le=200)
) -> list[ConversationResponse]:
    return [
        conversation_response(item) for item in await conversations.list_conversations(limit=limit)
    ]


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: uuid.UUID, conversations: ServiceDep
) -> ConversationResponse:
    return conversation_response(await conversations.get(conversation_id))


@router.put("/{conversation_id}/goal/{goal_id}", response_model=ConversationResponse)
async def bind_goal(
    conversation_id: uuid.UUID, goal_id: uuid.UUID, conversations: ServiceDep
) -> ConversationResponse:
    return conversation_response(await conversations.bind_goal(conversation_id, goal_id))


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def append_message(
    conversation_id: uuid.UUID,
    payload: AppendMessageBody,
    conversations: ServiceDep,
) -> MessageResponse:
    return message_response(
        await conversations.append(
            AppendConversationMessage(conversation_id=conversation_id, **payload.model_dump())
        )
    )


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    conversation_id: uuid.UUID,
    conversations: ServiceDep,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
) -> list[MessageResponse]:
    return [
        message_response(item)
        for item in await conversations.messages(conversation_id, after=after, limit=limit)
    ]


def conversation_response(model: Any) -> ConversationResponse:
    return ConversationResponse(
        id=model.id,
        app_project_id=model.app_project_id,
        goal_id=model.goal_id,
        title=model.title,
        status=model.status,
        created_by=model.created_by,
        metadata=model.metadata_json,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def message_response(model: Any) -> MessageResponse:
    return MessageResponse(
        id=model.id,
        conversation_id=model.conversation_id,
        ordinal=model.ordinal,
        role=model.role,
        message_type=model.message_type,
        content=model.content,
        metadata=model.metadata_json,
        created_by=model.created_by,
        created_at=model.created_at,
    )
