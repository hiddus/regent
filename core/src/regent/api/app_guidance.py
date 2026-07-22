import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from regent.application.app_guidance_service import AppGuidanceService, GuidanceReceipt
from regent.config import get_settings
from regent.model.factory import build_model_provider

router = APIRouter(prefix="/v1/app-projects", tags=["app-guidance"])


def service(request: Request) -> AppGuidanceService:
    return AppGuidanceService(
        request.app.state.sessions,
        build_model_provider(get_settings()),
    )


ServiceDep = Annotated[AppGuidanceService, Depends(service)]


class GuideAppBody(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    actor: str = Field(min_length=1, max_length=255)


@router.post("/{project_id}/guidance", response_model=GuidanceReceipt)
async def guide_app(
    project_id: uuid.UUID,
    payload: GuideAppBody,
    guidance: ServiceDep,
) -> GuidanceReceipt:
    return await guidance.guide(
        project_id,
        message=payload.message,
        actor=payload.actor,
    )


@router.get("/{project_id}/status", response_model=dict[str, Any])
async def app_status(
    project_id: uuid.UUID,
    guidance: ServiceDep,
) -> dict[str, Any]:
    return await guidance.status(project_id)
