import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.feedback_service import (
    BindMetricDefinition,
    CreateIterationDecision,
    FeedbackService,
    MetricDefinition,
)
from regent.application.iteration_loop_service import IterationLoopService

router = APIRouter(tags=["feedback-loop"])


def service(request: Request) -> FeedbackService:
    return FeedbackService(request.app.state.sessions)


ServiceDep = Annotated[FeedbackService, Depends(service)]


class BindMetricBody(BaseModel):
    goal_id: uuid.UUID
    definition: MetricDefinition
    actor: str = Field(min_length=1)


class EvaluateGateBody(BaseModel):
    deployment_id: uuid.UUID
    actor: str = Field(min_length=1)


class DecideIterationBody(BaseModel):
    actor: str = Field(min_length=1)
    primary_hypothesis: str | None = Field(default=None, min_length=1)
    new_work_id: uuid.UUID | None = None


class MetricBindingResponse(BaseModel):
    id: uuid.UUID
    goal_id: uuid.UUID
    deployment_id: uuid.UUID
    metric_key: str
    definition_version: str
    definition_hash: str


class GateEvaluationResponse(BaseModel):
    id: uuid.UUID
    goal_id: uuid.UUID
    deployment_id: uuid.UUID
    status: str
    policy_version: str
    result: dict[str, Any]
    evidence_digest: str


class IterationDecisionResponse(BaseModel):
    id: uuid.UUID
    goal_id: uuid.UUID
    gate_evaluation_id: uuid.UUID
    decision: str
    rationale: str
    primary_hypothesis: str | None
    new_work_id: uuid.UUID | None
    evidence_digest: str
    policy_version: str


@router.post(
    "/v1/deployments/{deployment_id}/metric-bindings",
    response_model=MetricBindingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bind_metric(
    deployment_id: uuid.UUID, payload: BindMetricBody, feedback: ServiceDep
) -> MetricBindingResponse:
    model = await feedback.bind_metric(
        BindMetricDefinition(deployment_id=deployment_id, **payload.model_dump())
    )
    return MetricBindingResponse.model_validate(model, from_attributes=True)


@router.post(
    "/v1/goals/{goal_id}/gate-evaluations",
    response_model=GateEvaluationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def evaluate_gate(
    goal_id: uuid.UUID, payload: EvaluateGateBody, feedback: ServiceDep
) -> GateEvaluationResponse:
    return gate_response(
        await feedback.evaluate(goal_id, payload.deployment_id, actor=payload.actor)
    )


@router.get("/v1/gate-evaluations/{gate_id}", response_model=GateEvaluationResponse)
async def get_gate(gate_id: uuid.UUID, feedback: ServiceDep) -> GateEvaluationResponse:
    return gate_response(await feedback.get_gate(gate_id))


@router.post(
    "/v1/gate-evaluations/{gate_id}/iteration-decisions",
    response_model=IterationDecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def decide_iteration(
    gate_id: uuid.UUID, payload: DecideIterationBody, feedback: ServiceDep
) -> IterationDecisionResponse:
    model = await feedback.decide(
        CreateIterationDecision(gate_evaluation_id=gate_id, **payload.model_dump())
    )
    return IterationDecisionResponse.model_validate(model, from_attributes=True)


@router.get(
    "/v1/goals/{goal_id}/iteration-decisions",
    response_model=list[IterationDecisionResponse],
)
async def list_iteration_decisions(
    goal_id: uuid.UUID, feedback: ServiceDep
) -> list[IterationDecisionResponse]:
    return [
        IterationDecisionResponse.model_validate(item, from_attributes=True)
        for item in await feedback.list_decisions(goal_id)
    ]


@router.post(
    "/v1/iteration-decisions/{decision_id}/revise",
    status_code=status.HTTP_201_CREATED,
)
async def trigger_revise(
    decision_id: uuid.UUID, request: Request
) -> dict[str, Any]:
    """Trigger a REVISE iteration: create new DiscoveryRound and re-enter chain."""
    sessions = request.app.state.sessions
    loop_service = IterationLoopService(sessions)
    round_id = await loop_service.handle_revise(decision_id)
    return {"discovery_round_id": round_id}


def gate_response(model: Any) -> GateEvaluationResponse:
    return GateEvaluationResponse(
        id=model.id,
        goal_id=model.goal_id,
        deployment_id=model.deployment_id,
        status=model.status,
        policy_version=model.policy_version,
        result=model.result_json,
        evidence_digest=model.evidence_digest,
    )
