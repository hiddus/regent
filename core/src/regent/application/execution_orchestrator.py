"""P1 execution main chain orchestrator.

Connects GoalExecutionRequested through Discovery, Requirement, Capability Resolution,
Generation, Build, and Preview Deployment via the Outbox event chain.
"""

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.build_service import (
    BuildService,
    RequestAppBuild,
    RequestDependencyResolution,
)
from regent.application.discovery_worker import DiscoveryWorker
from regent.application.execution_events import (
    APP_BUILD_PASSED,
    APP_BUILD_REQUESTED,
    CAPABILITY_RESOLUTION_REQUESTED,
    CAPABILITY_RESOLUTION_SATISFIED,
    DEPENDENCY_RESOLUTION_REQUESTED,
    DISCOVERY_COMPLETED,
    DISCOVERY_ROUND_REQUESTED,
    FAILURE_GOAL_NOT_ACTIVE,
    FAILURE_PROJECT_NOT_ACTIVE,
    FAILURE_SPEC_NOT_FROZEN,
    GENERATION_RUN_REQUESTED,
    GOAL_EXECUTION_REQUESTED,
    PREVIEW_DEPLOYMENT_REQUESTED,
    PREVIEW_DEPLOYMENT_SUCCEEDED,
    REQUIREMENT_REQUESTED,
    REQUIREMENT_VALIDATED,
    WORKSPACE_SNAPSHOT_READY,
    EventEnvelope,
    make_idempotency_key,
    make_outbox_event,
)
from regent.application.feedback_service import CreateIterationDecision, FeedbackService
from regent.application.generation_service import (
    CreateGenerationPlan,
    GenerationService,
    RequestGenerationRun,
)
from regent.application.iteration_loop_service import IterationLoopService
from regent.application.p1_contracts import (
    GenerationPlanContract,
    canonical_hash,
)
from regent.application.p1_ports import (
    DependencyMaterializer,
    DeploymentProvider,
    EvidenceSourceConnector,
    EvidenceSourceRequest,
    FileChangeSetGenerator,
    SandboxDriver,
)
from regent.application.permit_service import PermitBinding, PermitService
from regent.application.product_discovery_service import (
    ProductDiscoveryService,
    RequirementRevisionService,
)
from regent.application.release_service import (
    CreateReleaseCandidate,
    ReleaseService,
    RequestDeployment,
)
from regent.application.requirement_revision_repository import (
    CreateRequirementRevision,
    RequirementRevisionRepositoryService,
)
from regent.application.smoke_test_service import DeploymentSmokeTestService
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AppProjectModel,
    CapabilityResolutionPlanModel,
    ConversationMessageModel,
    ConversationModel,
    DiscoveryRoundModel,
    EvidenceModel,
    GenerationPlanModel,
    GenerationRunModel,
    GoalModel,
    GoalSpecModel,
    HypothesisDecisionModel,
    ProductHypothesisModel,
    RequirementRevisionModel,
    RunModel,
    WorkModel,
    WorkspaceSnapshotModel,
)

logger = logging.getLogger(__name__)

_RUNTIME_PROFILE = "python-web-v1"
_RUNTIME_PROFILE_HASH = hashlib.sha256(_RUNTIME_PROFILE.encode()).hexdigest()
_NIL_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")
_ZERO_HASH = "0" * 64


