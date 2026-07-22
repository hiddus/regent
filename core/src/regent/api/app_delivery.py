import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.build_service import (
    BuildService,
    RequestAppBuild,
    RequestDependencyResolution,
)
from regent.application.generation_service import (
    CreateGenerationPlan,
    GenerationService,
    RequestGenerationRun,
)
from regent.application.p1_contracts import GenerationPlanContract
from regent.application.permit_service import PermitService
from regent.application.release_service import (
    CreateReleaseCandidate,
    ReleaseService,
    RequestDeployment,
)
from regent.config import get_settings
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.code_generator import ArtifactBackedCodeGenerator, ArtifactUriResolver
from regent.infrastructure.deployment import StaticPreviewDeploymentProvider
from regent.infrastructure.sandbox import DockerDependencyMaterializer, DockerSandboxDriver
from regent.infrastructure.workspace_writer import WorkspaceWriter
from regent.model.factory import build_model_provider

router = APIRouter(tags=["app-delivery"])


class CreateGenerationPlanBody(BaseModel):
    requirement_revision_id: uuid.UUID
    contract: GenerationPlanContract
    architecture_summary: str = Field(min_length=1)
    component_plan: list[dict[str, Any]] = Field(default_factory=list)
    actor: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class RequestGenerationRunBody(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    correlation_id: str = Field(min_length=1, max_length=255)


class GenerationPlanResponse(BaseModel):
    id: uuid.UUID
    status: str
    version: int
    input_digest: str


class GenerationRunResponse(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    attempt: int
    status: str
    version: int
    correlation_id: str
    model_ref: str | None
    failure_code: str | None


def service(request: Request) -> GenerationService:
    settings = get_settings()
    artifacts = FileArtifactStore(Path(settings.artifact_root))
    return GenerationService(
        request.app.state.sessions,
        ArtifactBackedCodeGenerator(build_model_provider(settings), artifacts),
        WorkspaceWriter(Path(settings.workspace_root), ArtifactUriResolver(artifacts.root)),
    )


ServiceDep = Annotated[GenerationService, Depends(service)]


@router.post(
    "/v1/resolution-plans/{resolution_plan_id}/generation-plans",
    response_model=GenerationPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_generation_plan(
    resolution_plan_id: uuid.UUID,
    payload: CreateGenerationPlanBody,
    generation: ServiceDep,
) -> GenerationPlanResponse:
    model = await generation.create_plan(
        CreateGenerationPlan(
            capability_resolution_plan_id=resolution_plan_id,
            **payload.model_dump(),
        )
    )
    return GenerationPlanResponse(
        id=model.id,
        status=model.status,
        version=model.version,
        input_digest=model.input_digest,
    )


@router.post(
    "/v1/generation-plans/{plan_id}/runs",
    response_model=GenerationRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_generation_run(
    plan_id: uuid.UUID,
    payload: RequestGenerationRunBody,
    generation: ServiceDep,
) -> GenerationRunResponse:
    model = await generation.request_run(
        RequestGenerationRun(plan_id=plan_id, **payload.model_dump())
    )
    return run_response(model)


@router.get("/v1/generation-runs/{run_id}", response_model=GenerationRunResponse)
async def get_generation_run(run_id: uuid.UUID, generation: ServiceDep) -> GenerationRunResponse:
    return run_response(await generation.get_run(run_id))


def run_response(model: Any) -> GenerationRunResponse:
    return GenerationRunResponse(
        id=model.id,
        plan_id=model.plan_id,
        attempt=model.attempt,
        status=model.status,
        version=model.version,
        correlation_id=model.correlation_id,
        model_ref=model.model_ref,
        failure_code=model.failure_code,
    )


class RequestDependenciesBody(BaseModel):
    dependency_intents: list[dict[str, Any]] = Field(default_factory=list)
    idempotency_key: str = Field(min_length=1, max_length=255)
    correlation_id: str = Field(min_length=1, max_length=255)


class RequestBuildBody(BaseModel):
    dependency_resolution_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=255)
    correlation_id: str = Field(min_length=1, max_length=255)


class DependencyResolutionResponse(BaseModel):
    id: uuid.UUID
    workspace_snapshot_id: uuid.UUID
    status: str
    version: int
    failure_code: str | None


class AppBuildResponse(BaseModel):
    id: uuid.UUID
    workspace_snapshot_id: uuid.UUID
    dependency_resolution_id: uuid.UUID
    status: str
    version: int
    reconciliation_required: bool
    failure_code: str | None


def build_service(request: Request) -> BuildService:
    settings = get_settings()
    permits = PermitService(request.app.state.sessions)

    async def validate_permit(permit_id: str, action: str) -> None:
        claimed = await permits.claim(uuid.UUID(permit_id), actor_id="dependency-resolver")
        if claimed.binding.action != action:
            raise ValueError("permit action mismatch")
        await permits.consume(claimed.id, nonce=claimed.nonce)

    root = Path(settings.build_root)
    return BuildService(
        request.app.state.sessions,
        DockerDependencyMaterializer(
            root=root / "dependencies",
            image=settings.dependency_resolver_image,
            egress_proxy=settings.dependency_egress_proxy,
            permit_validator=validate_permit,
        ),
        DockerSandboxDriver(root=root / "sandbox", image=settings.sandbox_image),
    )


BuildServiceDep = Annotated[BuildService, Depends(build_service)]


@router.post(
    "/v1/workspace-snapshots/{snapshot_id}/dependency-resolutions",
    response_model=DependencyResolutionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_dependency_resolution(
    snapshot_id: uuid.UUID, payload: RequestDependenciesBody, builds: BuildServiceDep
) -> DependencyResolutionResponse:
    model = await builds.request_dependencies(
        RequestDependencyResolution(workspace_snapshot_id=snapshot_id, **payload.model_dump())
    )
    return DependencyResolutionResponse(
        id=model.id,
        workspace_snapshot_id=model.workspace_snapshot_id,
        status=model.status,
        version=model.version,
        failure_code=model.failure_code,
    )


@router.post(
    "/v1/workspace-snapshots/{snapshot_id}/builds",
    response_model=AppBuildResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_app_build(
    snapshot_id: uuid.UUID, payload: RequestBuildBody, builds: BuildServiceDep
) -> AppBuildResponse:
    model = await builds.request_build(
        RequestAppBuild(workspace_snapshot_id=snapshot_id, **payload.model_dump())
    )
    return build_response(model)


@router.get("/v1/app-builds/{build_id}", response_model=AppBuildResponse)
async def get_app_build(build_id: uuid.UUID, builds: BuildServiceDep) -> AppBuildResponse:
    return build_response(await builds.get_build(build_id))


def build_response(model: Any) -> AppBuildResponse:
    return AppBuildResponse(
        id=model.id,
        workspace_snapshot_id=model.workspace_snapshot_id,
        dependency_resolution_id=model.dependency_resolution_id,
        status=model.status,
        version=model.version,
        reconciliation_required=model.reconciliation_required,
        failure_code=model.failure_code,
    )


@router.post("/v1/app-builds/{build_id}/reconcile", response_model=AppBuildResponse)
async def reconcile_app_build(build_id: uuid.UUID, builds: BuildServiceDep) -> AppBuildResponse:
    return build_response(await builds.reconcile_build(build_id))


class CreateReleaseCandidateBody(BaseModel):
    actor: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    human_task_id: uuid.UUID | None = None


class ReleaseDecisionBody(BaseModel):
    decision: str = Field(pattern=r"^(APPROVE|REJECT)$")
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RequestDeploymentBody(BaseModel):
    permit_id: uuid.UUID
    environment: str = Field(default="preview", pattern=r"^preview$")
    idempotency_key: str = Field(min_length=1, max_length=255)
    correlation_id: str = Field(min_length=1, max_length=255)


class ReleaseCandidateResponse(BaseModel):
    id: uuid.UUID
    app_build_id: uuid.UUID
    status: str
    version: int
    content_hash: str
    human_task_id: uuid.UUID | None


class DeploymentResponse(BaseModel):
    id: uuid.UUID
    release_candidate_id: uuid.UUID
    environment: str
    status: str
    version: int
    endpoint: str | None
    reconciliation_required: bool
    failure_code: str | None


def release_service(request: Request) -> ReleaseService:
    settings = get_settings()
    preview_root = Path(settings.workspace_root) / "previews"
    provider = StaticPreviewDeploymentProvider(preview_root=preview_root, base_url="")
    return ReleaseService(request.app.state.sessions, provider)


ReleaseServiceDep = Annotated[ReleaseService, Depends(release_service)]


@router.post(
    "/v1/app-builds/{build_id}/release-candidates",
    response_model=ReleaseCandidateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_release_candidate(
    build_id: uuid.UUID,
    payload: CreateReleaseCandidateBody,
    releases: ReleaseServiceDep,
) -> ReleaseCandidateResponse:
    model = await releases.create_candidate(
        CreateReleaseCandidate(app_build_id=build_id, **payload.model_dump())
    )
    return candidate_response(model)


@router.post(
    "/v1/release-candidates/{candidate_id}/decision",
    response_model=ReleaseCandidateResponse,
)
async def decide_release_candidate(
    candidate_id: uuid.UUID,
    payload: ReleaseDecisionBody,
    releases: ReleaseServiceDep,
) -> ReleaseCandidateResponse:
    if payload.decision == "APPROVE":
        model = await releases.approve(candidate_id, actor=payload.actor, reason=payload.reason)
    else:
        model = await releases.reject(candidate_id, actor=payload.actor, reason=payload.reason)
    return candidate_response(model)


@router.post(
    "/v1/release-candidates/{candidate_id}/deployments",
    response_model=DeploymentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_preview_deployment(
    candidate_id: uuid.UUID,
    payload: RequestDeploymentBody,
    releases: ReleaseServiceDep,
) -> DeploymentResponse:
    model = await releases.request_deployment(
        RequestDeployment(release_candidate_id=candidate_id, **payload.model_dump())
    )
    return deployment_response(model)


@router.get("/v1/deployments/{deployment_id}", response_model=DeploymentResponse)
async def get_deployment(
    deployment_id: uuid.UUID, releases: ReleaseServiceDep
) -> DeploymentResponse:
    return deployment_response(await releases.get_deployment(deployment_id))


class DeploymentEventBody(BaseModel):
    event_id: str = Field(min_length=1, max_length=255)
    event_name: str = Field(default="activation", pattern=r"^activation$")


class DeploymentEvaluateBody(BaseModel):
    actor: str = Field(min_length=1, max_length=255)


@router.post("/v1/deployments/{deployment_id}/events", status_code=status.HTTP_201_CREATED)
async def ingest_deployment_event(
    deployment_id: uuid.UUID,
    payload: DeploymentEventBody,
    request: Request,
) -> dict[str, uuid.UUID]:
    """Ingest a real, non-internal product observation for Gate evaluation."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from regent.application.observation_service import ObservationInput, ObservationService
    from regent.domain.errors import DomainError, ErrorCode
    from regent.infrastructure.models import DeploymentModel, GoalModel
    from regent.model import ModelConfigurationError

    settings = get_settings()
    if settings.observation_signing_key is None:
        raise ModelConfigurationError("observation signing key is not configured")

    async with request.app.state.sessions() as session:
        deployment = await session.get(DeploymentModel, deployment_id)
        if deployment is None or deployment.status != "SUCCEEDED":
            raise DomainError(ErrorCode.INVALID_STATE, "succeeded deployment is required")
        goal = await session.scalar(
            select(GoalModel).where(
                GoalModel.metadata_json["last_deployment_id"].as_string() == str(deployment_id)
            )
        )
        if goal is None:
            try:
                corr = uuid.UUID(str(deployment.correlation_id))
            except ValueError as exc:
                raise DomainError(ErrorCode.NOT_FOUND, "goal not found for deployment") from exc
            goal = await session.scalar(select(GoalModel).where(GoalModel.correlation_id == corr))
        if goal is None:
            raise DomainError(ErrorCode.NOT_FOUND, "goal not found for deployment")
        goal_id = goal.id

    observations = ObservationService(
        request.app.state.sessions,
        settings.observation_signing_key.get_secret_value(),
    )
    item = ObservationInput(
        event_id=f"deployment:{deployment_id}:{payload.event_id}",
        goal_id=goal_id,
        metric_name="task_completion_count",
        metric_value={"value": 1.0},
        source="product-analytics",
        definition_version="v1",
        is_bot=False,
        is_internal=False,
        observed_at=datetime.now(UTC),
    )
    return {"observation_id": await observations.ingest(item, observations.sign(item))}


@router.post("/v1/deployments/{deployment_id}/evaluate")
async def evaluate_deployment_gate(
    deployment_id: uuid.UUID,
    payload: DeploymentEvaluateBody,
    request: Request,
) -> dict[str, object]:
    """Re-evaluate Gate after real observations arrive and decide if possible."""
    from sqlalchemy import select

    from regent.application.feedback_service import CreateIterationDecision, FeedbackService
    from regent.domain.errors import DomainError, ErrorCode
    from regent.infrastructure.models import DeploymentModel, GoalModel

    async with request.app.state.sessions() as session:
        deployment = await session.get(DeploymentModel, deployment_id)
        if deployment is None:
            raise DomainError(ErrorCode.NOT_FOUND, "deployment not found")
        goal = await session.scalar(
            select(GoalModel).where(
                GoalModel.metadata_json["last_deployment_id"].as_string() == str(deployment_id)
            )
        )
        if goal is None:
            try:
                corr = uuid.UUID(str(deployment.correlation_id))
            except ValueError as exc:
                raise DomainError(ErrorCode.NOT_FOUND, "goal not found for deployment") from exc
            goal = await session.scalar(select(GoalModel).where(GoalModel.correlation_id == corr))
        if goal is None:
            raise DomainError(ErrorCode.NOT_FOUND, "goal not found for deployment")
        goal_id = goal.id

    feedback = FeedbackService(request.app.state.sessions)
    gate = await feedback.evaluate(goal_id, deployment_id, actor=payload.actor)
    decision = None
    if gate.status != "INSUFFICIENT_EVIDENCE":
        decision = await feedback.decide(
            CreateIterationDecision(gate_evaluation_id=gate.id, actor=payload.actor)
        )
    async with request.app.state.sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id)
        if goal is not None:
            metadata = dict(goal.metadata_json or {})
            metadata["last_gate_status"] = gate.status
            if decision is not None:
                metadata["last_iteration_decision"] = decision.decision
            goal.metadata_json = metadata
    return {
        "gate_evaluation_id": str(gate.id),
        "goal_id": str(goal_id),
        "status": gate.status,
        "decision": decision.decision if decision is not None else None,
        "result": gate.result_json,
    }


def candidate_response(model: Any) -> ReleaseCandidateResponse:
    return ReleaseCandidateResponse(
        id=model.id,
        app_build_id=model.app_build_id,
        status=model.status,
        version=model.version,
        content_hash=model.content_hash,
        human_task_id=model.human_task_id,
    )


def deployment_response(model: Any) -> DeploymentResponse:
    return DeploymentResponse(
        id=model.id,
        release_candidate_id=model.release_candidate_id,
        environment=model.environment,
        status=model.status,
        version=model.version,
        endpoint=model.endpoint,
        reconciliation_required=model.reconciliation_required,
        failure_code=model.failure_code,
    )
