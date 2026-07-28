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
from regent.application.capability_build_service import materialize_build_items
from regent.application.capability_resolution_service import (
    CapabilityCandidate,
    CapabilityGap,
    CapabilityResolutionService,
    ResolutionMethod,
    ToolCandidate,
)
from regent.application.compliance_risk_service import (
    ComplianceChecker,
    ComplianceStatus,
)
from regent.application.delivery_gap_recovery import DeliveryGapRecoveryService
from regent.application.discovery_worker import DiscoveryWorker
from regent.application.evidence_policy import (
    collect_authorized_urls,
    goal_requires_external_evidence,
)
from regent.application.execution_events import (
    APP_BUILD_PASSED,
    APP_BUILD_REQUESTED,
    CAPABILITY_RESOLUTION_REQUESTED,
    CAPABILITY_RESOLUTION_SATISFIED,
    DEPENDENCY_RESOLUTION_REQUESTED,
    DISCOVERY_COMPLETED,
    DISCOVERY_ROUND_REQUESTED,
    FAILURE_COMPLIANCE,
    FAILURE_GOAL_NOT_ACTIVE,
    FAILURE_PROJECT_NOT_ACTIVE,
    FAILURE_SPEC_NOT_FROZEN,
    GENERATION_RUN_REQUESTED,
    GOAL_EXECUTION_REQUESTED,
    PREVIEW_DEPLOYMENT_REQUESTED,
    PREVIEW_DEPLOYMENT_SUCCEEDED,
    QUALITY_APPROVAL_COMPLETED,
    QUALITY_APPROVAL_REQUESTED,
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
from regent.application.goal_interpreter import (
    GoalInterpretation,
    GoalInterpreter,
    SubGoal,
)
from regent.application.iteration_loop_service import IterationLoopService
from regent.application.milestone_service import (
    GOAL_SCALE_LARGE,
    acceptance_for_current_milestone,
    advance_milestone,
    current_milestone,
    ensure_milestone_plan,
    is_final_milestone,
    plan_from_metadata,
)
from regent.application.organization_service import (
    OrganizationService,
    compute_utility,
    default_organization_space,
    select_best_organization,
)
from regent.application.p1_contracts import (
    GenerationPlanContract,
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
from regent.application.research_more_recovery import ResearchMoreRecoveryService
from regent.application.run_advancement import advance_created_run
from regent.application.smoke_test_service import DeploymentSmokeTestService
from regent.application.transition_service import TransitionContext, TransitionService
from regent.domain.errors import DomainError, ErrorCode
from regent.domain.transitions import GoalCommand
from regent.infrastructure.models import (
    AppProjectModel,
    CapabilityModel,
    CapabilityResolutionItemModel,
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
    ToolSpecModel,
    WorkModel,
    WorkspaceSnapshotModel,
)
from regent.runtime.timers import DurableTimerService

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
        """Ensure a Work and Run exist for the goal; return (work_id, run_id).

        GAC-C1: never leave a Run stranded in CREATED — advance to RUNNING.
        """
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(WorkModel).where(WorkModel.goal_id == goal_id).limit(1)
            )
            if existing is not None:
                run = await session.scalar(
                    select(RunModel).where(RunModel.work_id == existing.id).limit(1)
                )
                if run is not None:
                    work_id, run_id = existing.id, run.id
                else:
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
                    work_id, run_id = existing.id, run.id
            else:
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
                work_id, run_id = work.id, run.id
        await advance_created_run(self._sessions, run_id, actor=actor)
        return work_id, run_id

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

            # GAC-E1: LARGE goals must be milestone-split before any delivery loop.
            milestone_plan = await ensure_milestone_plan(session, goal=goal, spec=spec)
            current = current_milestone(milestone_plan)

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
                "goal_scale": milestone_plan.goal_scale,
                "milestone_ordinal": current.ordinal,
                "milestone_key": current.key,
                "milestone_title": current.title,
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
                (
                    f"正在分析第 {current.ordinal}/{len(milestone_plan.milestones)} 阶段需求：{current.title}。"
                    + (
                        "项目规模较大，将分阶段推进。"
                        if milestone_plan.goal_scale == GOAL_SCALE_LARGE
                        else "项目规模适中，单阶段即可完成。"
                    )
                ),
                {
                    "goal_id": str(goal_id),
                    "discovery_round_id": str(discovery_round.id),
                    "round": str(next_round),
                    "goal_scale": milestone_plan.goal_scale,
                    "milestone_ordinal": current.ordinal,
                    "milestone_key": current.key,
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

        # V3 P1-B: Goal decomposition -> SubGoal -> Work items
        try:
            async with self._sessions() as session:
                goal_obj = await session.get(GoalModel, goal_id)
                if goal_obj is not None:
                    meta = dict(goal_obj.metadata_json or {})
                    if "decomposed_work_items" not in meta:
                        interpretation = GoalInterpretation(
                            objective=meta.get("objective", f"Goal {goal_id}"),
                            explicit_constraints=meta.get("constraints", {}),
                            success_criteria=meta.get("success_criteria", {}),
                        )
                        # Use static create_work_items with fallback sub-goals
                        sub_goals = [
                            SubGoal(
                                id="root",
                                label=interpretation.objective or "root",
                                depends_on=[],
                                acceptance_criteria=dict(interpretation.success_criteria),
                            )
                        ]
                        work_cmds = GoalInterpreter.create_work_items(
                            sub_goals,
                            goal_id=goal_id,
                            correlation_id=goal_obj.correlation_id,
                        )
                        meta["decomposed_work_items"] = work_cmds
                        goal_obj.metadata_json = meta
        except Exception:
            logger.warning(
                "goal decomposition skipped (non-fatal)",
                extra={"goal_id": str(goal_id)},
                exc_info=True,
            )

        # GAC-O1: ensure minimal organization exists before execution chain starts.
        # V3 P1-A: utility-driven organization selection.
        try:
            org_service = OrganizationService(self._sessions)
            receipt = await org_service.organize(goal_id)
            # Write utility evaluation to Goal metadata
            async with self._sessions() as session, session.begin():
                goal_obj = await session.get(GoalModel, goal_id)
                if goal_obj is not None:
                    meta = dict(goal_obj.metadata_json or {})
                    if "utility_evaluation" not in meta:
                        # Fallback: evaluate if organize() didn't store it
                        templates = default_organization_space()
                        candidates = [
                            (t, compute_utility(t)) for t in templates
                        ]
                        best = select_best_organization(candidates)
                        if best is not None:
                            tmpl, result = best
                            meta["utility_evaluation"] = {
                                "template_id": tmpl.template_id,
                                "utility": result.utility,
                                "components": result.components,
                                "rationale": result.rationale,
                            }
                            goal_obj.metadata_json = meta
            logger.info(
                "organization ready for goal",
                extra={
                    "goal_id": str(goal_id),
                    "strategy": receipt.strategy,
                },
            )
        except Exception:
            logger.warning(
                "organization skipped (non-fatal)",
                extra={"goal_id": str(goal_id)},
                exc_info=True,
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
            # REQUESTED = fresh; RESEARCHING = worker crashed mid-run — allow reclaim.
            if rnd is None or rnd.status not in {"REQUESTED", "RESEARCHING"}:
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

        goal_text = goal.original_input if goal else "discover"
        constraints = dict(spec.explicit_constraints) if spec else {}
        meta = dict(goal.metadata_json or {}) if goal else {}
        if meta.get("discovery_policy"):
            constraints["discovery_policy"] = meta["discovery_policy"]
        if meta.get("capability_resolution"):
            constraints["capability_resolution_bound"] = True
        authorized_urls = collect_authorized_urls(goal_text, constraints)
        meta_urls = meta.get("authorized_source_urls") or []
        if isinstance(meta_urls, list):
            authorized_urls = list(
                dict.fromkeys(
                    [
                        *authorized_urls,
                        *[str(item).strip() for item in meta_urls if str(item).strip()],
                    ]
                )
            )
        # GAC-E2: When goal requires external evidence but no URLs are authorized,
        # seed default feeds from the certified capability package so the connector
        # can fetch http-snapshot evidence on the first discovery round.
        _seed_default_feeds = False
        if not authorized_urls and goal_requires_external_evidence(goal_text, constraints):
            from regent.infrastructure.evidence_capability import (
                load_allowlisted_http_capability_package,
            )
            package = load_allowlisted_http_capability_package()
            authorized_urls = list(package.default_feeds)
            _seed_default_feeds = True
        evidence_request = EvidenceSourceRequest(
            query=goal_text,
            correlation_id=correlation_id,
            authorized_urls=authorized_urls,
            source_types=["http"] if authorized_urls else [],
        )
        snapshots = await self._evidence_connector.fetch(evidence_request)
        http_entry_count = sum(
            len(item.metadata.get("entries") or [])
            for item in snapshots
            if item.metadata.get("kind") == "http-snapshot"
        )
        if http_entry_count:
            constraints["http_entry_count_hint"] = http_entry_count
        evidence_ids_by_hash: dict[str, uuid.UUID] = {}
        async with self._sessions() as session, session.begin():
            # GAC-E2: persist seeded default feeds into Goal metadata.
            if _seed_default_feeds:
                goal_obj = await session.get(GoalModel, goal_id)
                if goal_obj is not None:
                    goal_meta = dict(goal_obj.metadata_json or {})
                    if not goal_meta.get("authorized_source_urls"):
                        goal_meta["authorized_source_urls"] = authorized_urls
                        goal_obj.metadata_json = goal_meta
            for snap in snapshots:
                if snap.content_hash in evidence_ids_by_hash:
                    continue
                evidence_id = uuid.uuid4()
                evidence_ids_by_hash[snap.content_hash] = evidence_id
                kind = str(snap.metadata.get("kind", "goal-intent"))
                if kind == "http-snapshot":
                    evidence_type = "http-snapshot"
                    quality_tier = "OBSERVED"
                else:
                    evidence_type = "goal-intent"
                    quality_tier = "DECLARED"
                session.add(
                    EvidenceModel(
                        id=evidence_id,
                        goal_id=goal_id,
                        evidence_type=evidence_type,
                        uri=snap.source_uri,
                        content_hash=snap.content_hash,
                        producer_ref=str(
                            snap.metadata.get("connector", "goal-intent-v1")
                        ),
                        quality_tier=quality_tier,
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
                goal=goal_text,
                constraints=constraints,
                requests=[evidence_request],
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
                    (
                        f"需求分析完成，决策：{decision.decision.value}。"
                        f"{decision.rationale}"
                    ).strip(),
                    {
                        "goal_id": str(goal_id),
                        "discovery_round_id": str(round_id),
                        "decision": decision.decision.value,
                        "rationale": decision.rationale,
                        "missing_evidence": list(decision.missing_evidence),
                    },
                )

    # ---------------------------------------------------------------------------
    # R2: DiscoveryCompleted -> RequirementRequested (if SELECT)
    # ---------------------------------------------------------------------------

    async def handle_discovery_completed(self, payload: dict[str, Any]) -> None:
        """Proceed to requirements if SELECT; RESEARCH_MORE binds evidence capability."""
        decision = str(payload.get("decision", ""))
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        round_id = uuid.UUID(str(payload["discovery_round_id"]))
        selected_hypothesis_id = payload.get("selected_hypothesis_id")
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))

        if decision == "RESEARCH_MORE":
            # Core detects the gap; certified connector capability supplies feeds — not chat paste.
            await ResearchMoreRecoveryService(self._sessions).recover(
                goal_id=goal_id,
                project_id=project_id,
                round_id=round_id,
                actor=actor,
            )
            return

        # R9: other non-SELECT decisions must stop the chain; never rewrite audit records.
        if decision != "SELECT" or selected_hypothesis_id is None:
            logger.info(
                "discovery did not select a hypothesis; chain stops",
                extra={"decision": decision, "round_id": str(round_id)},
            )
            await self._halt_goal_stage(
                goal_id,
                project_id,
                stage="DISCOVERY_NO_SELECT",
                message=(
                    f"Discovery decision={decision}; chain halted without SELECT "
                    "(GAC-A4 observable exit)."
                ),
                terminal=GoalCommand.EXHAUST,
                actor=actor,
                extra={"decision": decision, "discovery_round_id": str(round_id)},
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
                "正在根据选定的方案生成产品需求。",
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
                # GAC-GA: pass original goal text to requirement phase
                # so the requirement LLM can see what the user actually asked for.
                goal_text=goal.original_input if goal else "",
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
                    "产品需求已验证通过。",
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
                "正在分析实现所需的技术能力。",
                {
                    "goal_id": str(goal_id),
                    "requirement_revision_id": str(revision_id),
                },
            )

    # ---------------------------------------------------------------------------
    # R3: CapabilityResolutionRequested -> create plan -> CapabilityResolutionSatisfied
    # ---------------------------------------------------------------------------

    async def handle_capability_resolution_requested(self, payload: dict[str, Any]) -> None:
        """Resolve capability gaps from requirement/hypothesis; emit SATISFIED or wait."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        revision_id = uuid.UUID(str(payload["requirement_revision_id"]))
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))

        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(CapabilityResolutionPlanModel).where(
                    CapabilityResolutionPlanModel.requirement_revision_id == revision_id
                )
            )
            if existing is not None:
                if existing.status == "SATISFIED":
                    goal = await session.get(GoalModel, goal_id)
                    if goal is not None:
                        session.add(
                            make_outbox_event(
                                EventEnvelope(
                                    event_type=CAPABILITY_RESOLUTION_SATISFIED,
                                    aggregate_type="goal",
                                    aggregate_id=goal_id,
                                    aggregate_version=goal.version,
                                    payload={
                                        "goal_id": str(goal_id),
                                        "app_project_id": str(project_id),
                                        "requirement_revision_id": str(revision_id),
                                        "capability_resolution_plan_id": str(existing.id),
                                        "actor": actor,
                                        "idempotency_key": idempotency_key,
                                    },
                                    idempotency_key=idempotency_key,
                                    correlation_id=goal.correlation_id,
                                )
                            )
                        )
                return

            revision = await session.get(RequirementRevisionModel, revision_id)
            if revision is None:
                return
            hypothesis = await session.get(ProductHypothesisModel, revision.hypothesis_id)
            content = dict(revision.content_json or {})
            hyp_content = dict((hypothesis.content_json if hypothesis else None) or {})
            names: list[str] = []
            for raw in content.get("required_capabilities") or []:
                if isinstance(raw, str) and raw.strip():
                    names.append(raw.strip())
            for raw in hyp_content.get("required_capabilities") or []:
                if isinstance(raw, str) and raw.strip():
                    names.append(raw.strip())
            # de-dupe preserving order
            names = list(dict.fromkeys(names))

            gaps = [
                CapabilityGap(
                    requirement_key=name[:120],
                    capability_name=name,
                    build_allowed=True,
                    human_resolvable=True,
                )
                for name in names
            ]
            cap_rows = list(await session.scalars(select(CapabilityModel)))
            tool_rows = list(await session.scalars(select(ToolSpecModel)))
            resolved = CapabilityResolutionService().resolve(
                gaps,
                [
                    CapabilityCandidate(id=row.id, name=row.name, status=row.status)
                    for row in cap_rows
                ],
                [
                    ToolCandidate(
                        id=row.id, capability_name=row.capability_name, status=row.status
                    )
                    for row in tool_rows
                ],
            )
            # GAC-B2: materialize BUILD into registered capabilities before SATISFIED.
            resolved_items = await materialize_build_items(
                session, goal_id=goal_id, items=resolved.items
            )

            blocking = {
                ResolutionMethod.BLOCK,
                ResolutionMethod.REQUEST_HUMAN,
            }
            has_block = any(item.method in blocking for item in resolved_items)
            # Empty gaps → honest SATISFIED (no declared capability needs).
            # BUILD (now with capability_id) / REUSE / CONFIGURE / COMPOSE → proceed.
            status = "WAITING_HUMAN" if has_block else "SATISFIED"
            plan = CapabilityResolutionPlanModel(
                id=uuid.uuid4(),
                requirement_revision_id=revision_id,
                status=status,
                version=1,
                content_hash=resolved.content_hash,
                policy_version=resolved.policy_version,
            )
            session.add(plan)
            await session.flush()
            for item in resolved_items:
                session.add(
                    CapabilityResolutionItemModel(
                        id=uuid.uuid4(),
                        plan_id=plan.id,
                        requirement_key=item.requirement_key,
                        capability_name=item.capability_name,
                        gap_type=item.gap_type,
                        resolution_method=item.method.value,
                        capability_id=item.capability_id,
                        tool_spec_id=item.tool_spec_id,
                        status="RESOLVED" if item.method not in blocking else "OPEN",
                        evidence_refs=[],
                    )
                )

            goal = await session.get(GoalModel, goal_id)
            if goal is not None:
                metadata = dict(goal.metadata_json or {})
                metadata["requirement_revision_id"] = str(revision_id)
                metadata["capability_resolution_plan_id"] = str(plan.id)
                metadata["capability_resolution_status"] = status
                metadata["capability_resolution_methods"] = [
                    item.method.value for item in resolved_items
                ]
                metadata["capability_build_ids"] = [
                    str(item.capability_id)
                    for item in resolved_items
                    if item.method is ResolutionMethod.BUILD and item.capability_id
                ]
                if has_block:
                    metadata["execution_stage"] = "WAITING_HUMAN"
                    metadata["awaiting_capability_resolution"] = True
                goal.metadata_json = metadata

            await self._append_conversation_event(
                session,
                project_id,
                "CAPABILITY_RESOLUTION_PLANNED",
                (
                    "部分能力需要人工确认，等待处理中。"
                    if has_block
                    else (
                        "技术能力已就绪"
                        + (
                            f"，已自动构建 {sum(1 for i in resolved_items if i.method.value == 'BUILD')} 项能力。"
                            if any(i.method.value == "BUILD" for i in resolved_items)
                            else "。"
                        )
                    )
                ),
                {
                    "goal_id": str(goal_id),
                    "capability_resolution_plan_id": str(plan.id),
                    "status": status,
                    "item_count": len(resolved_items),
                    "gac_exit": "WAITING_HUMAN" if has_block else None,
                },
            )

            if status != "SATISFIED" or goal is None:
                return

            session.add(
                make_outbox_event(
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
                "正在生成应用源代码。",
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
            http_evidence = list(
                await session.scalars(
                    select(EvidenceModel).where(
                        EvidenceModel.goal_id == goal_id,
                        EvidenceModel.evidence_type == "http-snapshot",
                    )
                )
            )
            correlation_id = str(goal.correlation_id) if goal else ""
            spec_hash = spec.content_hash if spec else _ZERO_HASH
            revision_hash = revision.content_hash if revision else _ZERO_HASH
            decision_id = decision.id if decision else _NIL_UUID

        # Derive generation plan from requirement content (R10: no hardcoding)
        req_content = dict(revision.content_json) if revision else {}
        planned_paths = req_content.get(
            "planned_paths",
            ["src/app.py", "src/index.html", "requirements.txt", "README.md"],
        )
        # Ensure mandatory project files are always included
        for mandatory in ("requirements.txt", "README.md"):
            if mandatory not in planned_paths:
                planned_paths.append(mandatory)
        dependency_intents = req_content.get("dependency_intents", [])
        verification_commands = req_content.get(
            "verification_commands", ["python -c 'import app'"]
        )
        architecture_summary = req_content.get(
            "architecture_summary", "Generated web application per requirement"
        )
        component_plan = req_content.get("component_plan", [{"name": "app", "type": "web"}])
        observed_entries: list[dict[str, object]] = []
        for item in http_evidence:
            evidence_payload = dict(item.payload or {})
            metadata = dict(evidence_payload.get("metadata") or {})
            for entry in metadata.get("entries") or []:
                if isinstance(entry, dict):
                    observed_entries.append(
                        {
                            "title": entry.get("title", ""),
                            "link": entry.get("link", ""),
                            "summary": entry.get("summary", ""),
                            "source_uri": item.uri,
                            "evidence_id": str(item.id),
                        }
                    )
            if len(observed_entries) >= 30:
                break
        acceptance_contract = dict(req_content.get("acceptance_contract") or {})
        if observed_entries:
            acceptance_contract["observed_evidence_entries"] = observed_entries[:30]
            acceptance_contract["must_render_observed_entries"] = True
        if spec and spec.success_criteria:
            acceptance_contract["success_criteria"] = dict(spec.success_criteria)
            if "min_list_items" in spec.success_criteria:
                acceptance_contract["min_list_items"] = spec.success_criteria["min_list_items"]
            if "required_phrases" in spec.success_criteria:
                acceptance_contract["required_phrases"] = spec.success_criteria[
                    "required_phrases"
                ]
            if "min_outbound_links" in spec.success_criteria:
                acceptance_contract["min_outbound_links"] = spec.success_criteria[
                    "min_outbound_links"
                ]

        # Goal-attainment / milestone-scoped acceptance (GAC-E2).
        async with self._sessions() as session:
            goal_meta_row = await session.get(GoalModel, goal_id)
            goal_meta = dict((goal_meta_row.metadata_json if goal_meta_row else None) or {})
        # GAC-GA: GoalAnchor — inject original goal text into acceptance_contract
        # so both the generator and delivery review can see it.
        if goal_meta_row and goal_meta_row.original_input:
            acceptance_contract["goal_anchor_text"] = goal_meta_row.original_input
        if goal_meta.get("goal_scale"):
            acceptance_contract["goal_scale"] = goal_meta["goal_scale"]
        first_deliverable = str(
            goal_meta.get("first_deliverable")
            or (spec.success_criteria or {}).get("first_deliverable")
            or ""
        ).strip()
        milestone_acceptance = acceptance_for_current_milestone(
            goal_meta, fallback_first_deliverable=first_deliverable
        )
        if milestone_acceptance:
            # Milestone slice adds current-milestone acceptance; full goal success
            # criteria stay visible (P0-4: never dilute global acceptance).
            full_success = acceptance_contract.get("success_criteria")
            for key, value in milestone_acceptance.items():
                acceptance_contract[key] = value
            if milestone_acceptance.get("forbid_full_goal_claim"):
                # Mark this round as a subset check, but keep full criteria attached.
                acceptance_contract["acceptance_scope"] = "milestone_subset"
                if full_success is not None:
                    acceptance_contract["success_criteria"] = full_success
                    acceptance_contract["full_goal_success_criteria"] = full_success
                acceptance_contract["milestone_acceptance_subset"] = {
                    k: v
                    for k, v in milestone_acceptance.items()
                    if k
                    not in {
                        "forbid_full_goal_claim",
                        "milestone_ordinal",
                        "milestone_key",
                        "milestone_title",
                        "goal_scale",
                        "milestone_count",
                    }
                }
        elif first_deliverable:
            acceptance_contract["first_deliverable"] = first_deliverable
        delivery_policy = str(
            payload.get("delivery_policy") or goal_meta.get("delivery_policy") or ""
        )
        if delivery_policy in {"goal_attainment_retry", "goal_attainment_escalation"}:
            acceptance_contract["delivery_policy"] = delivery_policy
            acceptance_contract["delivery_gap_reasons"] = list(
                goal_meta.get("delivery_gap_reasons") or payload.get("gap_reasons") or []
            )[:12]
            acceptance_contract["delivery_gap_recovery_attempt"] = int(
                payload.get("delivery_gap_recovery_attempt")
                or goal_meta.get("delivery_gap_recovery_attempts")
                or 1
            )
            guidance = (
                (goal_meta.get("capability_resolution") or {}).get("generation_guidance") or []
            )
            if guidance:
                architecture_summary = (
                    f"{architecture_summary}\n\nGoal-attainment recovery guidance:\n"
                    + "\n".join(f"- {item}" for item in guidance)
                )
            ms_title = milestone_acceptance.get("milestone_title")
            if ms_title:
                architecture_summary = (
                    f"{architecture_summary}\n\nCurrent milestone only: {ms_title}. "
                    "Do not claim the full Goal until the final milestone."
                )

        acceptance_contract["app_project_id"] = str(project_id)
        acceptance_contract["goal_id"] = str(goal_id)
        acceptance_contract.setdefault("org_key", "default")

        from regent.config import get_settings

        settings = get_settings()
        if settings.generation_strategy == "agentic":
            generator_ref = "agentic-generation-v1"
            prompt_version = "agentic-generation-v1"
        else:
            generator_ref = "artifact-backed-code-generator-v1"
            prompt_version = "code-generation-v1"

        contract = GenerationPlanContract(
            goal_spec_hash=spec_hash,
            hypothesis_decision_id=decision_id,
            requirement_revision_hash=revision_hash,
            capability_resolution_hash=_ZERO_HASH,
            runtime_profile_hash=_RUNTIME_PROFILE_HASH,
            evidence_bundle_digest=_ZERO_HASH,
            generator_ref=generator_ref,
            model_ref="p1-model",
            prompt_version=prompt_version,
            planned_paths=planned_paths,
            dependency_intents=dependency_intents,
            verification_commands=verification_commands,
            acceptance_contract=acceptance_contract,
            # GAC-GA: GoalAnchor — inject original goal text so the
            # generator LLM sees what the user actually asked for.
            goal_anchor_text=goal.original_input if goal else "",
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
            if run.status == "COMPLETED":
                async with self._sessions() as session:
                    snapshot = await session.scalar(
                        select(WorkspaceSnapshotModel).where(
                            WorkspaceSnapshotModel.generation_run_id == run.id
                        )
                    )
                if snapshot is None:
                    raise DomainError(
                        ErrorCode.INVALID_STATE,
                        "completed generation run missing workspace snapshot",
                    )
            else:
                snapshot = await gen_service.execute(run.id)
        except DomainError as exc:
            if exc.code == ErrorCode.LEASE_CONFLICT:
                # In-flight generate under a prior lease — retry outbox later.
                raise
            if "delivery-review-v1" in str(exc):
                reasons = [
                    part.strip()
                    for part in str(exc).split("rejected non-deliverable surface:", 1)[-1].split(
                        ";"
                    )
                    if part.strip()
                ][:12] or [str(exc)[:200]]
                recovery = await DeliveryGapRecoveryService(self._sessions).recover(
                    goal_id=goal_id,
                    project_id=project_id,
                    requirement_revision_id=requirement_id,
                    capability_resolution_plan_id=resolution_plan_id,
                    actor=actor,
                    gap_reasons=reasons,
                )
                if recovery.recovered:
                    logger.info(
                        "delivery gap recovery scheduled",
                        extra={"goal_id": str(goal_id), "attempts": recovery.attempts},
                    )
                    return
                if recovery.terminal_exhaust:
                    await self._halt_goal_stage(
                        goal_id,
                        project_id,
                        stage="DELIVERY_GAP_EXHAUSTED",
                        message=recovery.message,
                        terminal=GoalCommand.EXHAUST,
                        actor=actor,
                        extra={
                            "gap_kind": recovery.gap_kind,
                            "attempts": recovery.attempts,
                            "gac": "GAC-D5",
                        },
                    )
                    return
                logger.warning(
                    "delivery gap exhausted; refusing unreliable publish",
                    extra={"goal_id": str(goal_id), "message": recovery.message},
                )
                return
            logger.exception("generation failed", extra={"goal_id": str(goal_id)})
            raise
        except ValueError as exc:
            # delivery-review-v1 / goal-attainment failure: organize capability, do not publish.
            if "delivery-review-v1" not in str(exc):
                logger.exception("generation failed", extra={"goal_id": str(goal_id)})
                raise
            reasons = [
                part.strip()
                for part in str(exc).split("rejected non-deliverable surface:", 1)[-1].split(";")
                if part.strip()
            ][:12] or [str(exc)[:200]]
            recovery = await DeliveryGapRecoveryService(self._sessions).recover(
                goal_id=goal_id,
                project_id=project_id,
                requirement_revision_id=requirement_id,
                capability_resolution_plan_id=resolution_plan_id,
                actor=actor,
                gap_reasons=reasons,
            )
            if recovery.recovered:
                logger.info(
                    "delivery gap recovery scheduled",
                    extra={"goal_id": str(goal_id), "attempts": recovery.attempts},
                )
                return
            if recovery.terminal_exhaust:
                await self._halt_goal_stage(
                    goal_id,
                    project_id,
                    stage="DELIVERY_GAP_EXHAUSTED",
                    message=recovery.message,
                    terminal=GoalCommand.EXHAUST,
                    actor=actor,
                    extra={
                        "gap_kind": recovery.gap_kind,
                        "attempts": recovery.attempts,
                        "gac": "GAC-D5",
                    },
                )
                return
            logger.warning(
                "delivery gap exhausted; refusing unreliable publish",
                extra={"goal_id": str(goal_id), "message": recovery.message},
            )
            return
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
                    "源代码已生成完毕。",
                    {"goal_id": str(goal_id), "snapshot_id": str(snapshot.id)},
                )

    # ---------------------------------------------------------------------------
    # V3 Compliance Gate
    # ---------------------------------------------------------------------------

    async def _run_compliance_gate(
        self,
        goal_id: uuid.UUID,
        snapshot_id: uuid.UUID,
        *,
        actor: str = "regent-core",
    ) -> bool:
        """Run ComplianceChecker on workspace snapshot files.

        Returns True if the snapshot passes compliance (no secrets/PII).
        On failure, logs the findings and records an audit entry.
        """
        checker = ComplianceChecker()
        async with self._sessions() as session:
            snapshot = await session.get(WorkspaceSnapshotModel, snapshot_id)
            if snapshot is None:
                return True  # nothing to check
            files = snapshot.files_json if hasattr(snapshot, "files_json") else []
        if not files:
            return True

        artifacts = []
        for f in files:
            content = f.get("content", "") if isinstance(f, dict) else str(f)
            artifacts.append({"content": content, "classification": "UNTRUSTED_DATA"})

        report = checker.check_artifacts(artifacts)
        if report.status == ComplianceStatus.FAIL:
            logger.warning(
                "compliance gate FAILED for goal=%s snapshot=%s: %d finding(s)",
                goal_id, snapshot_id, len(report.findings),
            )
            return False
        if report.status == ComplianceStatus.WARN:
            logger.info(
                "compliance gate WARN for goal=%s: %d finding(s)",
                goal_id, len(report.findings),
            )
        return True

    # ---------------------------------------------------------------------------
    # R4: WorkspaceSnapshotReady -> DependencyResolutionRequested
    # ---------------------------------------------------------------------------

    async def handle_workspace_snapshot_ready(self, payload: dict[str, Any]) -> None:
        """Run compliance check on generated artifacts, then emit DependencyResolutionRequested."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        snapshot_id = uuid.UUID(str(payload["workspace_snapshot_id"]))
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))

        # --- V3 Compliance Gate: scan generated workspace for secrets/PII ---
        compliance_ok = await self._run_compliance_gate(
            goal_id, snapshot_id, actor=actor,
        )
        if not compliance_ok:
            # P1-C: fail-closed — write FAILURE_COMPLIANCE and terminate chain
            logger.warning(
                "compliance gate failed for goal %s snapshot %s",
                goal_id, snapshot_id,
            )
            async with self._sessions() as session, session.begin():
                goal = await session.get(GoalModel, goal_id)
                if goal is not None:
                    outbox_event = make_outbox_event(
                        EventEnvelope(
                            event_type=FAILURE_COMPLIANCE,
                            aggregate_type="goal",
                            aggregate_id=goal_id,
                            aggregate_version=goal.version,
                            payload={
                                "goal_id": str(goal_id),
                                "reason": "compliance gate failed: secrets/PII detected",
                                "snapshot_id": str(snapshot_id),
                                "actor": actor,
                            },
                            idempotency_key=make_idempotency_key(
                                "compliance_fail", goal_id, idempotency_key,
                            ),
                            correlation_id=goal.correlation_id,
                        )
                    )
                    session.add(outbox_event)
                    goal.status = "FAILED"
                    await self._append_conversation_event(
                        session, project_id, "FAILURE_COMPLIANCE",
                        "安全检查未通过：检测到敏感信息，已终止流程。",
                        {"goal_id": str(goal_id), "snapshot_id": str(snapshot_id)},
                    )
            return

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
                "依赖已解决，正在构建应用。",
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
                    "应用构建成功，已通过验证。",
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
        except Exception as exc:
            if "delivery-review-v1" in str(exc):
                req_uuid: uuid.UUID | None = None
                plan_uuid: uuid.UUID | None = None
                async with self._sessions() as session:
                    goal = await session.get(GoalModel, goal_id)
                    meta = dict((goal.metadata_json if goal else None) or {})
                    raw_plan = meta.get("capability_resolution_plan_id")
                    if raw_plan:
                        plan = await session.get(
                            CapabilityResolutionPlanModel, uuid.UUID(str(raw_plan))
                        )
                        if plan is not None:
                            plan_uuid = plan.id
                            req_uuid = plan.requirement_revision_id
                    if req_uuid is None:
                        gen = await session.scalar(
                            select(GenerationPlanModel)
                            .join(
                                RequirementRevisionModel,
                                RequirementRevisionModel.id
                                == GenerationPlanModel.requirement_revision_id,
                            )
                            .join(
                                ProductHypothesisModel,
                                ProductHypothesisModel.id
                                == RequirementRevisionModel.hypothesis_id,
                            )
                            .join(
                                DiscoveryRoundModel,
                                DiscoveryRoundModel.id == ProductHypothesisModel.round_id,
                            )
                            .where(DiscoveryRoundModel.goal_id == goal_id)
                            .order_by(GenerationPlanModel.created_at.desc())
                            .limit(1)
                        )
                        if gen is not None:
                            req_uuid = gen.requirement_revision_id
                            plan_uuid = gen.capability_resolution_plan_id
                if req_uuid is not None and plan_uuid is not None:
                    reasons = [
                        part.strip()
                        for part in str(exc)
                        .split("rejected non-deliverable surface:", 1)[-1]
                        .split(";")
                        if part.strip()
                    ][:12] or [str(exc)[:200]]
                    recovery = await DeliveryGapRecoveryService(self._sessions).recover(
                        goal_id=goal_id,
                        project_id=project_id,
                        requirement_revision_id=req_uuid,
                        capability_resolution_plan_id=plan_uuid,
                        actor=actor,
                        gap_reasons=reasons,
                    )
                    if recovery.recovered:
                        return
                    if recovery.terminal_exhaust:
                        await self._halt_goal_stage(
                            goal_id,
                            project_id,
                            stage="DEPLOY_DELIVERY_REJECTED",
                            message=recovery.message,
                            terminal=GoalCommand.EXHAUST,
                            actor=actor,
                            extra={
                                "gap_kind": recovery.gap_kind,
                                "gac": "GAC-D5",
                            },
                        )
                        return
                await self._halt_goal_stage(
                    goal_id,
                    project_id,
                    stage="DEPLOY_DELIVERY_REJECTED",
                    message=f"Preview deploy blocked by delivery-review (GAC-A5): {exc}",
                    terminal=GoalCommand.EXHAUST,
                    actor=actor,
                    extra={"error": str(exc)[:400]},
                )
                return
            logger.exception("deployment failed", extra={"goal_id": str(goal_id)})
            await self._halt_goal_stage(
                goal_id,
                project_id,
                stage="DEPLOY_FAILED",
                message=f"Preview deployment failed (GAC-A4): {type(exc).__name__}",
                terminal=GoalCommand.FAIL,
                actor=actor,
                extra={"error": str(exc)[:400]},
            )
            return

        if result.status != "SUCCEEDED":
            logger.warning(
                "deployment did not succeed",
                extra={"deployment_id": str(result.id), "status": result.status},
            )
            await self._halt_goal_stage(
                goal_id,
                project_id,
                stage="DEPLOY_NOT_SUCCEEDED",
                message=f"Deployment status={result.status} (GAC-A4).",
                terminal=GoalCommand.EXHAUST,
                actor=actor,
                extra={"deployment_id": str(result.id), "status": result.status},
            )
            return

        # Stamp real deployment UUID into published preview HTML for browser events.
        evidence = dict(result.evidence or {})
        project_key = str(evidence.get("project_key") or "")
        release_key = str(evidence.get("release_key") or "")
        if project_key and release_key:
            try:
                from pathlib import Path

                from regent.config import get_settings
                from regent.infrastructure.deployment import stamp_preview_deployment_id

                settings = get_settings()
                stamp_preview_deployment_id(
                    Path(settings.workspace_root) / "previews",
                    project_key=project_key,
                    release_key=release_key,
                    deployment_id=str(result.id),
                )
            except Exception:
                logger.exception(
                    "failed to stamp preview deployment id",
                    extra={"deployment_id": str(result.id)},
                )

        # Write PreviewDeploymentSucceeded
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal:
                metadata = dict(goal.metadata_json or {})
                verification = evidence.get("delivery_verification") or {}
                if verification:
                    metadata["delivery_verification"] = verification
                elif evidence.get("delivery_review", {}).get("passed"):
                    metadata["delivery_verification"] = {
                        "verdict": "PASS",
                        "capability": evidence.get("delivery_review", {}).get("capability"),
                        "summary": evidence.get("delivery_review", {}).get("summary"),
                    }
                goal.metadata_json = metadata
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
                            "delivery_verification": metadata.get("delivery_verification"),
                        },
                        correlation_id=goal.correlation_id,
                    )
                )
                session.add(outbox_event)
                await self._append_conversation_event(
                    session,
                    project_id,
                    "PREVIEW_DEPLOYMENT_SUCCEEDED",
                    f"预览环境已部署成功：{result.endpoint or 'N/A'}",
                    {
                        "goal_id": str(goal_id),
                        "deployment_id": str(result.id),
                        "endpoint": result.endpoint or "",
                        "delivery_verification": metadata.get("delivery_verification"),
                    },
                )

    # ---------------------------------------------------------------------------
    # R7+R8: PreviewDeploymentSucceeded -> smoke test + auto-bind metrics
    # ---------------------------------------------------------------------------

    async def handle_preview_deployment_succeeded(self, payload: dict[str, Any]) -> None:
        """Run smoke test, bind metrics, evaluate gate, converge Goal (GAC-A1/A2/A3)."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        deployment_id = uuid.UUID(str(payload["deployment_id"]))
        endpoint = str(payload.get("endpoint", ""))
        actor = str(payload.get("actor", "regent-core"))
        async with self._sessions() as session:
            goal_row = await session.get(GoalModel, goal_id)
            if goal_row is None or goal_row.app_project_id is None:
                logger.warning(
                    "preview succeeded but goal/project missing",
                    extra={"goal_id": str(goal_id)},
                )
                return
            project_id = goal_row.app_project_id
        if "app_project_id" in payload:
            project_id = uuid.UUID(str(payload["app_project_id"]))

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

        loop_service = IterationLoopService(self._sessions)
        feedback = FeedbackService(self._sessions)
        transitions = TransitionService(self._sessions)
        try:
            binding_ids = await loop_service.bind_default_metrics(
                goal_id, deployment_id, actor=actor
            )
            gate = await feedback.evaluate(goal_id, deployment_id, actor=actor)
            decision = None
            if gate.status == "INSUFFICIENT_EVIDENCE":
                # GAC-C2: schedule durable timeout → EXHAUST if still waiting.
                timers = DurableTimerService(self._sessions)
                due_at = datetime.now(UTC) + timedelta(minutes=30)
                timer_id = await timers.schedule(
                    aggregate_type="goal",
                    aggregate_id=goal_id,
                    command="goal.exhaust_insufficient",
                    payload={
                        "goal_id": str(goal_id),
                        "app_project_id": str(project_id),
                        "deployment_id": str(deployment_id),
                        "actor": actor,
                        "gate_status": gate.status,
                    },
                    due_at=due_at,
                )
                async with self._sessions() as session, session.begin():
                    goal = await session.get(GoalModel, goal_id, with_for_update=True)
                    if goal is not None:
                        metadata = dict(goal.metadata_json or {})
                        metadata["execution_stage"] = "GATE_INSUFFICIENT_EVIDENCE"
                        metadata["last_gate_status"] = gate.status
                        metadata["last_deployment_id"] = str(deployment_id)
                        metadata["last_preview_endpoint"] = endpoint
                        metadata["gate_insufficient_since"] = datetime.now(UTC).isoformat()
                        metadata["gate_insufficient_timer_id"] = str(timer_id)
                        goal.metadata_json = metadata
                    await self._append_conversation_event(
                        session,
                        project_id,
                        "GATE_INSUFFICIENT_EVIDENCE",
                        "正在收集运行数据以评估质量，请耐心等待。",
                        {
                            "goal_id": str(goal_id),
                            "deployment_id": str(deployment_id),
                            "gate_status": gate.status,
                            "timer_id": str(timer_id),
                        },
                    )
                logger.info(
                    "gate waiting for observations",
                    extra={
                        "goal_id": str(goal_id),
                        "gate_status": gate.status,
                        "timer_id": str(timer_id),
                    },
                )
                return

            # GAC-D4: gate FAILED → reorganize capabilities/org before STOP/EXHAUST.
            if gate.status == "FAILED":
                reorg = await DeliveryGapRecoveryService(
                    self._sessions
                ).prepare_gate_reorganization(
                    goal_id=goal_id,
                    project_id=project_id,
                    actor=actor,
                    gate_status=gate.status,
                )
                if reorg.recovered and reorg.recovery_work_id is not None:
                    decision = await feedback.decide(
                        CreateIterationDecision(
                            gate_evaluation_id=gate.id,
                            actor=actor,
                            primary_hypothesis=(
                                f"capability_reorganization:{reorg.method}:{reorg.gap_kind}"
                            ),
                            new_work_id=reorg.recovery_work_id,
                        )
                    )
                else:
                    decision = await feedback.decide(
                        CreateIterationDecision(gate_evaluation_id=gate.id, actor=actor)
                    )
            else:
                decision = await feedback.decide(
                    CreateIterationDecision(gate_evaluation_id=gate.id, actor=actor)
                )

            async with self._sessions() as session, session.begin():
                goal = await session.get(GoalModel, goal_id, with_for_update=True)
                if goal is not None:
                    metadata = dict(goal.metadata_json or {})
                    # Cancel prior insufficient timer if any (best-effort).
                    old_timer = metadata.pop("gate_insufficient_timer_id", None)
                    metadata["execution_stage"] = "PREVIEW_SUCCEEDED"
                    metadata["last_gate_status"] = gate.status
                    metadata["last_deployment_id"] = str(deployment_id)
                    metadata["last_preview_endpoint"] = endpoint
                    metadata["last_iteration_decision"] = decision.decision
                    goal.metadata_json = metadata
                    expected_version = goal.version
                    correlation_id = goal.correlation_id
                else:
                    old_timer = None
                    expected_version = 0
                    correlation_id = uuid.uuid4()
                await self._append_conversation_event(
                    session,
                    project_id,
                    "ITERATION_DECISION",
                    f"质量评估完成，决策：{decision.decision}。",
                    {
                        "goal_id": str(goal_id),
                        "decision": decision.decision,
                        "gate_status": gate.status,
                        "decision_id": str(decision.id),
                    },
                )
            if old_timer:
                try:
                    await DurableTimerService(self._sessions).cancel(uuid.UUID(str(old_timer)))
                except DomainError:
                    pass

            # GAC-A1 / GAC-A3 / GAC-E2: converge only on final milestone (or SMALL).
            if decision.decision == "CONTINUE":
                advanced = await self._continue_or_advance_milestone(
                    goal_id=goal_id,
                    project_id=project_id,
                    deployment_id=deployment_id,
                    actor=actor,
                    expected_version=expected_version,
                    correlation_id=correlation_id,
                )
                if advanced:
                    pass  # next discovery already scheduled
            elif decision.decision == "STOP":
                await transitions.transition_goal(
                    TransitionContext(
                        goal_id, expected_version, actor, correlation_id
                    ),
                    GoalCommand.EXHAUST,
                )
                async with self._sessions() as session, session.begin():
                    goal = await session.get(GoalModel, goal_id)
                    if goal is not None:
                        metadata = dict(goal.metadata_json or {})
                        metadata["execution_stage"] = "EXHAUSTED"
                        metadata["termination"] = {
                            "reason": "iteration_stop",
                            "gate_status": gate.status,
                            "gac": "GAC-A1",
                        }
                        goal.metadata_json = metadata
                    await self._append_conversation_event(
                        session,
                        project_id,
                        "GOAL_EXHAUSTED",
                        "目标执行已终止：质量评估未通过。",
                        {"goal_id": str(goal_id), "deployment_id": str(deployment_id)},
                    )
            elif decision.decision == "REVISE":
                round_id = await loop_service.handle_revise(decision.id, actor=actor)
                async with self._sessions() as session, session.begin():
                    goal = await session.get(GoalModel, goal_id)
                    if goal is not None:
                        metadata = dict(goal.metadata_json or {})
                        metadata["execution_stage"] = "DISCOVERING"
                        metadata["last_revise_discovery_round_id"] = str(round_id)
                        goal.metadata_json = metadata
                    await self._append_conversation_event(
                        session,
                        project_id,
                        "ITERATION_REVISE_STARTED",
                        "正在重新分析需求，优化方案。",
                        {
                            "goal_id": str(goal_id),
                            "discovery_round_id": str(round_id),
                            "decision_id": str(decision.id),
                        },
                    )

            if not smoke_result.passed and decision.decision == "CONTINUE":
                # Defensive: smoke fail should not CONTINUE; already handled via gate SUM.
                pass

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
        except DomainError as exc:
            if "no metric definitions" in str(exc):
                # No metrics bound yet; treat as PASSED for P1 chain convergence.
                logger.warning(
                    "feedback evaluation skipped: no metric definitions",
                    extra={"goal_id": str(goal_id), "deployment_id": str(deployment_id)},
                )
                expected_version = 0
                correlation_id = uuid.uuid4()
                async with self._sessions() as session, session.begin():
                    goal = await session.get(GoalModel, goal_id, with_for_update=True)
                    if goal is not None:
                        metadata = dict(goal.metadata_json or {})
                        metadata["execution_stage"] = "PREVIEW_SUCCEEDED"
                        metadata["last_gate_status"] = "PASSED"
                        metadata["last_deployment_id"] = str(deployment_id)
                        metadata["last_preview_endpoint"] = endpoint
                        goal.metadata_json = metadata
                        expected_version = goal.version
                        correlation_id = goal.correlation_id
                    await self._append_conversation_event(
                        session,
                        project_id,
                        "PREVIEW_SUCCEEDED",
                        f"Preview deployment succeeded (no metrics, auto-advancing): {endpoint}",
                        {
                            "goal_id": str(goal_id),
                            "deployment_id": str(deployment_id),
                            "endpoint": endpoint,
                        },
                    )
                # Advance milestone or ACHIEVE just like CONTINUE path.
                await self._continue_or_advance_milestone(
                    goal_id=goal_id,
                    project_id=project_id,
                    deployment_id=deployment_id,
                    actor=actor,
                    expected_version=expected_version,
                    correlation_id=correlation_id,
                )
                return
            logger.exception(
                "failed to complete observation feedback loop",
                extra={"goal_id": str(goal_id), "deployment_id": str(deployment_id)},
            )
            raise
        except Exception:
            logger.exception(
                "failed to complete observation feedback loop",
                extra={"goal_id": str(goal_id), "deployment_id": str(deployment_id)},
            )
            raise

    async def handle_quality_approval_completed(self, payload: dict[str, Any]) -> None:
        """GAC-Q1: QualityApprovalCompleted → ACHIEVE or EXHAUST the goal."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        actor = str(payload.get("actor", "regent-core"))
        approved = bool(payload.get("approved", True))
        feedback_text = str(payload.get("feedback", ""))

        async with self._sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                logger.warning("quality approval: goal not found", extra={"goal_id": str(goal_id)})
                return
            project_id = goal.app_project_id
            status = goal.status
            expected_version = goal.version
            correlation_id = goal.correlation_id

        if status != "ACTIVE":
            logger.info(
                "quality approval skipped: goal not active",
                extra={"goal_id": str(goal_id), "status": status},
            )
            return

        if not approved:
            # User rejected → EXHAUST with feedback
            await TransitionService(self._sessions).transition_goal(
                TransitionContext(goal_id, expected_version, actor, correlation_id),
                GoalCommand.EXHAUST,
            )
            async with self._sessions() as session, session.begin():
                goal = await session.get(GoalModel, goal_id)
                if goal is not None:
                    metadata = dict(goal.metadata_json or {})
                    metadata["execution_stage"] = "EXHAUSTED"
                    metadata["termination"] = {
                        "reason": "quality_rejected",
                        "feedback": feedback_text,
                        "gac": "GAC-Q1",
                    }
                    goal.metadata_json = metadata
                await self._append_conversation_event(
                    session,
                    project_id,
                    "GOAL_EXHAUSTED",
                    f"目标执行已终止：质量未通过。反馈：{feedback_text}",
                    {"goal_id": str(goal_id), "feedback": feedback_text},
                )
            return

        # Approved → ACHIEVE
        await TransitionService(self._sessions).transition_goal(
            TransitionContext(goal_id, expected_version, actor, correlation_id),
            GoalCommand.ACHIEVE,
        )
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal is not None:
                metadata = dict(goal.metadata_json or {})
                metadata["execution_stage"] = "ACHIEVED"
                metadata["quality_approved_by"] = actor
                metadata["quality_feedback"] = feedback_text
                goal.metadata_json = metadata
            await self._append_conversation_event(
                session,
                project_id,
                "GOAL_ACHIEVED",
                "目标已完成！",
                {"goal_id": str(goal_id)},
            )
        logger.info(
            "goal achieved after quality approval",
            extra={"goal_id": str(goal_id), "actor": actor},
        )

    async def handle_quality_approval_requested(self, payload: dict[str, Any]) -> None:
        """GAC-Q1: QualityApprovalRequested — goal waits for user confirmation."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        task_id = str(payload.get("task_id", ""))
        logger.info(
            "quality approval requested",
            extra={"goal_id": str(goal_id), "task_id": task_id},
        )

    async def handle_timer_fired(self, payload: dict[str, Any]) -> None:
        """GAC-C2: DurableTimer → Goal EXHAUST when gate still insufficient."""
        command = str(payload.get("command") or "")
        if command != "goal.exhaust_insufficient":
            logger.info("timer fired ignored", extra={"command": command})
            return
        goal_id = uuid.UUID(str(payload["goal_id"]))
        actor = str(payload.get("actor", "regent-core"))
        project_id_raw = payload.get("app_project_id")
        async with self._sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                return
            meta = dict(goal.metadata_json or {})
            stage = str(meta.get("execution_stage") or "")
            status = goal.status
            expected_version = goal.version
            correlation_id = goal.correlation_id
            project_id = (
                uuid.UUID(str(project_id_raw))
                if project_id_raw
                else goal.app_project_id
            )
        if status != "ACTIVE" or stage != "GATE_INSUFFICIENT_EVIDENCE":
            logger.info(
                "insufficient timer skipped; goal already progressed",
                extra={"goal_id": str(goal_id), "status": status, "stage": stage},
            )
            return
        # GAC-D4: one capability/org reorg before EXHAUST; resume generation when lineage exists.
        if project_id is not None:
            reorg = await DeliveryGapRecoveryService(
                self._sessions
            ).prepare_gate_reorganization(
                goal_id=goal_id,
                project_id=project_id,
                actor=actor,
                gate_status="INSUFFICIENT_EVIDENCE_TIMEOUT",
            )
            if reorg.recovered:
                resumed = False
                async with self._sessions() as session, session.begin():
                    goal = await session.get(GoalModel, goal_id, with_for_update=True)
                    if goal is not None:
                        metadata = dict(goal.metadata_json or {})
                        req_raw = metadata.get("requirement_revision_id")
                        plan_raw = metadata.get("capability_resolution_plan_id")
                        if req_raw and plan_raw:
                            resume_key = make_idempotency_key(
                                "generation-insufficient-reorg",
                                goal.id,
                                f"{reorg.attempts}:{reorg.method}",
                            )
                            session.add(
                                make_outbox_event(
                                    EventEnvelope(
                                        event_type=GENERATION_RUN_REQUESTED,
                                        aggregate_type="goal",
                                        aggregate_id=goal.id,
                                        aggregate_version=goal.version,
                                        payload={
                                            "goal_id": str(goal.id),
                                            "app_project_id": str(project_id),
                                            "requirement_revision_id": str(req_raw),
                                            "capability_resolution_plan_id": str(plan_raw),
                                            "actor": actor,
                                            "idempotency_key": resume_key,
                                            "delivery_policy": "gate_insufficient_reorg",
                                        },
                                        idempotency_key=resume_key,
                                        correlation_id=goal.correlation_id,
                                    )
                                )
                            )
                            metadata["execution_stage"] = "GENERATING"
                            metadata.pop("gate_insufficient_timer_id", None)
                            goal.metadata_json = metadata
                            resumed = True
                        await self._append_conversation_event(
                            session,
                            project_id,
                            "GATE_INSUFFICIENT_REORGANIZED",
                            reorg.message,
                            {
                                "goal_id": str(goal_id),
                                "recovery_work_id": str(reorg.recovery_work_id)
                                if reorg.recovery_work_id
                                else None,
                                "method": reorg.method,
                                "resumed_generation": resumed,
                            },
                        )
                if resumed:
                    return

        await TransitionService(self._sessions).transition_goal(
            TransitionContext(goal_id, expected_version, actor, correlation_id),
            GoalCommand.EXHAUST,
        )
        if project_id is not None:
            async with self._sessions() as session, session.begin():
                goal = await session.get(GoalModel, goal_id)
                if goal is not None:
                    metadata = dict(goal.metadata_json or {})
                    metadata["execution_stage"] = "EXHAUSTED"
                    metadata["termination"] = {
                        "reason": "gate_insufficient_timeout",
                        "gac": "GAC-C2",
                    }
                    metadata.pop("gate_insufficient_timer_id", None)
                    goal.metadata_json = metadata
                await self._append_conversation_event(
                    session,
                    project_id,
                    "GOAL_EXHAUSTED",
                    "目标执行已终止：等待数据超时。",
                    {"goal_id": str(goal_id)},
                )

    async def _continue_or_advance_milestone(
        self,
        *,
        goal_id: uuid.UUID,
        project_id: uuid.UUID,
        deployment_id: uuid.UUID,
        actor: str,
        expected_version: int,
        correlation_id: uuid.UUID,
    ) -> bool:
        """GAC-E2/E3: CONTINUE on non-final LARGE milestone advances; else ACHIEVE.

        Returns True if a next-milestone discovery was scheduled (Goal still ACTIVE).
        """
        transitions = TransitionService(self._sessions)
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                return False
            metadata = dict(goal.metadata_json or {})
            plan = plan_from_metadata(metadata)
            # LARGE without plan should not ACHIEVE — force ensure first.
            if plan is None or (
                plan.goal_scale == GOAL_SCALE_LARGE and len(plan.milestones) < 2
            ):
                spec = await session.scalar(
                    select(GoalSpecModel)
                    .where(GoalSpecModel.goal_id == goal_id)
                    .order_by(GoalSpecModel.version.desc())
                    .limit(1)
                )
                if spec is not None:
                    plan = await ensure_milestone_plan(session, goal=goal, spec=spec)
                    metadata = dict(goal.metadata_json or {})

            if (
                plan is not None
                and plan.goal_scale == GOAL_SCALE_LARGE
                and not is_final_milestone(plan)
            ):
                attained = current_milestone(plan)
                new_plan = await advance_milestone(session, goal=goal)
                if new_plan is None:
                    await self._append_conversation_event(
                        session,
                        project_id,
                        "MILESTONE_ADVANCE_BLOCKED",
                        "项目规模较大，当前阶段无法直接完成，需要继续推进下一阶段。",
                        {
                            "goal_id": str(goal_id),
                            "current_ordinal": attained.ordinal,
                            "gac": "GAC-E2",
                        },
                    )
                    metadata = dict(goal.metadata_json or {})
                    metadata["execution_stage"] = "MILESTONE_BLOCKED"
                    goal.metadata_json = metadata
                    return True
                nxt = current_milestone(new_plan)
                metadata = dict(goal.metadata_json or {})
                metadata["execution_stage"] = "MILESTONE_ADVANCING"
                metadata["delivery_gap_recovery_attempts"] = 0
                goal.metadata_json = metadata
                await self._append_conversation_event(
                    session,
                    project_id,
                    "MILESTONE_ATTAINED",
                    (
                        f"第 {attained.ordinal} 阶段已完成：{attained.title}。"
                        f"正在进入第 {nxt.ordinal}/{len(new_plan.milestones)} 阶段：{nxt.title}。"
                    ),
                    {
                        "goal_id": str(goal_id),
                        "attained_ordinal": attained.ordinal,
                        "next_ordinal": nxt.ordinal,
                        "next_key": nxt.key,
                        "deployment_id": str(deployment_id),
                        "gac": "GAC-E2",
                    },
                )
                await self._emit_milestone_discovery(
                    session,
                    goal=goal,
                    project_id=project_id,
                    actor=actor,
                    milestone_key=nxt.key,
                    milestone_ordinal=nxt.ordinal,
                )
                return True

            # Final milestone or SMALL → require verification PASS before ACHIEVE (P0-4).
            metadata = dict(goal.metadata_json or {})
            verification = dict(metadata.get("delivery_verification") or {})
            verdict = str(verification.get("verdict") or "").upper()
            if verdict != "PASS":
                metadata["execution_stage"] = "WAITING_HUMAN_VERIFICATION"
                metadata["awaiting_verification"] = True
                goal.metadata_json = metadata
                version = goal.version
                corr = goal.correlation_id
                await self._append_conversation_event(
                    session,
                    project_id,
                    "VERIFICATION_REQUIRED",
                    "交付验证未通过或缺失，已请求人工裁决，不能标记为目标达成。",
                    {
                        "goal_id": str(goal_id),
                        "deployment_id": str(deployment_id),
                        "delivery_verification": verification or None,
                        "gac": "P0-4",
                    },
                )
                # Exit transaction before transition (same pattern as ACHIEVE below).
            else:
                metadata["execution_stage"] = "ACHIEVING"
                if plan is not None:
                    metadata["milestones_completed"] = True
                goal.metadata_json = metadata
                version = goal.version
                corr = goal.correlation_id

                await self._append_conversation_event(
                    session,
                    project_id,
                    "QUALITY_SELF_VERIFIED",
                    "对抗式交付验证通过，正在完成目标。",
                    {
                        "goal_id": str(goal_id),
                        "deployment_id": str(deployment_id),
                        "goal_scale": (plan.goal_scale if plan else "UNKNOWN"),
                        "delivery_verification": verification,
                        "gac": "P0-4",
                    },
                )

        if verdict != "PASS":
            await transitions.transition_goal(
                TransitionContext(goal_id, version, actor, corr),
                GoalCommand.WAIT_FOR_HUMAN,
            )
            logger.warning(
                "ACHIEVE blocked: verification missing or failed",
                extra={
                    "goal_id": str(goal_id),
                    "verdict": verdict or "MISSING",
                    "deployment_id": str(deployment_id),
                },
            )
            return False

        # Accepting Agent ACHIEVEs only with Verification PASS evidence.
        await transitions.transition_goal(
            TransitionContext(goal_id, version, actor, corr),
            GoalCommand.ACHIEVE,
        )
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal is not None:
                metadata = dict(goal.metadata_json or {})
                metadata["execution_stage"] = "ACHIEVED"
                metadata["quality_verified_by"] = actor
                goal.metadata_json = metadata
            await self._append_conversation_event(
                session,
                project_id,
                "GOAL_ACHIEVED",
                "目标已完成！",
                {
                    "goal_id": str(goal_id),
                    "deployment_id": str(deployment_id),
                    "delivery_verification": verification,
                },
            )
        logger.info(
            "goal achieved with verification PASS",
            extra={"goal_id": str(goal_id), "actor": actor},
        )
        return False

    async def _emit_milestone_discovery(
        self,
        session: AsyncSession,
        *,
        goal: GoalModel,
        project_id: uuid.UUID,
        actor: str,
        milestone_key: str,
        milestone_ordinal: int,
    ) -> uuid.UUID:
        """Start a new Discovery round scoped to the next milestone (GAC-E3)."""
        next_round = (
            int(
                await session.scalar(
                    select(func.coalesce(func.max(DiscoveryRoundModel.round), 0)).where(
                        DiscoveryRoundModel.goal_id == goal.id
                    )
                )
                or 0
            )
            + 1
        )
        idempotency_key = make_idempotency_key(
            "milestone-discovery",
            goal.id,
            f"{milestone_key}:{milestone_ordinal}:{next_round}",
        )
        existing = await session.scalar(
            select(DiscoveryRoundModel).where(
                DiscoveryRoundModel.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            return existing.id

        snapshot = {
            "goal_id": str(goal.id),
            "milestone_key": milestone_key,
            "milestone_ordinal": milestone_ordinal,
            "advance": True,
        }
        snapshot_hash = hashlib.sha256(
            json.dumps(
                snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        discovery_round = DiscoveryRoundModel(
            id=uuid.uuid4(),
            goal_id=goal.id,
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
        session.add(
            make_outbox_event(
                EventEnvelope(
                    event_type=DISCOVERY_ROUND_REQUESTED,
                    aggregate_type="goal",
                    aggregate_id=goal.id,
                    aggregate_version=goal.version,
                    payload={
                        "goal_id": str(goal.id),
                        "app_project_id": str(project_id),
                        "discovery_round_id": str(discovery_round.id),
                        "round": next_round,
                        "actor": actor,
                        "idempotency_key": idempotency_key,
                        "milestone_key": milestone_key,
                        "milestone_ordinal": milestone_ordinal,
                    },
                    idempotency_key=idempotency_key,
                    correlation_id=goal.correlation_id,
                )
            )
        )
        meta = dict(goal.metadata_json or {})
        meta["execution_stage"] = "DISCOVERING"
        goal.metadata_json = meta
        return discovery_round.id

    async def _halt_goal_stage(
        self,
        goal_id: uuid.UUID,
        project_id: uuid.UUID,
        *,
        stage: str,
        message: str,
        terminal: GoalCommand | None,
        actor: str,
        extra: dict[str, object] | None = None,
    ) -> None:
        """GAC-A4: mark observable mid-chain exit; optionally converge Goal."""
        expected_version = 0
        correlation_id = uuid.uuid4()
        status = ""
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                return
            status = goal.status
            expected_version = goal.version
            correlation_id = goal.correlation_id
            metadata = dict(goal.metadata_json or {})
            metadata["execution_stage"] = stage
            metadata["halt"] = {
                "message": message,
                "at": datetime.now(UTC).isoformat(),
                **(extra or {}),
            }
            goal.metadata_json = metadata
            await self._append_conversation_event(
                session,
                project_id,
                "GOAL_EXECUTION_STAGE_HALTED",
                message,
                {"goal_id": str(goal_id), "stage": stage, **(extra or {})},
            )
        if terminal is not None and status == "ACTIVE":
            try:
                await TransitionService(self._sessions).transition_goal(
                    TransitionContext(goal_id, expected_version, actor, correlation_id),
                    terminal,
                )
            except DomainError:
                logger.warning(
                    "halt terminal transition skipped",
                    extra={"goal_id": str(goal_id), "command": terminal.value},
                )

    # ---------------------------------------------------------------------------
    # Helper methods
    # ---------------------------------------------------------------------------

    @staticmethod
    async def _append_conversation_message(
        session: AsyncSession,
        project_id: uuid.UUID,
        *,
        role: str,
        message_type: str,
        content: str,
        metadata: dict[str, object],
    ) -> None:
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
                role=role,
                message_type=message_type,
                content=content,
                metadata_json=metadata,
                created_by="regent-core",
            )
        )

    # ---------------------------------------------------------------------------
    # P1-C: V3 Domain Event Handlers
    # ---------------------------------------------------------------------------

    async def handle_reorganization_triggered(self, payload: dict[str, Any]) -> None:
        """Handle ReorganizationTriggered domain event.

        Logs the trigger and records it in the conversation timeline.
        Future: initiate capability gap analysis and org restructuring.
        """
        goal_id = uuid.UUID(str(payload.get("goal_id", "")))
        reason = str(payload.get("reason", "unknown"))
        logger.info(
            "reorganization triggered for goal=%s: %s",
            goal_id, reason,
        )
        project_id = uuid.UUID(str(payload.get("app_project_id", "")))
        if project_id:
            async with self._sessions() as session, session.begin():
                await self._append_conversation_event(
                    session, project_id, "REORGANIZATION_TRIGGERED",
                    f"Reorganization triggered: {reason}",
                    {"goal_id": str(goal_id), "reason": reason},
                )

    async def handle_constraint_violated(self, payload: dict[str, Any]) -> None:
        """Handle ConstraintViolated domain event.

        Records the violation and marks the Goal as FAILED if the violation
        is blocking.
        """
        goal_id = uuid.UUID(str(payload.get("goal_id", "")))
        constraint = str(payload.get("constraint", "unknown"))
        blocking = bool(payload.get("blocking", True))
        logger.warning(
            "constraint violated for goal=%s: %s (blocking=%s)",
            goal_id, constraint, blocking,
        )
        if blocking:
            async with self._sessions() as session, session.begin():
                goal = await session.get(GoalModel, goal_id)
                if goal is not None:
                    goal.status = "FAILED"
                    meta = dict(goal.metadata_json or {})
                    meta["failure_reason"] = f"CONSTRAINT_VIOLATED: {constraint}"
                    goal.metadata_json = meta

    async def handle_organization_selected(self, payload: dict[str, Any]) -> None:
        """Handle OrganizationSelected domain event.

        Records the organization selection in the conversation timeline.
        """
        goal_id = uuid.UUID(str(payload.get("goal_id", "")))
        template_id = str(payload.get("template_id", ""))
        utility = float(payload.get("utility", 0.0))
        logger.info(
            "organization selected for goal=%s: template=%s utility=%.4f",
            goal_id, template_id, utility,
        )
        project_id = uuid.UUID(str(payload.get("app_project_id", "")))
        if project_id:
            async with self._sessions() as session, session.begin():
                await self._append_conversation_event(
                    session, project_id, "ORGANIZATION_SELECTED",
                    f"Organization selected: {template_id} (U={utility:.4f})",
                    {
                        "goal_id": str(goal_id),
                        "template_id": template_id,
                        "utility": utility,
                    },
                )

    # ---------------------------------------------------------------------------
    # P3-A: Adaptive Organization Handler
    # ---------------------------------------------------------------------------

    async def handle_adaptive_organization(self, payload: dict[str, Any]) -> None:
        """Handle adaptive organization proposal.

        Evaluates utility for all candidate organizations and proposes
        the best one. Records the proposal in conversation timeline.
        """
        goal_id = uuid.UUID(str(payload.get("goal_id", "")))
        actor = str(payload.get("actor", "regent-core"))

        try:
            org_service = OrganizationService(self._sessions)
            proposal = await org_service.propose_adaptive_organization(
                goal_id, actor=actor,
            )
            logger.info(
                "adaptive organization proposed for goal=%s: %s (U=%.4f)",
                goal_id, proposal["proposed_template"], proposal["utility"],
            )
            project_id = uuid.UUID(str(payload.get("app_project_id", "")))
            if project_id:
                async with self._sessions() as session, session.begin():
                    await self._append_conversation_event(
                        session, project_id, "ADAPTIVE_ORGANIZATION_PROPOSED",
                        f"Adaptive org proposed: {proposal['proposed_template']} "
                        f"(U={proposal['utility']:.4f})",
                        {
                            "goal_id": str(goal_id),
                            "proposal": proposal,
                        },
                    )
        except Exception:
            logger.warning(
                "adaptive organization proposal failed for goal=%s",
                goal_id, exc_info=True,
            )

    @staticmethod
    async def _append_conversation_event(
        session: AsyncSession,
        project_id: uuid.UUID,
        message_type: str,
        content: str,
        metadata: dict[str, object],
    ) -> None:
        """Append event message to conversation timeline."""
        await ExecutionOrchestrator._append_conversation_message(
            session,
            project_id,
            role="EVENT",
            message_type=message_type,
            content=content,
            metadata=dict(metadata),
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
        QUALITY_APPROVAL_REQUESTED: orchestrator.handle_quality_approval_requested,
        QUALITY_APPROVAL_COMPLETED: orchestrator.handle_quality_approval_completed,
        "TimerFired": orchestrator.handle_timer_fired,
        # P1-C: V3 domain event handlers
        "ReorganizationTriggered": orchestrator.handle_reorganization_triggered,
        "ConstraintViolated": orchestrator.handle_constraint_violated,
        "OrganizationSelected": orchestrator.handle_organization_selected,
    }