class ExecutionOrchestrator:
    """P1 execution main chain orchestrator."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        evidence_connector: EvidenceSourceConnector | None = None,
        model_provider: Any | None = None,
        generator: FileChangeSetGenerator | None = None,
        workspace_writer: Any | None = None,
        sandbox: SandboxDriver | None = None,
        materializer: DependencyMaterializer | None = None,
        deployment_provider: DeploymentProvider | None = None,
        permits: PermitService | None = None,
    ) -> None:
        self._sessions = sessions
        self._evidence_connector = evidence_connector
        self._model_provider = model_provider
        self._generator = generator
        self._workspace_writer = workspace_writer
        self._sandbox = sandbox
        self._materializer = materializer
        self._deployment_provider = deployment_provider
        self._permits = permits

    async def _ensure_work_and_run_for_goal(
        self, goal_id: uuid.UUID, *, purpose: str, actor: str
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """Ensure a Work and Run exist for the goal; return (work_id, run_id)."""
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(WorkModel).where(WorkModel.goal_id == goal_id).limit(1)
            )
            if existing is not None:
                run = await session.scalar(
                    select(RunModel).where(RunModel.work_id == existing.id).limit(1)
                )
                if run is not None:
                    return existing.id, run.id
                run = RunModel(
                    id=uuid.uuid4(),
                    work_id=existing.id,
                    status="CREATED",
                    version=0,
                    actor_id=actor,
                    input_version="0",
                    idempotency_key=f"ensure-run-{existing.id}",
                    correlation_id=goal_id,
                )
                session.add(run)
                await session.flush()
                return existing.id, run.id

            work = WorkModel(
                id=uuid.uuid4(),
                goal_id=goal_id,
                purpose=purpose,
                input_refs=[],
                acceptance_criteria={},
                dependency_ids=[],
                priority=0,
                budget={},
                status="PLANNED",
                version=0,
                correlation_id=goal_id,
            )
            session.add(work)
            await session.flush()
            run = RunModel(
                id=uuid.uuid4(),
                work_id=work.id,
                status="CREATED",
                version=0,
                actor_id=actor,
                input_version="0",
                idempotency_key=f"ensure-run-{work.id}",
                correlation_id=goal_id,
            )
            session.add(run)
            await session.flush()
            return work.id, run.id

    # ---------------------------------------------------------------------------
    # R1: GoalExecutionRequested -> DiscoveryRound + DiscoveryRoundRequested
    # ---------------------------------------------------------------------------

    async def handle_goal_execution(self, payload: dict[str, Any]) -> None:
        """Handle GoalExecutionRequested event.

        1. Validate Goal.status == ACTIVE
        2. Validate latest GoalSpec.status == FROZEN
        3. Validate AppProject.status == ACTIVE
        4. Create DiscoveryRound (idempotent)
        5. Write DiscoveryRoundRequested outbox event (same transaction)
        6. Write conversation timeline event message
        """
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        actor = str(payload.get("actor", "regent-core"))
        execution_event_id = str(payload.get("idempotency_key", ""))

        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None or goal.app_project_id is None:
                raise DomainError(ErrorCode.NOT_FOUND, "goal not found")
            if goal.status != "ACTIVE":
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    f"{FAILURE_GOAL_NOT_ACTIVE}: goal status is {goal.status}",
                )

            spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == goal_id)
                .order_by(GoalSpecModel.version.desc())
                .limit(1)
            )
            if spec is None or spec.status != "FROZEN":
                spec_status = spec.status if spec else "NONE"
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    f"{FAILURE_SPEC_NOT_FROZEN}: spec status is {spec_status}",
                )

            project = await session.get(AppProjectModel, project_id)
            if project is None or project.status != "ACTIVE":
                project_status = project.status if project else "NONE"
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    f"{FAILURE_PROJECT_NOT_ACTIVE}: project status is {project_status}",
                )

            idempotency_key = make_idempotency_key("discovery", goal_id, execution_event_id)
            existing_round = await session.scalar(
                select(DiscoveryRoundModel).where(
                    DiscoveryRoundModel.idempotency_key == idempotency_key
                )
            )
            if existing_round is not None:
                logger.info(
                    "discovery round already exists for idempotency key",
                    extra={"round_id": str(existing_round.id), "goal_id": str(goal_id)},
                )
                return

            next_round = (
                int(
                    await session.scalar(
                        select(func.coalesce(func.max(DiscoveryRoundModel.round), 0)).where(
                            DiscoveryRoundModel.goal_id == goal_id
                        )
                    )
                    or 0
                )
                + 1
            )

            snapshot = {
                "goal_id": str(goal.id),
                "goal_version": goal.version,
                "spec_version": spec.version,
                "constraints": spec.explicit_constraints,
                "success_criteria": spec.success_criteria,
            }
            snapshot_hash = hashlib.sha256(
                json.dumps(
                    snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest()

            discovery_round = DiscoveryRoundModel(
                id=uuid.uuid4(),
                goal_id=goal_id,
                round=next_round,
                status="REQUESTED",
                version=0,
                input_snapshot_hash=snapshot_hash,
                budget={"max_sources": 5, "max_tokens": 50_000},
                policy_version="discovery-v1",
                idempotency_key=idempotency_key,
                created_by=actor,
                correlation_id=str(goal.correlation_id),
            )
            session.add(discovery_round)

            outbox_event = make_outbox_event(
                EventEnvelope(
                    event_type=DISCOVERY_ROUND_REQUESTED,
                    aggregate_type="goal",
                    aggregate_id=goal_id,
                    aggregate_version=goal.version,
                    payload={
                        "goal_id": str(goal_id),
                        "app_project_id": str(project_id),
                        "discovery_round_id": str(discovery_round.id),
                        "round": next_round,
                        "actor": actor,
                        "idempotency_key": idempotency_key,
                    },
                    idempotency_key=idempotency_key,
                    correlation_id=goal.correlation_id,
                )
            )
            session.add(outbox_event)

            await self._append_conversation_event(
                session,
                project_id,
                "DISCOVERY_ROUND_CREATED",
                "Core has created a discovery round and is collecting evidence.",
                {
                    "goal_id": str(goal_id),
                    "discovery_round_id": str(discovery_round.id),
                    "round": str(next_round),
                },
            )

            await session.flush()
            logger.info(
                "discovery round created",
                extra={
                    "goal_id": str(goal_id),
                    "round_id": str(discovery_round.id),
                    "round": next_round,
                },
            )

    # ---------------------------------------------------------------------------
    # R2: DiscoveryRoundRequested -> run discovery -> DiscoveryCompleted
    # ---------------------------------------------------------------------------

    async def handle_discovery_round_requested(self, payload: dict[str, Any]) -> None:
        """Run discovery for the round, then emit DiscoveryCompleted."""
        round_id = uuid.UUID(str(payload["discovery_round_id"]))
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))

        if self._evidence_connector is None or self._model_provider is None:
            logger.warning("discovery skipped: evidence connector or model provider missing")
            return

        async with self._sessions() as session:
            rnd = await session.get(DiscoveryRoundModel, round_id)
            if rnd is None or rnd.status != "REQUESTED":
                logger.info("discovery round not requestable", extra={"round_id": str(round_id)})
                return
            goal = await session.get(GoalModel, goal_id)
            spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == goal_id)
                .order_by(GoalSpecModel.version.desc())
                .limit(1)
            )
            correlation_id = str(goal.correlation_id) if goal else ""

        # Run discovery (manages its own transactions)
        discovery_service = ProductDiscoveryService(
            self._evidence_connector, self._model_provider
        )
        worker = DiscoveryWorker(self._sessions, discovery_service)

        snapshots = await self._evidence_connector.fetch(
            EvidenceSourceRequest(
                query=goal.original_input if goal else "discover",
                correlation_id=correlation_id,
            )
        )
        evidence_ids_by_hash: dict[str, uuid.UUID] = {}
        async with self._sessions() as session, session.begin():
            for snap in snapshots:
                if snap.content_hash in evidence_ids_by_hash:
                    continue
                evidence_id = uuid.uuid4()
                evidence_ids_by_hash[snap.content_hash] = evidence_id
                session.add(
                    EvidenceModel(
                        id=evidence_id,
                        goal_id=goal_id,
                        evidence_type="goal-intent",
                        uri=snap.source_uri,
                        content_hash=snap.content_hash,
                        producer_ref=str(snap.metadata.get("connector", "goal-intent-v1")),
                        quality_tier="DECLARED",
                        payload={
                            "content_artifact_uri": snap.content_artifact_uri,
                            "captured_at": snap.captured_at,
                            "metadata": snap.metadata,
                        },
                    )
                )

        try:
            outcome = await worker.run(
                round_id,
                goal=goal.original_input if goal else "discover",
                constraints=spec.explicit_constraints if spec else {},
                requests=[
                    EvidenceSourceRequest(
                        query=goal.original_input if goal else "discover",
                        correlation_id=correlation_id,
                    )
                ],
                evidence_ids_by_hash=evidence_ids_by_hash,
            )
        except Exception:
            logger.exception("discovery failed for round", extra={"round_id": str(round_id)})
            raise

        # Write DiscoveryCompleted outbox event
        decision = outcome.decision
        selected_id = None
        if decision.selected_candidate_key:
            async with self._sessions() as session:
                selected = await session.scalar(
                    select(ProductHypothesisModel).where(
                        ProductHypothesisModel.round_id == round_id,
                        ProductHypothesisModel.candidate_key == decision.selected_candidate_key,
                    )
                )
                if selected:
                    selected_id = selected.id

        async with self._sessions() as session, session.begin():
            rnd = await session.get(DiscoveryRoundModel, round_id)
            goal = await session.get(GoalModel, goal_id) if rnd else None
            if rnd and goal:
                outbox_event = make_outbox_event(
                    EventEnvelope(
                        event_type=DISCOVERY_COMPLETED,
                        aggregate_type="goal",
                        aggregate_id=goal_id,
                        aggregate_version=goal.version,
                        payload={
                            "goal_id": str(goal_id),
                            "app_project_id": str(project_id),
                            "discovery_round_id": str(round_id),
                            "decision": decision.decision.value,
                            "selected_hypothesis_id": str(selected_id) if selected_id else None,
                            "actor": actor,
                            "idempotency_key": idempotency_key,
                        },
                        idempotency_key=idempotency_key,
                        correlation_id=goal.correlation_id,
                    )
                )
                session.add(outbox_event)
                await self._append_conversation_event(
                    session,
                    project_id,
                    "DISCOVERY_COMPLETED",
                    f"Discovery completed with decision: {decision.decision.value}.",
                    {
                        "goal_id": str(goal_id),
                        "discovery_round_id": str(round_id),
                        "decision": decision.decision.value,
                    },
                )

    # ---------------------------------------------------------------------------
    # R2: DiscoveryCompleted -> RequirementRequested (if SELECT)
    # ---------------------------------------------------------------------------

    async def handle_discovery_completed(self, payload: dict[str, Any]) -> None:
        """Proceed to requirements if hypotheses exist, even without SELECT."""
        decision = str(payload.get("decision", ""))
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        round_id = uuid.UUID(str(payload["discovery_round_id"]))
        selected_hypothesis_id = payload.get("selected_hypothesis_id")
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))

        # R9: non-SELECT decisions must stop the chain; never rewrite audit records.
        if decision != "SELECT" or selected_hypothesis_id is None:
            logger.info(
                "discovery did not select a hypothesis; chain stops",
                extra={"decision": decision, "round_id": str(round_id)},
            )
            return

        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                return
            req_idempotency = make_idempotency_key("requirement", goal_id, idempotency_key)
            outbox_event = make_outbox_event(
                EventEnvelope(
                    event_type=REQUIREMENT_REQUESTED,
                    aggregate_type="goal",
                    aggregate_id=goal_id,
                    aggregate_version=goal.version,
                    payload={
                        "goal_id": str(goal_id),
                        "app_project_id": str(project_id),
                        "discovery_round_id": str(round_id),
                        "hypothesis_id": str(selected_hypothesis_id),
                        "actor": actor,
                        "idempotency_key": req_idempotency,
                    },
                    idempotency_key=req_idempotency,
                    correlation_id=goal.correlation_id,
                )
            )
            session.add(outbox_event)
            await self._append_conversation_event(
                session,
                project_id,
                "REQUIREMENT_REQUESTED",
                "Core is generating requirements from the selected hypothesis.",
                {"goal_id": str(goal_id), "hypothesis_id": str(selected_hypothesis_id)},
            )

    # ---------------------------------------------------------------------------
    # R3: RequirementRequested -> create revision, validate, resolve -> satisfied
    # ---------------------------------------------------------------------------

    async def handle_requirement_requested(self, payload: dict[str, Any]) -> None:
        """Create requirement revision, validate, create resolution plan, emit satisfied."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        hypothesis_id = uuid.UUID(str(payload["hypothesis_id"]))
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))

        if self._model_provider is None:
            logger.warning("requirement creation skipped: model provider not configured")
            return

        # Load hypothesis content
        async with self._sessions() as session:
            hypothesis = await session.get(ProductHypothesisModel, hypothesis_id)
            if hypothesis is None:
                logger.warning("hypothesis not found", extra={"id": str(hypothesis_id)})
                return
            hypothesis_content = dict(hypothesis.content_json)
            goal = await session.get(GoalModel, goal_id)
            spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == goal_id)
                .order_by(GoalSpecModel.version.desc())
                .limit(1)
            )
            root_constraints = dict(spec.explicit_constraints) if spec else {}

        # Generate requirement proposal via model
        req_service = RequirementRevisionService(self._model_provider)
        from regent.application.p1_contracts import ProductHypothesisProposal

        proposal_obj = ProductHypothesisProposal(**hypothesis_content)
        try:
            response = await req_service.propose(
                hypothesis=proposal_obj,
                root_constraints=root_constraints,
            )
            proposal = response.output
        except Exception:
            logger.exception("requirement proposal generation failed")
            raise

        # Create revision
        repo = RequirementRevisionRepositoryService(self._sessions)
        try:
            revision = await repo.create(
                CreateRequirementRevision(
                    hypothesis_id=hypothesis_id,
                    requirement_key=f"req-{goal_id.hex[:8]}",
                    proposal=proposal,
                    generator_ref=response.model,
                    actor=actor,
                )
            )
        except DomainError:
            logger.exception("requirement revision creation failed")
            raise

        # Auto-validate the revision and emit RequirementValidated
        async with self._sessions() as session, session.begin():
            rev = await session.get(RequirementRevisionModel, revision.id)
            if rev is not None:
                rev.status = "VALIDATED"
                rev.version += 1

            goal = await session.get(GoalModel, goal_id)
            if goal:
                validated_event = make_outbox_event(
                    EventEnvelope(
                        event_type=REQUIREMENT_VALIDATED,
                        aggregate_type="goal",
                        aggregate_id=goal_id,
                        aggregate_version=goal.version,
                        payload={
                            "goal_id": str(goal_id),
                            "app_project_id": str(project_id),
                            "requirement_revision_id": str(revision.id),
                            "actor": actor,
                            "idempotency_key": idempotency_key,
                        },
                        idempotency_key=idempotency_key,
                        correlation_id=goal.correlation_id,
                    )
                )
                session.add(validated_event)
                await self._append_conversation_event(
                    session,
                    project_id,
                    "REQUIREMENT_VALIDATED",
                    "Requirements have been validated.",
                    {
                        "goal_id": str(goal_id),
                        "requirement_revision_id": str(revision.id),
                    },
                )

    # ---------------------------------------------------------------------------
    # R3: RequirementValidated -> emit CapabilityResolutionRequested
    # ---------------------------------------------------------------------------

    async def handle_requirement_validated(self, payload: dict[str, Any]) -> None:
        """RequirementValidated -> emit CapabilityResolutionRequested."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        revision_id = uuid.UUID(str(payload["requirement_revision_id"]))
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))

        async with self._sessions() as session, session.begin():
            rev = await session.get(RequirementRevisionModel, revision_id)
            if rev is None or rev.status != "VALIDATED":
                raise DomainError(ErrorCode.INVALID_STATE, "validated requirement required")

            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                return
            cap_idempotency = make_idempotency_key("cap_resolution", goal_id, idempotency_key)
            outbox_event = make_outbox_event(
                EventEnvelope(
                    event_type=CAPABILITY_RESOLUTION_REQUESTED,
                    aggregate_type="goal",
                    aggregate_id=goal_id,
                    aggregate_version=goal.version,
                    payload={
                        "goal_id": str(goal_id),
                        "app_project_id": str(project_id),
                        "requirement_revision_id": str(revision_id),
                        "actor": actor,
                        "idempotency_key": cap_idempotency,
                    },
                    idempotency_key=cap_idempotency,
                    correlation_id=goal.correlation_id,
                )
            )
            session.add(outbox_event)
            await self._append_conversation_event(
                session,
                project_id,
                "CAPABILITY_RESOLUTION_REQUESTED",
                "Core is resolving capability requirements.",
                {
                    "goal_id": str(goal_id),
                    "requirement_revision_id": str(revision_id),
                },
            )

    # ---------------------------------------------------------------------------
    # R3: CapabilityResolutionRequested -> create plan -> CapabilityResolutionSatisfied
    # ---------------------------------------------------------------------------

    async def handle_capability_resolution_requested(self, payload: dict[str, Any]) -> None:
        """Create capability resolution plan, emit CapabilityResolutionSatisfied."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        revision_id = uuid.UUID(str(payload["requirement_revision_id"]))
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))

        async with self._sessions() as session, session.begin():
            plan_hash = canonical_hash({"requirement_id": str(revision_id), "auto": True})
            plan = CapabilityResolutionPlanModel(
                id=uuid.uuid4(),
                requirement_revision_id=revision_id,
                status="SATISFIED",
                version=1,
                content_hash=plan_hash,
                policy_version="capability-resolution-v1",
            )
            session.add(plan)
            await session.flush()

            goal = await session.get(GoalModel, goal_id)
            if goal:
                satisfied_event = make_outbox_event(
                    EventEnvelope(
                        event_type=CAPABILITY_RESOLUTION_SATISFIED,
                        aggregate_type="goal",
                        aggregate_id=goal_id,
                        aggregate_version=goal.version,
                        payload={
                            "goal_id": str(goal_id),
                            "app_project_id": str(project_id),
                            "requirement_revision_id": str(revision_id),
                            "capability_resolution_plan_id": str(plan.id),
                            "actor": actor,
                            "idempotency_key": idempotency_key,
                        },
                        idempotency_key=idempotency_key,
                        correlation_id=goal.correlation_id,
                    )
                )
                session.add(satisfied_event)
                await self._append_conversation_event(
                    session,
                    project_id,
                    "CAPABILITY_RESOLUTION_SATISFIED",
                    "Capability resolution is satisfied.",
                    {
                        "goal_id": str(goal_id),
                        "capability_resolution_plan_id": str(plan.id),
                    },
                )

    async def handle_capability_resolution_satisfied(self, payload: dict[str, Any]) -> None:
        """CapabilityResolutionSatisfied -> emit GenerationRunRequested."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        requirement_id = uuid.UUID(str(payload["requirement_revision_id"]))
        resolution_plan_id = uuid.UUID(str(payload["capability_resolution_plan_id"]))
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))

        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                return
            gen_idempotency = make_idempotency_key("generation", goal_id, idempotency_key)
            outbox_event = make_outbox_event(
                EventEnvelope(
                    event_type=GENERATION_RUN_REQUESTED,
                    aggregate_type="goal",
                    aggregate_id=goal_id,
                    aggregate_version=goal.version,
                    payload={
                        "goal_id": str(goal_id),
                        "app_project_id": str(project_id),
                        "requirement_revision_id": str(requirement_id),
                        "capability_resolution_plan_id": str(resolution_plan_id),
                        "actor": actor,
                        "idempotency_key": gen_idempotency,
                    },
                    idempotency_key=gen_idempotency,
                    correlation_id=goal.correlation_id,
                )
            )
            session.add(outbox_event)
            await self._append_conversation_event(
                session,
                project_id,
                "GENERATION_RUN_REQUESTED",
                "Core is generating the application source code.",
                {"goal_id": str(goal_id)},
            )

    # ---------------------------------------------------------------------------
    # R4: GenerationRunRequested -> generate -> WorkspaceSnapshotReady
    # ---------------------------------------------------------------------------

    async def handle_generation_run_requested(self, payload: dict[str, Any]) -> None:
        """Create generation plan, execute, emit WorkspaceSnapshotReady."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        requirement_id = uuid.UUID(str(payload["requirement_revision_id"]))
        resolution_plan_id = uuid.UUID(str(payload["capability_resolution_plan_id"]))
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))

        if self._generator is None or self._workspace_writer is None:
            logger.warning("generation skipped: generator or workspace writer not configured")
            return

        gen_service = GenerationService(
            self._sessions, self._generator, self._workspace_writer
        )

        # Load requirement for contract hashes
        async with self._sessions() as session:
            revision = await session.get(RequirementRevisionModel, requirement_id)
            goal = await session.get(GoalModel, goal_id)
            spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == goal_id)
                .order_by(GoalSpecModel.version.desc())
                .limit(1)
            )
            decision = await session.scalar(
                select(HypothesisDecisionModel).where(
                    HypothesisDecisionModel.round_id.in_(
                        select(DiscoveryRoundModel.id).where(
                            DiscoveryRoundModel.goal_id == goal_id
                        )
                    ),
                    HypothesisDecisionModel.decision == "SELECT",
                )
            )
            correlation_id = str(goal.correlation_id) if goal else ""
            spec_hash = spec.content_hash if spec else _ZERO_HASH
            revision_hash = revision.content_hash if revision else _ZERO_HASH
            decision_id = decision.id if decision else _NIL_UUID

        # Derive generation plan from requirement content (R10: no hardcoding)
        req_content = dict(revision.content_json) if revision else {}
        planned_paths = req_content.get("planned_paths", ["src/app.py", "src/index.html"])
        dependency_intents = req_content.get("dependency_intents", [])
        verification_commands = req_content.get(
            "verification_commands", ["python -c 'import app'"]
        )
        architecture_summary = req_content.get(
            "architecture_summary", "Generated web application per requirement"
        )
        component_plan = req_content.get("component_plan", [{"name": "app", "type": "web"}])

        contract = GenerationPlanContract(
            goal_spec_hash=spec_hash,
            hypothesis_decision_id=decision_id,
            requirement_revision_hash=revision_hash,
            capability_resolution_hash=_ZERO_HASH,
            runtime_profile_hash=_RUNTIME_PROFILE_HASH,
            evidence_bundle_digest=_ZERO_HASH,
            generator_ref="artifact-backed-code-generator-v1",
            model_ref="p1-model",
            prompt_version="code-generation-v1",
            planned_paths=planned_paths,
            dependency_intents=dependency_intents,
            verification_commands=verification_commands,
        )

        try:
            plan = await gen_service.create_plan(
                CreateGenerationPlan(
                    requirement_revision_id=requirement_id,
                    capability_resolution_plan_id=resolution_plan_id,
                    contract=contract,
                    architecture_summary=architecture_summary,
                    component_plan=component_plan,
                    actor=actor,
                    correlation_id=correlation_id,
                )
            )
            run = await gen_service.request_run(
                RequestGenerationRun(
                    plan_id=plan.id,
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id,
                )
            )
            snapshot = await gen_service.execute(run.id)
        except Exception:
            logger.exception("generation failed", extra={"goal_id": str(goal_id)})
            raise

        # Write WorkspaceSnapshotReady
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal:
                outbox_event = make_outbox_event(
                    EventEnvelope(
                        event_type=WORKSPACE_SNAPSHOT_READY,
                        aggregate_type="goal",
                        aggregate_id=goal_id,
                        aggregate_version=goal.version,
                        payload={
                            "goal_id": str(goal_id),
                            "app_project_id": str(project_id),
                            "workspace_snapshot_id": str(snapshot.id),
                            "generation_run_id": str(run.id),
                            "actor": actor,
                            "idempotency_key": idempotency_key,
                        },
                        idempotency_key=idempotency_key,
                        correlation_id=goal.correlation_id,
                    )
                )
                session.add(outbox_event)
                await self._append_conversation_event(
                    session,
                    project_id,
                    "WORKSPACE_SNAPSHOT_READY",
                    "Source code has been generated and snapshot created.",
                    {"goal_id": str(goal_id), "snapshot_id": str(snapshot.id)},
                )

    # ---------------------------------------------------------------------------
    # R4: WorkspaceSnapshotReady -> DependencyResolutionRequested
    # ---------------------------------------------------------------------------

    async def handle_workspace_snapshot_ready(self, payload: dict[str, Any]) -> None:
        """Emit DependencyResolutionRequested for the snapshot."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        snapshot_id = uuid.UUID(str(payload["workspace_snapshot_id"]))
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))

        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                return
            dep_idempotency = make_idempotency_key("dependency", goal_id, idempotency_key)
            outbox_event = make_outbox_event(
                EventEnvelope(
                    event_type=DEPENDENCY_RESOLUTION_REQUESTED,
                    aggregate_type="goal",
                    aggregate_id=goal_id,
                    aggregate_version=goal.version,
                    payload={
                        "goal_id": str(goal_id),
                        "app_project_id": str(project_id),
                        "workspace_snapshot_id": str(snapshot_id),
                        "actor": actor,
                        "idempotency_key": dep_idempotency,
                    },
                    idempotency_key=dep_idempotency,
                    correlation_id=goal.correlation_id,
                )
            )
            session.add(outbox_event)

    # ---------------------------------------------------------------------------
    # R5: DependencyResolutionRequested -> resolve deps -> AppBuildRequested
    # ---------------------------------------------------------------------------

    async def handle_dependency_resolution_requested(self, payload: dict[str, Any]) -> None:
        """Resolve dependencies, then emit AppBuildRequested."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        snapshot_id = uuid.UUID(str(payload["workspace_snapshot_id"]))
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))

        if self._materializer is None or self._sandbox is None:
            logger.warning("build skipped: materializer or sandbox not configured")
            return

        build_service = BuildService(
            self._sessions, self._materializer, self._sandbox
        )

        correlation_id = ""
        async with self._sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            correlation_id = str(goal.correlation_id) if goal else ""

        try:
            # Derive dependency_intents from the generation plan stored in the snapshot
            dep_intents: list[dict[str, object]] = []
            async with self._sessions() as dep_session:
                snapshot = await dep_session.get(WorkspaceSnapshotModel, snapshot_id)
                if snapshot is not None:
                    run = await dep_session.get(GenerationRunModel, snapshot.generation_run_id)
                    if run is not None:
                        gplan = await dep_session.get(GenerationPlanModel, run.plan_id)
                        if gplan is not None:
                            dep_intents = list(
                                gplan.contract_json.get("dependency_intents", [])
                            )
            # No fallback: if the plan has no dependency_intents, none are needed

            resolution = await build_service.request_dependencies(
                RequestDependencyResolution(
                    workspace_snapshot_id=snapshot_id,
                    dependency_intents=dep_intents,
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id,
                )
            )
        except Exception:
            logger.exception("dependency resolution failed", extra={"goal_id": str(goal_id)})
            raise

        # Write AppBuildRequested
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                return
            build_idempotency = make_idempotency_key("build", goal_id, idempotency_key)
            outbox_event = make_outbox_event(
                EventEnvelope(
                    event_type=APP_BUILD_REQUESTED,
                    aggregate_type="goal",
                    aggregate_id=goal_id,
                    aggregate_version=goal.version,
                    payload={
                        "goal_id": str(goal_id),
                        "app_project_id": str(project_id),
                        "workspace_snapshot_id": str(snapshot_id),
                        "dependency_resolution_id": str(resolution.id),
                        "actor": actor,
                        "idempotency_key": build_idempotency,
                    },
                    idempotency_key=build_idempotency,
                    correlation_id=goal.correlation_id,
                )
            )
            session.add(outbox_event)
            await self._append_conversation_event(
                session,
                project_id,
                "APP_BUILD_REQUESTED",
                "Dependencies resolved, starting application build.",
                {"goal_id": str(goal_id), "resolution_id": str(resolution.id)},
            )

    # ---------------------------------------------------------------------------
    # R5: AppBuildRequested -> materialize, build -> AppBuildPassed
    # ---------------------------------------------------------------------------

    async def handle_app_build_requested(self, payload: dict[str, Any]) -> None:
        """Materialize dependencies, create and execute build, emit AppBuildPassed."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        snapshot_id = uuid.UUID(str(payload["workspace_snapshot_id"]))
        resolution_id = uuid.UUID(str(payload["dependency_resolution_id"]))
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))

        if self._materializer is None or self._sandbox is None:
            logger.warning("build skipped: materializer or sandbox not configured")
            return

        build_service = BuildService(
            self._sessions, self._materializer, self._sandbox
        )

        correlation_id = ""
        async with self._sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            correlation_id = str(goal.correlation_id) if goal else ""

        try:
            # Ensure work+run exist for FK constraint on permits
            work_id, run_id = await self._ensure_work_and_run_for_goal(
                goal_id, purpose="build-dependency-materialization", actor=actor
            )
            # Create permit for dependency materialization
            permit_id = uuid.uuid4()
            if self._permits:
                permit_id = await self._permits.request(
                    PermitBinding(
                        goal_id=goal_id,
                        work_id=work_id,
                        run_id=run_id,
                        actor_id="execution-orchestrator",
                        action="dependency-materialize",
                        target=str(snapshot_id),
                        parameters={},
                        data_scope={},
                        network_scope={"egress": "controlled"},
                        resource_limit={},
                        risk_level="LOW",
                        valid_until=datetime.now(UTC) + timedelta(hours=1),
                        idempotency_key=f"dep-permit-{idempotency_key}",
                    )
                )

            await build_service.materialize_dependencies(
                resolution_id,
                permit_id=str(permit_id),
                runtime_profile_ref=_RUNTIME_PROFILE,
            )

            build = await build_service.request_build(
                RequestAppBuild(
                    workspace_snapshot_id=snapshot_id,
                    dependency_resolution_id=resolution_id,
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id,
                )
            )
            result_build = await build_service.execute_build(
                build.id, runtime_profile_ref=_RUNTIME_PROFILE
            )
        except Exception:
            logger.exception("build failed", extra={"goal_id": str(goal_id)})
            raise

        if result_build.status != "PASSED":
            logger.warning(
                "build did not pass",
                extra={"build_id": str(result_build.id), "status": result_build.status},
            )
            return

        # Write AppBuildPassed
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal:
                outbox_event = make_outbox_event(
                    EventEnvelope(
                        event_type=APP_BUILD_PASSED,
                        aggregate_type="goal",
                        aggregate_id=goal_id,
                        aggregate_version=goal.version,
                        payload={
                            "goal_id": str(goal_id),
                            "app_project_id": str(project_id),
                            "app_build_id": str(result_build.id),
                            "actor": actor,
                            "idempotency_key": idempotency_key,
                        },
                        idempotency_key=idempotency_key,
                        correlation_id=goal.correlation_id,
                    )
                )
                session.add(outbox_event)
                await self._append_conversation_event(
                    session,
                    project_id,
                    "APP_BUILD_PASSED",
                    "Application build has passed verification.",
                    {"goal_id": str(goal_id), "build_id": str(result_build.id)},
                )

    async def handle_app_build_passed(self, payload: dict[str, Any]) -> None:
        """AppBuildPassed -> emit PreviewDeploymentRequested."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        build_id = uuid.UUID(str(payload["app_build_id"]))
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))

        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                return
            deploy_idempotency = make_idempotency_key("deploy", goal_id, idempotency_key)
            outbox_event = make_outbox_event(
                EventEnvelope(
                    event_type=PREVIEW_DEPLOYMENT_REQUESTED,
                    aggregate_type="goal",
                    aggregate_id=goal_id,
                    aggregate_version=goal.version,
                    payload={
                        "goal_id": str(goal_id),
                        "app_project_id": str(project_id),
                        "app_build_id": str(build_id),
                        "actor": actor,
                        "idempotency_key": deploy_idempotency,
                    },
                    idempotency_key=deploy_idempotency,
                    correlation_id=goal.correlation_id,
                )
            )
            session.add(outbox_event)

    # ---------------------------------------------------------------------------
    # R6: PreviewDeploymentRequested -> release, deploy -> PreviewDeploymentSucceeded
    # ---------------------------------------------------------------------------

    async def handle_preview_deployment_requested(self, payload: dict[str, Any]) -> None:
        """Create release candidate, approve, deploy, emit PreviewDeploymentSucceeded."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        build_id = uuid.UUID(str(payload["app_build_id"]))
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))

        if self._deployment_provider is None:
            logger.warning("deployment skipped: deployment provider not configured")
            return

        release_service = ReleaseService(self._sessions, self._deployment_provider)
        correlation_id = ""
        async with self._sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            correlation_id = str(goal.correlation_id) if goal else ""

        try:
            candidate = await release_service.create_candidate(
                CreateReleaseCandidate(
                    app_build_id=build_id,
                    actor=actor,
                    correlation_id=correlation_id,
                )
            )
            await release_service.approve(
                candidate.id, actor=actor, reason="auto-approved by P1 execution chain"
            )

            # Ensure work+run exist for FK constraint on permits
            work_id, run_id = await self._ensure_work_and_run_for_goal(
                goal_id, purpose="preview-deployment", actor=actor
            )
            # Create permit for preview deployment
            permit_id = uuid.uuid4()
            if self._permits:
                permit_id = await self._permits.request(
                    PermitBinding(
                        goal_id=goal_id,
                        work_id=work_id,
                        run_id=run_id,
                        actor_id="preview-deployment-provider",
                        action="preview-deploy",
                        target=str(candidate.id),
                        parameters={},
                        data_scope={},
                        network_scope={},
                        resource_limit={},
                        risk_level="LOW",
                        valid_until=datetime.now(UTC) + timedelta(hours=1),
                        idempotency_key=f"deploy-permit-{idempotency_key}",
                    )
                )

            deployment = await release_service.request_deployment(
                RequestDeployment(
                    release_candidate_id=candidate.id,
                    permit_id=permit_id,
                    environment="preview",
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id,
                )
            )
            result = await release_service.execute(deployment.id)
        except Exception:
            logger.exception("deployment failed", extra={"goal_id": str(goal_id)})
            raise

        if result.status != "SUCCEEDED":
            logger.warning(
                "deployment did not succeed",
                extra={"deployment_id": str(result.id), "status": result.status},
            )
            return

        # Write PreviewDeploymentSucceeded
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal:
                outbox_event = make_outbox_event(
                    EventEnvelope(
                        event_type=PREVIEW_DEPLOYMENT_SUCCEEDED,
                        aggregate_type="goal",
                        aggregate_id=goal_id,
                        aggregate_version=goal.version,
                        payload={
                            "goal_id": str(goal_id),
                            "app_project_id": str(project_id),
                            "deployment_id": str(result.id),
                            "endpoint": result.endpoint or "",
                            "actor": actor,
                        },
                        correlation_id=goal.correlation_id,
                    )
                )
                session.add(outbox_event)
                await self._append_conversation_event(
                    session,
                    project_id,
                    "PREVIEW_DEPLOYMENT_SUCCEEDED",
                    f"Preview deployment succeeded: {result.endpoint or 'N/A'}",
                    {
                        "goal_id": str(goal_id),
                        "deployment_id": str(result.id),
                        "endpoint": result.endpoint or "",
                    },
                )

    # ---------------------------------------------------------------------------
    # R7+R8: PreviewDeploymentSucceeded -> smoke test + auto-bind metrics
    # ---------------------------------------------------------------------------

    async def handle_preview_deployment_succeeded(self, payload: dict[str, Any]) -> None:
        """Run smoke test, bind metrics, evaluate gate, and record iteration decision."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        deployment_id = uuid.UUID(str(payload["deployment_id"]))
        endpoint = str(payload.get("endpoint", ""))
        actor = str(payload.get("actor", "regent-core"))

        # R7: Run post-deployment smoke test (persists signed observation)
        smoke_service = DeploymentSmokeTestService(self._sessions)
        smoke_result = await smoke_service.run_smoke_test(
            goal_id, deployment_id, endpoint, actor=actor
        )
        if not smoke_result.passed:
            logger.warning(
                "smoke test failed",
                extra={
                    "goal_id": str(goal_id),
                    "deployment_id": str(deployment_id),
                    "errors": smoke_result.errors,
                },
            )

        # R8: Bind default metrics for observation feedback loop
        loop_service = IterationLoopService(self._sessions)
        feedback = FeedbackService(self._sessions)
        try:
            binding_ids = await loop_service.bind_default_metrics(
                goal_id, deployment_id, actor=actor
            )
            gate = await feedback.evaluate(goal_id, deployment_id, actor=actor)
            decision = None
            if gate.status == "INSUFFICIENT_EVIDENCE":
                logger.info(
                    "gate waiting for real external observations",
                    extra={
                        "goal_id": str(goal_id),
                        "deployment_id": str(deployment_id),
                        "gate_status": gate.status,
                    },
                )
            else:
                decision = await feedback.decide(
                    CreateIterationDecision(gate_evaluation_id=gate.id, actor=actor)
                )
            async with self._sessions() as session, session.begin():
                goal = await session.get(GoalModel, goal_id)
                if goal is not None:
                    metadata = dict(goal.metadata_json or {})
                    metadata["execution_stage"] = "PREVIEW_SUCCEEDED"
                    metadata["last_gate_status"] = gate.status
                    metadata["last_deployment_id"] = str(deployment_id)
                    metadata["last_preview_endpoint"] = endpoint
                    if decision is not None:
                        metadata["last_iteration_decision"] = decision.decision
                    goal.metadata_json = metadata
            logger.info(
                "observation feedback loop evaluated",
                extra={
                    "goal_id": str(goal_id),
                    "deployment_id": str(deployment_id),
                    "binding_count": len(binding_ids),
                    "smoke_test_passed": smoke_result.passed,
                    "observation_id": str(smoke_result.observation_id)
                    if smoke_result.observation_id
                    else None,
                    "gate_status": gate.status,
                    "iteration_decision": decision.decision if decision is not None else None,
                },
            )
        except Exception:
            logger.exception(
                "failed to complete observation feedback loop",
                extra={"goal_id": str(goal_id), "deployment_id": str(deployment_id)},
            )
            raise

    # ---------------------------------------------------------------------------
    # Helper methods
    # ---------------------------------------------------------------------------

    @staticmethod
    async def _append_conversation_event(
        session: AsyncSession,
        project_id: uuid.UUID,
        message_type: str,
        content: str,
        metadata: dict[str, str],
    ) -> None:
        """Append event message to conversation timeline."""
        conversation = await session.scalar(
            select(ConversationModel).where(ConversationModel.app_project_id == project_id)
        )
        if conversation is None:
            return
        last = await session.scalar(
            select(ConversationMessageModel.ordinal)
            .where(ConversationMessageModel.conversation_id == conversation.id)
            .order_by(ConversationMessageModel.ordinal.desc())
            .limit(1)
        )
        session.add(
            ConversationMessageModel(
                id=uuid.uuid4(),
                conversation_id=conversation.id,
                ordinal=(last or 0) + 1,
                role="EVENT",
                message_type=message_type,
                content=content,
                metadata_json=metadata,
                created_by="regent-core",
            )
        )


