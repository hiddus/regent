import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.goal_execution_service import GoalExecutionReceipt, GoalExecutionService
from regent.application.goal_interpreter import GoalInterpreter
from regent.application.goal_service import CreateGoal, GoalService
from regent.application.organization_service import OrganizationReceipt, OrganizationService
from regent.application.planning_service import PlanningService, PlanReceipt
from regent.application.transition_service import TransitionContext, TransitionService
from regent.config import get_settings
from regent.domain.transitions import GoalCommand
from regent.model.factory import build_model_provider

router = APIRouter(prefix="/v1/goals", tags=["goals"])


class CreateGoalRequest(BaseModel):
    original_input: str = Field(min_length=1, max_length=20_000)
    created_by: str = Field(min_length=1, max_length=255)
    explicit_constraints: dict[str, Any] = Field(default_factory=dict)
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InterpretGoalRequest(BaseModel):
    original_input: str = Field(min_length=1, max_length=20_000)
    created_by: str = Field(min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TransitionGoalRequest(BaseModel):
    command: GoalCommand
    expected_version: int = Field(ge=0)
    actor: str = Field(min_length=1, max_length=255)
    causation_id: uuid.UUID | None = None

class StartGoalRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=255)



class GoalResponse(BaseModel):
    id: uuid.UUID
    original_input: str
    status: str
    version: int
    created_by: str
    correlation_id: uuid.UUID
    metadata: dict[str, Any]
    spec_version: int
    spec_status: str
    spec_hash: str


def goal_service(request: Request) -> GoalService:
    return GoalService(request.app.state.sessions)


def transition_service(request: Request) -> TransitionService:
    return TransitionService(request.app.state.sessions)


GoalServiceDep = Annotated[GoalService, Depends(goal_service)]
TransitionServiceDep = Annotated[TransitionService, Depends(transition_service)]


def _response(goal: Any) -> GoalResponse:
    specs = sorted(goal.specs, key=lambda spec: spec.version)
    return GoalResponse(
        id=goal.id,
        original_input=goal.original_input,
        status=goal.status,
        version=goal.version,
        created_by=goal.created_by,
        correlation_id=goal.correlation_id,
        metadata=goal.metadata_json,
        spec_version=specs[-1].version,
        spec_status=specs[-1].status,
        spec_hash=specs[-1].content_hash,
    )


@router.post("", response_model=GoalResponse, status_code=status.HTTP_201_CREATED)
async def create_goal(payload: CreateGoalRequest, service: GoalServiceDep) -> GoalResponse:
    goal = await service.create(CreateGoal(**payload.model_dump()))
    return _response(await service.get(goal.id))


@router.post("/interpret", response_model=GoalResponse, status_code=status.HTTP_201_CREATED)
async def interpret_goal(payload: InterpretGoalRequest, service: GoalServiceDep) -> GoalResponse:
    interpreted = await GoalInterpreter(build_model_provider(get_settings())).interpret(
        payload.original_input
    )
    result = interpreted.output
    goal = await service.create(
        CreateGoal(
            original_input=payload.original_input,
            created_by=payload.created_by,
            explicit_constraints=result.explicit_constraints,
            success_criteria=result.success_criteria,
            metadata={
                **payload.metadata,
                "interpretation_model": interpreted.model,
                "interpretation_usage": {
                    "input_tokens": interpreted.usage.input_tokens,
                    "output_tokens": interpreted.usage.output_tokens,
                },
                "interpreted_objective": result.objective or payload.original_input,
            },
            system_inferences=result.system_inferences,
            unknowns=[item.model_dump() for item in result.unknowns],
        )
    )
    return _response(await service.get(goal.id))


@router.get("/{goal_id}", response_model=GoalResponse)
async def get_goal(goal_id: uuid.UUID, service: GoalServiceDep) -> GoalResponse:
    return _response(await service.get(goal_id))


@router.post("/{goal_id}/organize", response_model=OrganizationReceipt)
@router.post(
    "/{goal_id}/start",
    response_model=GoalExecutionReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_goal(
    goal_id: uuid.UUID, payload: StartGoalRequest, request: Request
) -> GoalExecutionReceipt:
    return await GoalExecutionService(request.app.state.sessions).start(
        goal_id, actor=payload.actor, idempotency_key=payload.idempotency_key
    )

async def organize_goal(goal_id: uuid.UUID, request: Request) -> OrganizationReceipt:
    return await OrganizationService(request.app.state.sessions).organize(goal_id)


@router.post("/{goal_id}/plan", response_model=PlanReceipt)
async def plan_goal(goal_id: uuid.UUID, request: Request) -> PlanReceipt:
    return await PlanningService(
        request.app.state.sessions,
        build_model_provider(get_settings()),
    ).plan(goal_id)


@router.post("/{goal_id}/transitions", response_model=GoalResponse)
async def transition_goal(
    goal_id: uuid.UUID,
    payload: TransitionGoalRequest,
    transitions: TransitionServiceDep,
    goals: GoalServiceDep,
) -> GoalResponse:
    goal = await goals.get(goal_id)
    await transitions.transition_goal(
        TransitionContext(
            aggregate_id=goal_id,
            expected_version=payload.expected_version,
            actor=payload.actor,
            correlation_id=goal.correlation_id,
            causation_id=payload.causation_id,
        ),
        payload.command,
    )
    return _response(await goals.get(goal_id))
