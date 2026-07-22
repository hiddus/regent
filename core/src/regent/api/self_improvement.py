import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

import regent
from regent.application.self_improvement_service import (
    SelfImprovementReceipt,
    SelfImprovementService,
)
from regent.config import get_settings
from regent.model.factory import build_model_provider

router = APIRouter(prefix="/v1/self-improvement-runs", tags=["self-improvement"])


def service(request: Request) -> SelfImprovementService:
    settings = get_settings()
    return SelfImprovementService(
        request.app.state.sessions,
        build_model_provider(settings),
        Path(regent.__file__ or "").resolve().parent,
        Path(settings.workspace_root) / "self-improvement",
    )


ServiceDep = Annotated[SelfImprovementService, Depends(service)]


class ProposeImprovementBody(BaseModel):
    primary_problem: str = Field(min_length=1, max_length=10_000)
    hypothesis: str = Field(min_length=1, max_length=10_000)
    target_file: str = Field(min_length=1, max_length=1024)
    actor: str = Field(min_length=1, max_length=255)


class ImprovementDecisionBody(BaseModel):
    decision: str = Field(pattern=r"^(APPROVE|REJECT)$")
    actor: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=10_000)


@router.post("", response_model=SelfImprovementReceipt, status_code=status.HTTP_201_CREATED)
async def propose_improvement(
    payload: ProposeImprovementBody,
    improvements: ServiceDep,
) -> SelfImprovementReceipt:
    return await improvements.propose(**payload.model_dump())


@router.get("/{run_id}", response_model=SelfImprovementReceipt)
async def get_improvement(
    run_id: uuid.UUID,
    improvements: ServiceDep,
) -> SelfImprovementReceipt:
    return await improvements.get(run_id)


@router.post("/{run_id}/decision", response_model=SelfImprovementReceipt)
async def decide_improvement(
    run_id: uuid.UUID,
    payload: ImprovementDecisionBody,
    improvements: ServiceDep,
) -> SelfImprovementReceipt:
    return await improvements.decide(
        run_id,
        approve=payload.decision == "APPROVE",
        actor=payload.actor,
        reason=payload.reason,
    )