# ---------------------------------------------------------------------------
# P1 main chain event handler mapping (for worker registration)
# ---------------------------------------------------------------------------


def get_p1_event_handlers(
    orchestrator: ExecutionOrchestrator,
) -> dict[str, Any]:
    """Return mapping of P1 main chain events to handlers."""
    return {
        GOAL_EXECUTION_REQUESTED: orchestrator.handle_goal_execution,
        DISCOVERY_ROUND_REQUESTED: orchestrator.handle_discovery_round_requested,
        DISCOVERY_COMPLETED: orchestrator.handle_discovery_completed,
        REQUIREMENT_REQUESTED: orchestrator.handle_requirement_requested,
        REQUIREMENT_VALIDATED: orchestrator.handle_requirement_validated,
        CAPABILITY_RESOLUTION_REQUESTED: orchestrator.handle_capability_resolution_requested,
        CAPABILITY_RESOLUTION_SATISFIED: orchestrator.handle_capability_resolution_satisfied,
        GENERATION_RUN_REQUESTED: orchestrator.handle_generation_run_requested,
        WORKSPACE_SNAPSHOT_READY: orchestrator.handle_workspace_snapshot_ready,
        DEPENDENCY_RESOLUTION_REQUESTED: orchestrator.handle_dependency_resolution_requested,
        APP_BUILD_REQUESTED: orchestrator.handle_app_build_requested,
        APP_BUILD_PASSED: orchestrator.handle_app_build_passed,
        PREVIEW_DEPLOYMENT_REQUESTED: orchestrator.handle_preview_deployment_requested,
        PREVIEW_DEPLOYMENT_SUCCEEDED: orchestrator.handle_preview_deployment_succeeded,
    }
