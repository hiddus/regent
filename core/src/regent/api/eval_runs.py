"""P2-4 eval harness HTTP API."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.eval_harness_service import CreateEvalRun, EvalHarnessService

router = APIRouter(tags=["eval-runs"])


def service(request: Request) -> EvalHarnessService:
    return EvalHarnessService(request.app.state.sessions)


ServiceDep = Annotated[EvalHarnessService, Depends(service)]


class CreateEvalBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    task_set: dict[str, Any]
    baseline: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    seed: str = Field(min_length=1, max_length=64)
    actor: str = Field(default="regent-core", min_length=1)


class ActorBody(BaseModel):
    actor: str = Field(min_length=1)


class DecideBody(BaseModel):
    actor: str = Field(min_length=1)
    promote: bool | None = None


def _serialize(model: Any) -> dict[str, Any]:
    scores = model.scores_json or {}
    metrics = model.metrics_json or {}
    return {
        "id": str(model.id),
        "name": model.name,
        "status": model.status,
        "task_set_hash": model.task_set_hash,
        "seed": model.seed,
        "scores": scores,
        "evidence_digest": scores.get("evidence_digest"),
        "scoring_mode": scores.get("scoring_mode"),
        "decision": model.decision,
        "decision_rationale": model.decision_rationale,
        "product_decision_record": metrics.get("product_decision_record"),
        "org_adaptive_status": metrics.get("org_adaptive_status", "ROLLOUT_NOT_ALLOWED"),
        "policy_version": model.policy_version,
        "created_by": model.created_by,
    }


@router.post("/v1/eval-runs", status_code=status.HTTP_201_CREATED)
async def create_eval(payload: CreateEvalBody, harness: ServiceDep) -> dict[str, Any]:
    model = await harness.create(
        CreateEvalRun(
            name=payload.name,
            task_set=payload.task_set,
            baseline=payload.baseline,
            budget=payload.budget,
            seed=payload.seed,
            actor=payload.actor,
        )
    )
    return _serialize(model)


@router.get("/v1/eval-runs")
async def list_evals(harness: ServiceDep) -> list[dict[str, Any]]:
    return [_serialize(row) for row in await harness.list_runs()]


@router.get("/v1/eval-runs/{eval_run_id}")
async def get_eval(eval_run_id: uuid.UUID, harness: ServiceDep) -> dict[str, Any]:
    return _serialize(await harness.get(eval_run_id))


@router.post("/v1/eval-runs/{eval_run_id}/freeze")
async def freeze_eval(
    eval_run_id: uuid.UUID, payload: ActorBody, harness: ServiceDep
) -> dict[str, Any]:
    return _serialize(await harness.freeze(eval_run_id, actor=payload.actor))


@router.post("/v1/eval-runs/{eval_run_id}/run")
async def run_eval(
    eval_run_id: uuid.UUID, payload: ActorBody, harness: ServiceDep
) -> dict[str, Any]:
    return _serialize(await harness.run_and_score(eval_run_id, actor=payload.actor))


@router.post("/v1/eval-runs/{eval_run_id}/decide")
async def decide_eval(
    eval_run_id: uuid.UUID, payload: DecideBody, harness: ServiceDep
) -> dict[str, Any]:
    return _serialize(
        await harness.decide(eval_run_id, actor=payload.actor, promote=payload.promote)
    )
