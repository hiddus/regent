import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.discovery_round_service import DiscoveryRoundService, RequestDiscoveryRound

router = APIRouter(tags=["product-creation"])


class RequestDiscoveryRoundBody(BaseModel):
    budget: dict[str, int | float] = Field(default_factory=dict)
    policy_version: str = Field(default="product-discovery-v1", min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=255)
    actor: str = Field(min_length=1, max_length=255)
    correlation_id: str = Field(min_length=1, max_length=255)


class DiscoveryRoundResponse(BaseModel):
    id: uuid.UUID
    goal_id: uuid.UUID
    round: int
    status: str
    version: int
    input_snapshot_hash: str
    budget: dict[str, Any]
    policy_version: str
    correlation_id: str
    failure_code: str | None


class HypothesisResponse(BaseModel):
    id: uuid.UUID
    round_id: uuid.UUID
    candidate_key: str
    content: dict[str, Any]
    content_hash: str
    eligibility: str
    invalid_reasons: list[str]
    generator_ref: str


class DecisionResponse(BaseModel):
    id: uuid.UUID
    round_id: uuid.UUID
    decision: str
    selected_hypothesis_id: uuid.UUID | None
    rationale: str
    evidence_digest: str
    policy_version: str
    created_by: str


def service(request: Request) -> DiscoveryRoundService:
    return DiscoveryRoundService(request.app.state.sessions)


ServiceDep = Annotated[DiscoveryRoundService, Depends(service)]


def round_response(model: Any) -> DiscoveryRoundResponse:
    return DiscoveryRoundResponse(
        id=model.id,
        goal_id=model.goal_id,
        round=model.round,
        status=model.status,
        version=model.version,
        input_snapshot_hash=model.input_snapshot_hash,
        budget=model.budget,
        policy_version=model.policy_version,
        correlation_id=model.correlation_id,
        failure_code=model.failure_code,
    )


@router.post(
    "/v1/goals/{goal_id}/discovery-rounds",
    response_model=DiscoveryRoundResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_discovery_round(
    goal_id: uuid.UUID, payload: RequestDiscoveryRoundBody, rounds: ServiceDep
) -> DiscoveryRoundResponse:
    model = await rounds.request(RequestDiscoveryRound(goal_id=goal_id, **payload.model_dump()))
    return round_response(model)


@router.get("/v1/discovery-rounds/{round_id}", response_model=DiscoveryRoundResponse)
async def get_discovery_round(round_id: uuid.UUID, rounds: ServiceDep) -> DiscoveryRoundResponse:
    return round_response(await rounds.get(round_id))


@router.get("/v1/discovery-rounds/{round_id}/hypotheses", response_model=list[HypothesisResponse])
async def get_hypotheses(round_id: uuid.UUID, rounds: ServiceDep) -> list[HypothesisResponse]:
    return [
        HypothesisResponse(
            id=item.id,
            round_id=item.round_id,
            candidate_key=item.candidate_key,
            content=item.content_json,
            content_hash=item.content_hash,
            eligibility=item.eligibility,
            invalid_reasons=item.invalid_reasons,
            generator_ref=item.generator_ref,
        )
        for item in await rounds.hypotheses(round_id)
    ]


@router.get("/v1/discovery-rounds/{round_id}/decision", response_model=DecisionResponse)
async def get_decision(round_id: uuid.UUID, rounds: ServiceDep) -> DecisionResponse:
    item = await rounds.decision(round_id)
    return DecisionResponse(
        id=item.id,
        round_id=item.round_id,
        decision=item.decision,
        selected_hypothesis_id=item.selected_hypothesis_id,
        rationale=item.rationale,
        evidence_digest=item.evidence_digest,
        policy_version=item.policy_version,
        created_by=item.created_by,
    )
