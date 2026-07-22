import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.side_effect_service import AttemptReceipt, SideEffectService

router = APIRouter(prefix="/v1/side-effects", tags=["governance"])


class CompleteAttemptRequest(BaseModel):
    outcome: Literal["SUCCEEDED", "FAILED", "UNKNOWN"]
    external_request_id: str | None = Field(default=None, max_length=255)
    result: dict[str, Any] = Field(default_factory=dict)


class ReconcileAttemptRequest(BaseModel):
    final_outcome: Literal["SUCCEEDED", "FAILED"]
    evidence: dict[str, Any]


def service(request: Request) -> SideEffectService:
    return SideEffectService(request.app.state.sessions)


SideEffectDep = Annotated[SideEffectService, Depends(service)]


@router.post(
    "/permit/{permit_id}/start", response_model=AttemptReceipt, status_code=status.HTTP_201_CREATED
)
async def start_attempt(permit_id: uuid.UUID, attempts: SideEffectDep) -> AttemptReceipt:
    return await attempts.start(permit_id)


@router.post("/{attempt_id}/complete", response_model=AttemptReceipt)
async def complete_attempt(
    attempt_id: uuid.UUID, payload: CompleteAttemptRequest, attempts: SideEffectDep
) -> AttemptReceipt:
    return await attempts.complete(attempt_id, **payload.model_dump())


@router.post("/{attempt_id}/reconcile", response_model=AttemptReceipt)
async def reconcile_attempt(
    attempt_id: uuid.UUID, payload: ReconcileAttemptRequest, attempts: SideEffectDep
) -> AttemptReceipt:
    return await attempts.reconcile(attempt_id, **payload.model_dump())
