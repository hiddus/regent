"""P2-2 runtime profile HTTP API."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.runtime_profile_service import (
    RuntimeProfileService,
    UpsertRuntimeProfile,
)

router = APIRouter(tags=["runtime-profiles"])


def service(request: Request) -> RuntimeProfileService:
    return RuntimeProfileService(request.app.state.sessions)


ServiceDep = Annotated[RuntimeProfileService, Depends(service)]


class UpsertProfileBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    version: str = Field(default="1", min_length=1, max_length=64)
    status: str = Field(min_length=1, max_length=32)
    abi_json: dict[str, Any] = Field(default_factory=dict)
    sandbox_image: str | None = None
    resolver_image: str | None = None
    actor: str = Field(default="regent-core", min_length=1)


def _serialize(model: Any) -> dict[str, Any]:
    return {
        "id": str(model.id),
        "name": model.name,
        "version": model.version,
        "status": model.status,
        "abi_json": model.abi_json,
        "sandbox_image": model.sandbox_image,
        "resolver_image": model.resolver_image,
        "content_hash": model.content_hash,
        "created_by": model.created_by,
    }


@router.post("/v1/runtime-profiles", status_code=status.HTTP_201_CREATED)
async def upsert_profile(payload: UpsertProfileBody, profiles: ServiceDep) -> dict[str, Any]:
    model = await profiles.upsert(UpsertRuntimeProfile(**payload.model_dump()))
    return _serialize(model)


@router.get("/v1/runtime-profiles")
async def list_profiles(profiles: ServiceDep) -> list[dict[str, Any]]:
    rows = await profiles.list_profiles()
    return [_serialize(row) for row in rows]


@router.get("/v1/runtime-profiles/{name}")
async def get_certified(name: str, profiles: ServiceDep, version: str = "1") -> dict[str, Any]:
    model = await profiles.require_certified(name, version)
    return _serialize(model)
