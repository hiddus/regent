"""API for PenguinHarness-style harness evolution (skill LESSONS overlays)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.harness_evolution import (
    HarnessEvolutionService,
)
from regent.config import get_settings
from regent.model.factory import build_model_provider

router = APIRouter(prefix="/v1/harness-evolution", tags=["harness-evolution"])


def service(request: Request) -> HarnessEvolutionService:
    settings = get_settings()
    return HarnessEvolutionService(
        build_model_provider(settings),
        workspace_root=Path(settings.workspace_root),
    )


ServiceDep = Annotated[HarnessEvolutionService, Depends(service)]


class EvolveHarnessBody(BaseModel):
    gaps: list[str] = Field(min_length=1, max_length=40)
    actor: str = Field(min_length=1, max_length=255)
    goal_context: str = Field(default="", max_length=20_000)
    preview_url: str | None = Field(default=None, max_length=2048)
    preferred_skill_id: str | None = Field(default=None, max_length=64)


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def evolve_harness(
    payload: EvolveHarnessBody,
    evolution: ServiceDep,
) -> dict[str, Any]:
    receipt = await evolution.evolve_from_gaps(**payload.model_dump())
    return receipt.as_dict()
