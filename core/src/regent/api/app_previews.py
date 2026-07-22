import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.app_preview_service import AppPreviewService, PreviewReceipt
from regent.application.observation_service import ObservationInput, ObservationService
from regent.config import get_settings
from regent.infrastructure.models import AppPreviewReleaseModel
from regent.model.factory import build_model_provider

router = APIRouter(tags=["app-preview"])


def service(request: Request) -> AppPreviewService:
    settings = get_settings()
    return AppPreviewService(
        request.app.state.sessions,
        build_model_provider(settings),
        Path(settings.workspace_root) / "previews",
    )


ServiceDep = Annotated[AppPreviewService, Depends(service)]


class GeneratePreviewBody(BaseModel):
    actor: str = Field(min_length=1, max_length=255)


@router.post(
    "/v1/app-projects/{project_id}/goals/{goal_id}/preview-releases",
    response_model=PreviewReceipt,
    status_code=status.HTTP_201_CREATED,
)
async def generate_preview(
    project_id: uuid.UUID,
    goal_id: uuid.UUID,
    payload: GeneratePreviewBody,
    previews: ServiceDep,
) -> PreviewReceipt:
    return await previews.generate(project_id, goal_id, actor=payload.actor)


@router.get("/v1/preview-releases/{release_id}", response_model=PreviewReceipt)
async def get_preview(
    release_id: uuid.UUID,
    previews: ServiceDep,
) -> PreviewReceipt:
    return await previews.get(release_id)


class PreviewEventBody(BaseModel):
    event_id: str = Field(min_length=1, max_length=255)
    event_name: str = Field(pattern=r"^activation$")


class PreviewEvaluationBody(BaseModel):
    minimum_samples: int = Field(default=5, ge=1, le=10_000)
    activation_threshold: int = Field(default=3, ge=1, le=10_000)
    actor: str = Field(min_length=1, max_length=255)


@router.post("/v1/preview-releases/{release_id}/events", status_code=status.HTTP_201_CREATED)
async def ingest_preview_event(
    release_id: uuid.UUID,
    payload: PreviewEventBody,
    request: Request,
) -> dict[str, uuid.UUID]:
    async with request.app.state.sessions() as session:
        release = await session.get(AppPreviewReleaseModel, release_id)
    if release is None or release.status != "PREVIEW_READY":
        from regent.domain.errors import DomainError, ErrorCode

        raise DomainError(ErrorCode.INVALID_STATE, "ready preview release is required")
    settings = get_settings()
    if settings.observation_signing_key is None:
        from regent.model import ModelConfigurationError

        raise ModelConfigurationError("observation signing key is not configured")
    observations = ObservationService(
        request.app.state.sessions,
        settings.observation_signing_key.get_secret_value(),
    )
    item = ObservationInput(
        event_id=f"preview:{release_id}:{payload.event_id}",
        goal_id=release.goal_id,
        metric_name=payload.event_name,
        metric_value={"value": 1},
        source=f"preview:{release_id}",
        definition_version="preview-activation-v1",
        is_bot=False,
        is_internal=False,
        observed_at=datetime.now(UTC),
    )
    return {"observation_id": await observations.ingest(item, observations.sign(item))}


@router.post("/v1/preview-releases/{release_id}/evaluate")
async def evaluate_preview(
    release_id: uuid.UUID,
    payload: PreviewEvaluationBody,
    previews: ServiceDep,
) -> dict[str, object]:
    gate = await previews.evaluate(
        release_id,
        minimum_samples=payload.minimum_samples,
        activation_threshold=payload.activation_threshold,
        actor=payload.actor,
    )
    return {
        "gate_evaluation_id": gate.id,
        "goal_id": gate.goal_id,
        "status": gate.status,
        "result": gate.result_json,
        "evidence_digest": gate.evidence_digest,
    }
