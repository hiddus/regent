"""P2-3 memory HTTP API (activated by stage DecisionRecord)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.memory_service import AdmitMemory, MemoryService

router = APIRouter(tags=["memories"])


def service(request: Request) -> MemoryService:
    return MemoryService(request.app.state.sessions)


ServiceDep = Annotated[MemoryService, Depends(service)]


class AdmitBody(BaseModel):
    org_key: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=64)
    content: dict[str, Any]
    actor: str = Field(min_length=1)
    goal_id: uuid.UUID | None = None
    source_refs: list[Any] = Field(default_factory=list)


class ActorBody(BaseModel):
    actor: str = Field(min_length=1)


class RevokeBody(BaseModel):
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=255)


def _serialize(model: Any) -> dict[str, Any]:
    return {
        "id": str(model.id),
        "org_key": model.org_key,
        "goal_id": str(model.goal_id) if model.goal_id else None,
        "status": model.status,
        "kind": model.kind,
        "content_hash": model.content_hash,
        "source_refs": model.source_refs,
        "created_by": model.created_by,
    }


@router.post("/v1/memories", status_code=status.HTTP_201_CREATED)
async def admit_memory(payload: AdmitBody, memories: ServiceDep) -> dict[str, Any]:
    model = await memories.admit(AdmitMemory(**payload.model_dump()))
    return _serialize(model)


@router.get("/v1/memories")
async def list_memories(org_key: str, memories: ServiceDep) -> list[dict[str, Any]]:
    return [_serialize(row) for row in await memories.list_org(org_key)]


@router.post("/v1/memories/{memory_id}/verify")
async def verify_memory(
    memory_id: uuid.UUID, payload: ActorBody, memories: ServiceDep
) -> dict[str, Any]:
    return _serialize(await memories.verify(memory_id, actor=payload.actor))


@router.post("/v1/memories/{memory_id}/revoke")
async def revoke_memory(
    memory_id: uuid.UUID, payload: RevokeBody, memories: ServiceDep
) -> dict[str, Any]:
    return _serialize(await memories.revoke(memory_id, actor=payload.actor, reason=payload.reason))
