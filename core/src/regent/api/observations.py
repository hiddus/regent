import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.observation_service import ObservationInput, ObservationService
from regent.config import get_settings
from regent.model import ModelConfigurationError

router = APIRouter(prefix="/v1/observations", tags=["observations"])


class ObservationRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=255)
    goal_id: uuid.UUID | None = None
    metric_name: str = Field(min_length=1, max_length=255)
    metric_value: dict[str, Any]
    source: str = Field(min_length=1, max_length=255)
    definition_version: str = Field(min_length=1, max_length=128)
    signature: str = Field(min_length=64, max_length=64)
    is_bot: bool = False
    is_internal: bool = False
    observed_at: datetime


class ExperienceRequest(BaseModel):
    goal_id: uuid.UUID
    observation_ids: list[uuid.UUID] = Field(min_length=1)
    outcome: str = Field(min_length=1, max_length=64)
    lesson: str = Field(min_length=1)
    replan_triggered: bool = False
    attribution: dict[str, Any] = Field(default_factory=dict)


def service(request: Request) -> ObservationService:
    key = get_settings().observation_signing_key
    if key is None:
        raise ModelConfigurationError("observation signing key is not configured")
    return ObservationService(request.app.state.sessions, key.get_secret_value())


ObservationDep = Annotated[ObservationService, Depends(service)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def ingest(payload: ObservationRequest, observations: ObservationDep) -> dict[str, uuid.UUID]:
    values = payload.model_dump(exclude={"signature"})
    return {
        "observation_id": await observations.ingest(ObservationInput(**values), payload.signature)
    }


@router.post("/experiences", status_code=status.HTTP_201_CREATED)
async def create_experience(
    payload: ExperienceRequest, observations: ObservationDep
) -> dict[str, uuid.UUID]:
    return {"experience_record_id": await observations.create_experience(**payload.model_dump())}
