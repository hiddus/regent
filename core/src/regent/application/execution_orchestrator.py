"""P1 execution main chain orchestrator.

Connects GoalExecutionRequested through Discovery, Requirement, Capability Resolution,
Generation, Build, and Preview Deployment via the Outbox event chain.
"""

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from regent.application.budget_ledger import COST_MODEL_INPUT, COST_MODEL_OUTPUT, BudgetLedger
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
from regent.application.delivery_rejection import DeliveryRejection, reasons_from_exception
from regent.application.delivery_state import DeliveryState, decide_delivery_verdict
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
    DELIVERY_GAP_HUMAN_APPROVED,
    DELIVERY_STATE_CHANGED,
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
    RELEASE_APPROVAL_COMPLETED,
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
from regent.application.human_task_service import HumanTaskService
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
from regent.infrastructure.browser_journey import BrowserJourneyRunner
from regent.infrastructure.models import (
    AppBuildModel,
    AppProjectModel,
    CapabilityModel,
    CapabilityResolutionItemModel,
    CapabilityResolutionPlanModel,
    ConversationMessageModel,
    ConversationModel,
    DeploymentModel,
    DiscoveryRoundModel,
    EvidenceModel,
    GenerationPlanModel,
    GenerationRunModel,
    GoalModel,
    GoalSpecModel,
    HumanTaskModel,
    HypothesisDecisionModel,
    ProductHypothesisModel,
    RequirementRevisionModel,
    RunModel,
    ToolSpecModel,
    VerificationReportModel,
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
        budget_ledger: BudgetLedger | None = None,
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
        self._budget_ledger = budget_ledger

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
            goal_meta = dict(goal.metadata_json or {})
            if goal_meta.get("needs_user_fork"):
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    "goal awaits user fork selection; refusing execution",
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
            result = await ResearchMoreRecoveryService(self._sessions).recover(
                goal_id=goal_id,
                project_id=project_id,
                round_id=round_id,
                actor=actor,
            )
            if not result.recovered and result.method == "STOP":
                await self._halt_goal_stage(
                    goal_id,
                    project_id,
                    stage="RESEARCH_MORE_NEEDS_HUMAN",
                    message=result.message,
                    terminal=GoalCommand.WAIT_FOR_HUMAN,
                    actor=actor,
                    event_type="HUMAN_TASK_REQUIRED",
                    extra={
                        "decision": decision,
                        "discovery_round_id": str(round_id),
                        "gac": "GAC-A4",
                    },
                )
            return

        # Non-SELECT: try another discovery round via RESEARCH_MORE recovery before human.
        if decision != "SELECT" or selected_hypothesis_id is None:
            logger.info(
                "discovery did not select; attempting research-more recovery",
                extra={"decision": decision, "round_id": str(round_id)},
            )
            await self._halt_goal_stage(
                goal_id,
                project_id,
                stage="DISCOVERY_NO_SELECT",
                message=(
                    f"Discovery decision={decision}；未选定假设，正在穷举取证/重发现路径 "
                    "（不会因不达标而结束）。"
                ),
                terminal=None,
                actor=actor,
                event_type="ATTAINMENT_RECOVERY_STARTED",
                extra={"decision": decision, "discovery_round_id": str(round_id)},
            )
            result = await ResearchMoreRecoveryService(self._sessions).recover(
                goal_id=goal_id,
                project_id=project_id,
                round_id=round_id,
                actor=actor,
            )
            if result.recovered:
                return
            await self._halt_goal_stage(
                goal_id,
                project_id,
                stage="DISCOVERY_NO_SELECT_NEEDS_HUMAN",
                message=(
                    f"Discovery decision={decision}；自动取证/重发现已用尽。"
                    f"{result.message} 需要你补充方向或授权来源后继续，不会标记为已完成。"
                ),
                terminal=GoalCommand.WAIT_FOR_HUMAN,
                actor=actor,
                event_type="HUMAN_TASK_REQUIRED",
                extra={
                    "decision": decision,
                    "discovery_round_id": str(round_id),
                    "gac": "GAC-A4",
                },
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
            # I-A/I-B: stamp Session lease onto GenerationRunRequested so epoch
            # fencing and AgentRunner workspace bind apply on the primary path.
            meta = dict(goal.metadata_json or {})
            gen_payload: dict[str, Any] = {
                "goal_id": str(goal_id),
                "app_project_id": str(project_id),
                "requirement_revision_id": str(requirement_id),
                "capability_resolution_plan_id": str(resolution_plan_id),
                "actor": actor,
                "idempotency_key": gen_idempotency,
            }
            sid = str(meta.get("project_agent_session_id") or "").strip()
            if sid:
                gen_payload["project_agent_session_id"] = sid
                if meta.get("project_agent_session_epoch") is not None:
                    gen_payload["project_agent_session_epoch"] = int(
                        meta.get("project_agent_session_epoch") or 0
                    )
                ws = str(meta.get("project_agent_session_workspace_uri") or "").strip()
                if ws:
                    gen_payload["project_agent_session_workspace_uri"] = ws
            outbox_event = make_outbox_event(
                EventEnvelope(
                    event_type=GENERATION_RUN_REQUESTED,
                    aggregate_type="goal",
                    aggregate_id=goal_id,
                    aggregate_version=goal.version,
                    payload=gen_payload,
                    idempotency_key=gen_idempotency,
                    correlation_id=goal.correlation_id,
                )
            )
            session.add(outbox_event)
            await self._append_conversation_event(
                session,
                project_id,
                "GENERATION_RUN_REQUESTED",
                "Agent Session 正在继续编写与修复应用（同一工作区）。",
                {"goal_id": str(goal_id), "project_agent_session_id": sid or None},
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
        generation_run_id: uuid.UUID | None = None
        generation_plan_id: uuid.UUID | None = None

        if self._generator is None or self._workspace_writer is None:
            logger.warning("generation skipped: generator or workspace writer not configured")
            return

        # SESSION_RESUME epoch fence: drop stale outbox that lost the race.
        payload_session_id = str(payload.get("project_agent_session_id") or "").strip()
        payload_epoch_raw = payload.get("project_agent_session_epoch")
        if payload_session_id and payload_epoch_raw is not None:
            from regent.application.project_agent_session import ProjectAgentSessionService

            await ProjectAgentSessionService(self._sessions).assert_resume_epoch(
                project_id,
                session_id=payload_session_id,
                epoch=int(payload_epoch_raw),
            )

        # Soft concurrency gate: defer via retryable LEASE_CONFLICT rather than drop.
        from regent.application.delivery_success_policy import (
            effective_max_concurrent_generating,
        )
        from regent.config import get_settings

        max_generating = effective_max_concurrent_generating(get_settings())
        if max_generating > 0:
            async with self._sessions() as session:
                active = await session.scalar(
                    select(func.count())
                    .select_from(GenerationRunModel)
                    .where(GenerationRunModel.status == "GENERATING")
                )
            if int(active or 0) >= max_generating:
                raise DomainError(
                    ErrorCode.LEASE_CONFLICT,
                    f"generation concurrency cap reached ({max_generating})",
                )

        gen_service = GenerationService(
            self._sessions, self._generator, self._workspace_writer
        )

        # Load requirement for contract hashes
        async with self._sessions() as session:
            revision = await session.get(RequirementRevisionModel, requirement_id)
            goal = await session.get(GoalModel, goal_id)
            if goal is not None:
                _meta = dict(goal.metadata_json or {})
                if _meta.get("needs_user_fork"):
                    raise DomainError(
                        ErrorCode.INVALID_STATE,
                        "goal awaits user fork selection; refusing generation",
                    )
                # Soft-pause is sticky until chat resumes (clears ops_soft_pause).
                if (
                    str(_meta.get("execution_stage") or "") == "DELIVERY_SOFT_PAUSE"
                    or _meta.get("ops_soft_pause")
                ):
                    raise DomainError(
                        ErrorCode.INVALID_STATE,
                        "goal is soft-paused; refusing generation until new direction",
                    )
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
        from regent.application.planned_path_policy import (
            DEFAULT_PLANNED_PATHS,
            expand_planned_paths,
        )

        _scale_hint = str(
            (req_content.get("acceptance_contract") or {}).get("goal_scale")
            or req_content.get("goal_scale")
            or ""
        ).upper()
        planned_paths = expand_planned_paths(
            req_content.get("planned_paths", list(DEFAULT_PLANNED_PATHS)),
            goal_scale=_scale_hint,
        )
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
        # CD-3.4 follow-up: once goal_scale is known from metadata, ensure tests path.
        if str(acceptance_contract.get("goal_scale") or "").upper() != "SMALL" and not any(
            str(p).replace("\\", "/").lower().startswith("tests/")
            or str(p).lower().startswith("test_")
            for p in planned_paths
        ):
            planned_paths.append("tests/test_smoke.py")
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

        # Failure-driven learning: always inject prior lessons when present so
        # plan digest changes and the generator sees concrete constraints.
        from regent.application.goal_runtime_plan import lessons_for_acceptance

        failure_lessons = lessons_for_acceptance(goal_meta, limit=8)
        if failure_lessons:
            acceptance_contract["failure_lessons"] = failure_lessons
        # Always surface latest gap reasons (not only attainment policy path).
        prior_gaps = list(
            goal_meta.get("delivery_gap_reasons") or payload.get("gap_reasons") or []
        )[:12]
        if prior_gaps and "delivery_gap_reasons" not in acceptance_contract:
            acceptance_contract["delivery_gap_reasons"] = prior_gaps
        draft_uri = str(goal_meta.get("last_good_draft_uri") or "").strip()
        if draft_uri:
            acceptance_contract["last_good_draft_uri"] = draft_uri
        # GQ-2: inject durable FailureEnvelope summaries (real build/test/smoke/generation errors).
        try:
            from regent.application.failure_envelope import FailureEnvelopeService

            envelopes = await FailureEnvelopeService(self._sessions).structured_feedback_for_goal(
                goal_id, limit=5
            )
            if envelopes:
                acceptance_contract["failure_envelopes"] = envelopes
        except Exception:
            logger.warning(
                "failure envelope feedback load skipped",
                extra={"goal_id": str(goal_id)},
                exc_info=True,
            )
        learned_constraints = list(goal_meta.get("learned_constraints") or [])
        if learned_constraints:
            acceptance_contract["learned_constraints"] = learned_constraints[:16]
        replan_nonce = str(
            payload.get("replan_nonce") or goal_meta.get("replan_nonce") or ""
        ).strip()
        if replan_nonce:
            acceptance_contract["replan_nonce"] = replan_nonce
        lesson_digest = str(
            payload.get("failure_lesson_digest")
            or (goal_meta.get("capability_resolution") or {}).get("failure_lesson_digest")
            or ""
        ).strip()
        if lesson_digest:
            acceptance_contract["failure_lesson_digest"] = lesson_digest
        if learned_constraints and "Constraint:" not in architecture_summary:
            architecture_summary = (
                f"{architecture_summary}\n\nLearned constraints from prior failures:\n"
                + "\n".join(f"- {item}" for item in learned_constraints[:8])
            )

        acceptance_contract["app_project_id"] = str(project_id)
        acceptance_contract["goal_id"] = str(goal_id)
        acceptance_contract.setdefault("org_key", "default")

        # I-A/I-C: stamp ProjectAgentSession onto the frozen plan so AgentRunner
        # binds the durable workspace (not a disposable agentic/{run_id} tree).
        try:
            from regent.application.project_agent_session import ProjectAgentSessionService

            sessions_svc = ProjectAgentSessionService(self._sessions)
            # Prefer payload workspace from SESSION_RESUME when present.
            payload_ws = str(payload.get("project_agent_session_workspace_uri") or "").strip()
            session_view = await sessions_svc.ensure_active_session(
                app_project_id=project_id,
                goal_id=goal_id,
                actor=actor,
                workspace_uri=payload_ws or None,
            )
            acceptance_contract["project_agent_session_id"] = str(session_view.id)
            acceptance_contract["project_agent_session_epoch"] = session_view.epoch
            acceptance_contract["project_agent_session_workspace_uri"] = (
                payload_ws or session_view.workspace_uri
            )
            # Propagate onto plan root for AgentRunner resume seeding.
            acceptance_contract.setdefault(
                "session_prior_messages",
                list((session_view.checkpoint or {}).get("prior_messages") or [])[:12],
            )
            ckpt = dict(session_view.checkpoint or {})
            if ckpt.get("last_gap_reasons") and not acceptance_contract.get(
                "delivery_gap_reasons"
            ):
                acceptance_contract["delivery_gap_reasons"] = list(
                    ckpt.get("last_gap_reasons") or []
                )[:12]
            if ckpt or payload.get("escalation_step") == "SESSION_RESUME":
                acceptance_contract["session_resume_brief"] = (
                    f"Continue ProjectAgentSession {session_view.id} "
                    f"epoch={session_view.epoch} in the same workspace. "
                    f"Prior method={ckpt.get('resume_method') or payload.get('escalation_step') or 'start'}; "
                    f"fix verification gaps with tools — do not scaffold from scratch."
                )
        except DomainError:
            raise
        except Exception as exc:
            logger.exception(
                "project agent session ensure failed; refusing generation without Session (I-A)",
                extra={"goal_id": str(goal_id), "app_project_id": str(project_id)},
            )
            raise DomainError(
                ErrorCode.INVALID_STATE,
                f"ACTIVE project requires ProjectAgentSession before generation: {exc}",
            ) from exc

        from regent.config import get_settings
        from regent.application.generator_factory import plan_metadata_for_settings
        from regent.application.generator_metadata import assert_generator_consistency
        from regent.application.generation_strategy_policy import (
            resolve_effective_generation_strategy,
        )

        settings = get_settings()
        live_active: bool | None = None
        async with self._sessions() as session:
            goal_row = await session.get(GoalModel, goal_id)
            if goal_row is not None:
                gmeta = dict(goal_row.metadata_json or {})
                if gmeta.get("work_plan_approved"):
                    acceptance_contract["work_plan_approved"] = True
                    acceptance_contract["work_plan_seen"] = True
                if gmeta.get("authorized_session_resume"):
                    acceptance_contract["authorized_session_resume"] = True
                if gmeta.get("work_plan_seen"):
                    acceptance_contract["work_plan_seen"] = True
                if gmeta.get("session_steer_brief"):
                    acceptance_contract["session_steer_brief"] = str(
                        gmeta.get("session_steer_brief")
                    )[:1200]
                if isinstance(gmeta.get("active_corrections"), list):
                    acceptance_contract["active_corrections"] = list(
                        gmeta.get("active_corrections") or []
                    )[-12:]
                if gmeta.get("latest_goal_spec_version") is not None:
                    acceptance_contract["latest_goal_spec_version"] = gmeta.get(
                        "latest_goal_spec_version"
                    )
                if gmeta.get("work_plan_replan_requested"):
                    acceptance_contract["work_plan_approved"] = False
                    acceptance_contract.pop("skip_plan_approve", None)
                from regent.application.agent_control import (
                    get_execution_mode,
                    session_always_tools,
                )

                acceptance_contract["execution_mode"] = get_execution_mode(gmeta)
                acceptance_contract["permission_always_tools"] = sorted(
                    session_always_tools(gmeta)
                )
                if gmeta.get("permission_allow_once_tools"):
                    acceptance_contract["permission_allow_once_tools"] = list(
                        gmeta.get("permission_allow_once_tools") or []
                    )[:32]
                acceptance_contract["goal_metadata"] = {
                    k: gmeta[k]
                    for k in (
                        "agent_abort_requested",
                        "execution_mode",
                        "agent_permission_always",
                    )
                    if k in gmeta
                }
                if gmeta.get("awaiting_human_intervention") or gmeta.get(
                    "stale_progress_handoff_at"
                ):
                    live_active = False
                else:
                    live = gmeta.get("live_action")
                    if isinstance(live, dict) and live.get("updated_at"):
                        try:
                            raw = str(live["updated_at"]).replace("Z", "+00:00")
                            updated = datetime.fromisoformat(raw)
                            if updated.tzinfo is None:
                                updated = updated.replace(tzinfo=UTC)
                            # Treat >15min silence as not live for canary sampling.
                            live_active = datetime.now(UTC) - updated < timedelta(minutes=15)
                        except ValueError:
                            live_active = None
        strategy = resolve_effective_generation_strategy(
            settings, goal_id=str(goal_id), live_active=live_active
        )
        meta = plan_metadata_for_settings(settings, goal_id=str(goal_id))
        generator_ref = meta["generator_ref"]
        prompt_version = meta["prompt_version"]

        # Fail closed before freezing plan if injected generator disagrees.
        if self._generator is not None:
            gen = self._generator
            if hasattr(gen, "select"):
                gen = gen.select(str(goal_id))
            try:
                assert_generator_consistency(
                    strategy=strategy,
                    generator=gen,
                    plan_id=None,
                    run_id=None,
                    contract_generator_ref=generator_ref,
                    contract_prompt_version=prompt_version,
                )
            except DomainError as exc:
                if exc.code == ErrorCode.GENERATOR_METADATA_MISMATCH:
                    await self._record_generator_mismatch_evidence(
                        goal_id=goal_id,
                        message=exc.message,
                        strategy=strategy,
                        generator_ref=generator_ref,
                    )
                raise

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
            # Bind run idempotency to plan_id so metadata drift (lessons/envelopes)
            # that yields a new plan on outbox retry cannot collide with the prior
            # run under the outbox event key (scope-mismatch → replan storm).
            run_idempotency_key = make_idempotency_key(
                "generation-run",
                goal_id,
                f"{idempotency_key}:{plan.id}",
            )
            run = await gen_service.request_run(
                RequestGenerationRun(
                    plan_id=plan.id,
                    idempotency_key=run_idempotency_key,
                    correlation_id=correlation_id,
                )
            )
            generation_run_id = run.id
            generation_plan_id = run.plan_id
            # Certified hive: durable PM→Dev→QA AgentTasks for this generation run.
            try:
                from regent.application.hive_runtime import maybe_offer_generation_hive_chain

                hive_chain = await maybe_offer_generation_hive_chain(
                    self._sessions,
                    goal_id=goal_id,
                    generation_run_id=run.id,
                    correlation_id=str(correlation_id),
                )
                if hive_chain is not None:
                    logger.info(
                        "certified hive AgentTasks offered for generation",
                        extra={
                            "goal_id": str(goal_id),
                            "generation_run_id": str(run.id),
                            "dev_task_id": str(hive_chain.dev_task.id),
                            "qa_task_id": str(hive_chain.qa_task.id),
                        },
                    )
            except Exception:
                logger.warning(
                    "hive AgentTask offer skipped (non-fatal)",
                    extra={"goal_id": str(goal_id)},
                    exc_info=True,
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
                from regent.application.live_action import set_goal_live_action

                async def _on_generation_progress(progress: object) -> None:
                    from regent.agent.progress_event import ProgressEvent, coerce_progress

                    event = (
                        progress
                        if isinstance(progress, ProgressEvent)
                        else coerce_progress(str(progress))
                    )
                    tool = event.tool
                    tool_event = {
                        "type": event.type,
                        "tool": tool,
                        "summary": event.summary,
                        "turn": event.turn,
                        "args_preview": event.args_preview,
                        "result_preview": event.result_preview,
                        "input_tokens": event.input_tokens,
                        "output_tokens": event.output_tokens,
                        "cached_tokens": event.cached_tokens,
                    }
                    await set_goal_live_action(
                        self._sessions,
                        goal_id,
                        event.summary,
                        stage="GENERATING",
                        event_type=event.event_type or "GENERATION_RUN_REQUESTED",
                        turn=event.turn,
                        tool=tool,
                        tool_event=tool_event,
                        activity_event=tool_event,
                    )
                    # H0.6: dual-write RegentEvent ring (prebury for H1 SSE).
                    try:
                        from sqlalchemy.orm.attributes import flag_modified

                        from regent.agent.events import RegentEvent, append_regent_event

                        etype = str(event.type or "status")
                        if etype not in {
                            "turn_start",
                            "turn_end",
                            "tool_call",
                            "plan_updated",
                            "submit",
                            "budget",
                            "status",
                        }:
                            etype = "status"
                        async with self._sessions() as ev_sess, ev_sess.begin():
                            g_ev = await ev_sess.get(GoalModel, goal_id, with_for_update=True)
                            if g_ev is not None:
                                g_ev.metadata_json = append_regent_event(
                                    dict(g_ev.metadata_json or {}),
                                    RegentEvent(
                                        type=etype,  # type: ignore[arg-type]
                                        summary=str(event.summary or "")[:240],
                                        run_id=str(run.id),
                                        goal_id=str(goal_id),
                                        turn=event.turn,
                                        tool=tool,
                                        payload={
                                            k: v
                                            for k, v in tool_event.items()
                                            if k not in {"type", "summary", "tool", "turn"}
                                            and v is not None
                                        },
                                    ),
                                )
                                flag_modified(g_ev, "metadata_json")
                    except Exception:
                        logger.debug(
                            "regent_event append skipped",
                            extra={"goal_id": str(goal_id)},
                            exc_info=True,
                        )

                live_summary = "Agent Session 正在工作（读代码 / 改文件 / 验证）…"
                async with self._sessions() as _sess:
                    _goal = await _sess.get(GoalModel, goal_id)
                    _meta = dict((_goal.metadata_json or {}) if _goal else {})
                    if not _meta.get("project_agent_session_id"):
                        live_summary = "正在生成应用代码…"
                    elif str(_meta.get("capability_resolution", {}).get("delivery_method") or "") == "SESSION_RESUME":
                        live_summary = "同一 Agent Session 续跑修复中…"
                await set_goal_live_action(
                    self._sessions,
                    goal_id,
                    live_summary,
                    stage="GENERATING",
                    event_type="GENERATION_RUN_REQUESTED",
                )
                # Persist attempt pointer for delivery-review + learning continuity.
                await self._remember_generation_attempt(
                    goal_id,
                    generation_run_id=run.id,
                    plan_id=run.plan_id,
                )
                try:
                    from regent.application.project_agent_session import (
                        ProjectAgentSessionService,
                    )

                    await ProjectAgentSessionService(self._sessions).bind_generation_run(
                        project_id, generation_run_id=run.id
                    )
                except Exception:
                    logger.warning(
                        "bind generation run to project agent session failed",
                        extra={"goal_id": str(goal_id), "run_id": str(run.id)},
                        exc_info=True,
                    )
                base_workspace = await self._resolve_revise_base_workspace(goal_id)
                snapshot = await gen_service.execute(
                    run.id,
                    base_workspace=base_workspace,
                    on_progress=_on_generation_progress,
                )
                await self._remember_generation_attempt(
                    goal_id,
                    generation_run_id=run.id,
                    plan_id=run.plan_id,
                    completed=True,
                )
                await self._stamp_agent_loop_complete(
                    goal_id=goal_id,
                    project_id=project_id,
                    generation_run_id=run.id,
                    summary="本轮生成与验证已通过，结果已写入工作区。",
                    open_items=await self._open_work_plan_items(goal_id),
                )
            # Phase 2.3: Record generation token costs in BudgetLedger
            await self._record_generation_costs(goal_id, run.id)
        except DomainError as exc:
            if exc.code == ErrorCode.LEASE_CONFLICT:
                # In-flight generate under a prior lease — retry outbox later.
                raise
            await self._record_generation_failure_memory(
                goal_id=goal_id,
                project_id=project_id,
                generation_run_id=generation_run_id,
                plan_id=generation_plan_id,
                exc=exc,
            )
            if isinstance(exc, DeliveryRejection):
                # CD-7.5 / N-6: transcript DB jitter must not burn the capability ladder;
                # sidecar is already on disk for audit.
                if exc.code == ErrorCode.TRANSCRIPT_PERSIST_FAILED:
                    logger.error(
                        "transcript persist failed; sidecar retained; skipping ladder burn",
                        extra={
                            "goal_id": str(goal_id),
                            "error_code": exc.code.value,
                            "retryable": bool(getattr(exc, "retryable", False)),
                            "draft_uri": getattr(exc, "draft_uri", None),
                            "reasons": list(exc.reasons)[:3],
                        },
                    )
                    async with self._sessions() as session, session.begin():
                        await self._append_conversation_message(
                            session,
                            project_id,
                            role="ASSISTANT",
                            message_type="TRANSCRIPT_PERSIST_FAILED",
                            content=(
                                "生成 transcript 写入数据库失败（sidecar 已保留）。"
                                "这不消耗能力阶梯；请稍后重试或检查存储。"
                            ),
                            metadata={
                                "goal_id": str(goal_id),
                                "error_code": exc.code.value,
                                "retryable": True,
                                "draft_uri": getattr(exc, "draft_uri", None),
                            },
                        )
                    return
                reasons = reasons_from_exception(exc)
                draft_uri = getattr(exc, "draft_uri", None)
                gap_kind = getattr(exc, "gap_kind", None)
                recovery = await DeliveryGapRecoveryService(self._sessions).recover(
                    goal_id=goal_id,
                    project_id=project_id,
                    requirement_revision_id=requirement_id,
                    capability_resolution_plan_id=resolution_plan_id,
                    actor=actor,
                    gap_reasons=reasons,
                    halt_context={
                        "stage": "DELIVERY_REVIEW_REJECTED",
                        "last_error": str(exc)[:400],
                        "message": str(exc.message)[:400],
                        "draft_uri": draft_uri,
                        "gap_kind": gap_kind,
                        "primary_failure_code": gap_kind,
                    },
                )
                if await self._apply_delivery_verdict(
                    recovery,
                    goal_id=goal_id,
                    project_id=project_id,
                    actor=actor,
                    recovered_log="delivery gap recovery scheduled",
                    stage_exhausted="DELIVERY_GAP_EXHAUSTED",
                    extra_exhausted={
                        "gap_kind": recovery.gap_kind,
                        "attempts": recovery.attempts,
                        "gac": "GAC-D5",
                    },
                    append_conversation=False,
                ):
                    return
                logger.warning(
                    "delivery gap exhausted; refusing unreliable publish",
                    extra={"goal_id": str(goal_id), "message": recovery.message},
                )
                return
            if exc.code == ErrorCode.INVALID_STATE or exc.code == ErrorCode.POLICY_DENIED:
                # Dispatch/concurrency collisions are not product-surface gaps.
                # Feeding DeliveryGapRecovery caused invalid_state → replan storms.
                msg_l = (exc.message or "").lower()
                if (
                    "idempotency key scope mismatch" in msg_l
                    or "frozen generation plan is required" in msg_l
                    or "generation plan already executing" in msg_l
                ):
                    logger.warning(
                        "generation dispatch conflict; defer to outbox retry",
                        extra={"goal_id": str(goal_id), "error": exc.message[:200]},
                    )
                    raise DomainError(
                        ErrorCode.LEASE_CONFLICT,
                        exc.message,
                    ) from exc
                # Business INVALID_STATE / POLICY_DENIED: learn + replan into a new event.
                # Do not blind-retry the same GenerationRunRequested payload.
                recovery = await DeliveryGapRecoveryService(self._sessions).recover(
                    goal_id=goal_id,
                    project_id=project_id,
                    requirement_revision_id=requirement_id,
                    capability_resolution_plan_id=resolution_plan_id,
                    actor=actor,
                    gap_reasons=[f"{exc.code.value.lower()}: {exc.message[:200]}"],
                    halt_context={
                        "stage": (
                            "GENERATION_POLICY_DENIED"
                            if exc.code == ErrorCode.POLICY_DENIED
                            else "GENERATION_INVALID_STATE"
                        ),
                        "last_error": str(exc)[:400],
                        "message": exc.message[:400],
                        "error_code": exc.code.value,
                    },
                )
                if await self._apply_delivery_verdict(
                    recovery,
                    goal_id=goal_id,
                    project_id=project_id,
                    actor=actor,
                    recovered_log=(
                        "policy-denied recovery replanned"
                        if exc.code == ErrorCode.POLICY_DENIED
                        else "invalid-state recovery replanned"
                    ),
                    stage_exhausted=(
                        "GENERATION_POLICY_DENIED_NEEDS_HUMAN"
                        if exc.code == ErrorCode.POLICY_DENIED
                        else "GENERATION_INVALID_STATE_NEEDS_HUMAN"
                    ),
                    extra_exhausted={
                        "gap_kind": recovery.gap_kind,
                        "attempts": recovery.attempts,
                        "last_error": str(exc)[:400],
                        "gac": "GAC-D5",
                    },
                ):
                    return
                logger.warning(
                    "generation contract error could not replan; refusing blind outbox retry",
                    extra={"goal_id": str(goal_id), "error": exc.message[:200]},
                )
                raise
            logger.exception("generation failed", extra={"goal_id": str(goal_id)})
            raise
        except Exception as exc:
            await self._record_generation_failure_memory(
                goal_id=goal_id,
                project_id=project_id,
                generation_run_id=generation_run_id,
                plan_id=generation_plan_id,
                exc=exc,
            )
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
    # BudgetLedger integration (Phase 2.3)
    # ---------------------------------------------------------------------------

    async def _record_generation_costs(
        self, goal_id: uuid.UUID, run_id: uuid.UUID
    ) -> None:
        """Record generation run token costs in BudgetLedger and check budget limits."""
        if self._budget_ledger is None:
            return
        try:
            async with self._sessions() as session:
                run = await session.get(GenerationRunModel, run_id)
                if run is None or run.status != "COMPLETED":
                    return
                input_tokens = getattr(run, "input_tokens", 0) or 0
                output_tokens = getattr(run, "output_tokens", 0) or 0
            if input_tokens > 0:
                await self._budget_ledger.record_cost(
                    goal_id,
                    run_id,
                    cost_type=COST_MODEL_INPUT,
                    amount=float(input_tokens),
                    description="generation input tokens",
                )
            if output_tokens > 0:
                await self._budget_ledger.record_cost(
                    goal_id,
                    run_id,
                    cost_type=COST_MODEL_OUTPUT,
                    amount=float(output_tokens),
                    description="generation output tokens",
                )
            # Check budget limit after recording costs
            status = await self._budget_ledger.check_budget_limit(goal_id)
            if status.is_blocked:
                logger.warning(
                    "goal blocked: budget limit exceeded",
                    extra={"goal_id": str(goal_id), "total_cost": status.total_cost},
                )
        except Exception:
            logger.warning(
                "budget ledger cost recording failed (non-fatal)",
                extra={"goal_id": str(goal_id), "run_id": str(run_id)},
                exc_info=True,
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
            from regent.config import get_settings as _gs

            gates_mode = str(
                getattr(_gs(), "delivery_product_gates_mode", "soft") or "soft"
            ).lower()
            # Ship-first soft/off: do not trap on Docker sandbox UNKNOWN (e.g. exit 125).
            # Continue to preview; delivery review still soft-gates product surface.
            if gates_mode in {"soft", "off"} and str(result_build.status) in {
                "UNKNOWN",
                "FAILED",
            }:
                logger.warning(
                    "soft-pass build failure → continue to AppBuildPassed (ship-first)",
                    extra={
                        "build_id": str(result_build.id),
                        "status": result_build.status,
                        "gates_mode": gates_mode,
                    },
                )
            else:
                failure_reason = await self._summarize_build_failure(result_build.id)
                try:
                    from regent.application.failure_envelope import (
                        FailureEnvelopeService,
                        RecordFailureCommand,
                    )

                    await FailureEnvelopeService(self._sessions).record_failure(
                        RecordFailureCommand(
                            goal_id=goal_id,
                            stage="build",
                            error_code=str(result_build.failure_code or "BUILD_FAILED"),
                            error_summary=failure_reason,
                            generation_run_id=None,
                            workspace_snapshot_id=snapshot_id,
                            evidence_payload={
                                "build_id": str(result_build.id),
                                "status": result_build.status,
                            },
                        )
                    )
                except Exception:
                    logger.warning(
                        "failure envelope record skipped for build",
                        extra={"goal_id": str(goal_id)},
                        exc_info=True,
                    )
                await self._halt_goal_stage(
                    goal_id,
                    project_id,
                    stage="BUILD_FAILED",
                    message=f"应用构建未通过验证：{failure_reason}",
                    terminal=None,
                    actor=actor,
                    event_type="ATTAINMENT_RECOVERY_STARTED",
                    extra={
                        "build_id": str(result_build.id),
                        "status": result_build.status,
                        "log_uri": result_build.log_uri or "",
                        "failure_code": result_build.failure_code or "",
                    },
                )
                # Prefer regenerating over leaving the console stuck on "正在构建".
                req_uuid, plan_uuid = await self._resolve_generation_ids(goal_id)
                if req_uuid is not None and plan_uuid is not None:
                    recovery = await DeliveryGapRecoveryService(self._sessions).recover(
                        goal_id=goal_id,
                        project_id=project_id,
                        requirement_revision_id=req_uuid,
                        capability_resolution_plan_id=plan_uuid,
                        actor=actor,
                        gap_reasons=[f"build-verification: {failure_reason}"],
                        halt_context={
                            "stage": "BUILD_FAILED",
                            "message": f"应用构建未通过验证：{failure_reason}",
                            "last_error": failure_reason[:400],
                            "build_id": str(result_build.id),
                            "status": result_build.status,
                        },
                    )
                    if await self._apply_delivery_verdict(
                        recovery,
                        goal_id=goal_id,
                        project_id=project_id,
                        actor=actor,
                        recovered_log="build failure recovery scheduled",
                        stage_exhausted="BUILD_DELIVERY_GAP_EXHAUSTED",
                        extra_exhausted={
                            "gap_kind": recovery.gap_kind,
                            "attempts": recovery.attempts,
                            "build_id": str(result_build.id),
                        },
                    ):
                        return
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
        """Create release candidate + human approval task; await RELEASE_APPROVAL.

        SMALL goals auto-approve low-risk RELEASE_APPROVAL (audit retained) unless
        ``require_release_human_approval`` is forced on.
        """
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
        goal_scale = ""
        force_human = False
        async with self._sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            correlation_id = str(goal.correlation_id) if goal else ""
            if goal is not None:
                meta = dict(goal.metadata_json or {})
                goal_scale = str(meta.get("goal_scale") or "").upper()
                force_human = bool(meta.get("force_release_human"))

        # SMALL preview is low-risk: auto-approve unless operator forces human gate.
        # Ship-first soft/off: also auto-approve so Preview is not blocked on chat.
        from regent.config import get_settings as _gs

        gates_mode = str(
            getattr(_gs(), "delivery_product_gates_mode", "soft") or "soft"
        ).lower()
        auto_approve_small = (
            (goal_scale == "SMALL" or gates_mode in {"soft", "off"}) and not force_human
        )

        work_id, run_id = await self._ensure_work_and_run_for_goal(
            goal_id, purpose="preview-deployment", actor=actor
        )
        human_tasks = HumanTaskService(self._sessions)
        task_id = await human_tasks.create(
            goal_id=goal_id,
            work_id=work_id,
            run_id=run_id,
            task_type="RELEASE_APPROVAL",
            prompt=(
                f"Approve preview release candidate for build {build_id}. "
                "Respond with decision=APPROVE or decision=REJECT."
                if not auto_approve_small
                else f"Auto-approved SMALL preview release for build {build_id}."
            ),
            requested_by=actor if not auto_approve_small else "regent-core:auto-release",
            due_at=datetime.now(UTC) + timedelta(hours=24),
        )
        try:
            candidate = await release_service.create_candidate(
                CreateReleaseCandidate(
                    app_build_id=build_id,
                    actor=actor,
                    correlation_id=correlation_id,
                    human_task_id=task_id,
                )
            )
        except Exception as exc:
            logger.exception(
                "release candidate creation failed", extra={"goal_id": str(goal_id)}
            )
            await self._halt_goal_stage(
                goal_id,
                project_id,
                stage="RELEASE_CANDIDATE_FAILED",
                message=f"Release candidate creation failed: {exc}",
                terminal=GoalCommand.WAIT_FOR_HUMAN,
                actor=actor,
                event_type="HUMAN_TASK_REQUIRED",
                extra={"error": str(exc)[:400]},
            )
            return

        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal is not None:
                metadata = dict(goal.metadata_json or {})
                metadata["pending_release"] = {
                    "release_candidate_id": str(candidate.id),
                    "human_task_id": str(task_id),
                    "app_build_id": str(build_id),
                    "app_project_id": str(project_id),
                    "idempotency_key": idempotency_key,
                    "correlation_id": correlation_id,
                    "auto_approved_small": auto_approve_small,
                }
                goal.metadata_json = metadata
                if auto_approve_small:
                    from regent.infrastructure.models import AuditRecordModel

                    session.add(
                        AuditRecordModel(
                            id=uuid.uuid4(),
                            aggregate_type="goal",
                            aggregate_id=goal_id,
                            aggregate_version=goal.version,
                            action="AUTO_RELEASE_APPROVAL",
                            actor="regent-core:auto-release",
                            payload={
                                "goal_scale": goal_scale,
                                "release_candidate_id": str(candidate.id),
                                "human_task_id": str(task_id),
                                "app_build_id": str(build_id),
                                "reason": "SMALL preview auto-approve",
                            },
                            correlation_id=goal.correlation_id,
                        )
                    )

        if auto_approve_small:
            # Complete human task first so release_service.approve() can proceed.
            # execute() is idempotent if ReleaseApprovalCompleted outbox races us.
            await human_tasks.complete(
                task_id,
                assigned_to="regent-core:auto-release",
                response={
                    "decision": "APPROVE",
                    "approved": True,
                    "feedback": "auto-approved SMALL preview release",
                },
            )
            logger.info(
                "SMALL release auto-approved",
                extra={
                    "goal_id": str(goal_id),
                    "release_candidate_id": str(candidate.id),
                    "task_id": str(task_id),
                },
            )
            try:
                await self._execute_approved_preview_deployment(
                    {
                        "goal_id": str(goal_id),
                        "app_project_id": str(project_id),
                        "release_candidate_id": str(candidate.id),
                        "actor": "regent-core:auto-release",
                        "idempotency_key": idempotency_key or f"auto-release-{candidate.id}",
                        "correlation_id": correlation_id,
                    }
                )
            except Exception:
                logger.exception(
                    "auto-release inline preview deploy failed",
                    extra={
                        "goal_id": str(goal_id),
                        "release_candidate_id": str(candidate.id),
                    },
                )
            return

        release_prompt = (
            f"Approve preview release candidate for build {build_id}. "
            "Respond with decision=APPROVE or decision=REJECT."
        )
        await self._halt_goal_stage(
            goal_id,
            project_id,
            stage="RELEASE_APPROVAL_REQUIRED",
            message=(
                f"预览发布候选 {candidate.id} 等待人工批准（任务 {task_id}）。"
                "完成 RELEASE_APPROVAL 任务后将继续部署。"
            ),
            terminal=GoalCommand.WAIT_FOR_HUMAN,
            actor=actor,
            event_type="HUMAN_TASK_REQUIRED",
            extra={
                "id": str(task_id),
                "human_task_id": str(task_id),
                "task_type": "RELEASE_APPROVAL",
                "prompt": release_prompt,
                "release_candidate_id": str(candidate.id),
            },
        )

    async def handle_release_approval_completed(self, payload: dict[str, Any]) -> None:
        """After human RELEASE_APPROVAL, approve candidate and execute preview deploy."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        actor = str(payload.get("actor", "regent-core"))
        approved = bool(payload.get("approved", False))
        project_id = uuid.UUID(str(payload.get("app_project_id") or uuid.UUID(int=0)))
        candidate_id_raw = payload.get("release_candidate_id")
        idempotency_key = str(payload.get("idempotency_key", ""))
        correlation_id = str(payload.get("correlation_id", ""))

        async with self._sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            meta = dict((goal.metadata_json if goal else None) or {})
            pending = dict(meta.get("pending_release") or {})
            if not candidate_id_raw:
                candidate_id_raw = pending.get("release_candidate_id")
            if not idempotency_key:
                idempotency_key = str(pending.get("idempotency_key") or f"release-{goal_id}")
            if not correlation_id:
                correlation_id = str(pending.get("correlation_id") or "")
            if project_id.int == 0 and pending.get("app_project_id"):
                project_id = uuid.UUID(str(pending["app_project_id"]))

        if not approved:
            await self._halt_goal_stage(
                goal_id,
                project_id,
                stage="RELEASE_REJECTED",
                message="人工拒绝了预览发布候选。",
                terminal=GoalCommand.WAIT_FOR_HUMAN,
                actor=actor,
                event_type="HUMAN_TASK_REQUIRED",
                extra={"release_candidate_id": str(candidate_id_raw or "")},
            )
            return

        if not candidate_id_raw:
            logger.error("release approval missing candidate id", extra={"goal_id": str(goal_id)})
            return

        if self._deployment_provider is None:
            logger.warning("deployment skipped: deployment provider not configured")
            return

        await self._execute_approved_preview_deployment(
            {
                "goal_id": str(goal_id),
                "app_project_id": str(project_id),
                "release_candidate_id": str(candidate_id_raw),
                "actor": actor,
                "idempotency_key": idempotency_key,
                "correlation_id": correlation_id,
            }
        )

    async def handle_delivery_gap_human_approved(self, payload: dict[str, Any]) -> None:
        """After DELIVERY_GAP_INTERVENE approve: reset ladder and re-enter DeliveryGapRecovery."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        actor = str(payload.get("actor", "regent-core"))
        human_message = str(payload.get("message") or "")

        async with self._sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                return
            status = goal.status
            version = goal.version
            correlation_id = goal.correlation_id

        if status == "WAITING_HUMAN":
            try:
                await TransitionService(self._sessions).transition_goal(
                    TransitionContext(goal_id, version, actor, correlation_id),
                    GoalCommand.HUMAN_RESOLVED,
                )
            except DomainError:
                logger.warning(
                    "delivery gap human approve transition skipped",
                    extra={"goal_id": str(goal_id)},
                )

        recovery = await DeliveryGapRecoveryService(self._sessions).resume_after_human(
            goal_id=goal_id,
            project_id=project_id,
            actor=actor,
            human_message=human_message or None,
        )
        if recovery.recovered:
            logger.info(
                "delivery gap resumed after human approve",
                extra={
                    "goal_id": str(goal_id),
                    "attempts": recovery.attempts,
                    "method": recovery.method,
                },
            )
            return
        if recovery.method == "RESTART_DISCOVERY":
            await self._restart_discovery_for_lineage_gap(
                goal_id=goal_id,
                project_id=project_id,
                actor=actor,
            )
            return
        # resume → recover already created a HumanTask + conversation card when
        # terminal_exhaust (goal_intent / ladder handoff). Do not stack a second
        # HUMAN_TASK_REQUIRED without task id (Console「总是允许」死循环观感).
        if recovery.terminal_exhaust:
            logger.info(
                "delivery gap human approve landed on handoff; skip RESUME_BLOCKED card",
                extra={
                    "goal_id": str(goal_id),
                    "gap_kind": recovery.gap_kind,
                    "message": (recovery.message or "")[:200],
                },
            )
            return
        # Never re-open DELIVERY_GAP_INTERVENE for lineage-missing — that is the
        # approve→block→approve loop. Fall back to a non-intervening halt note.
        if "missing generation lineage" in (recovery.message or "").lower():
            await self._halt_goal_stage(
                goal_id,
                project_id,
                stage="PIPELINE_LINEAGE_MISSING",
                message=recovery.message or "人工批准后仍缺少生成谱系。",
                terminal=None,
                actor=actor,
                event_type="GOAL_EXECUTION_STAGE_HALTED",
                extra={"gap_kind": recovery.gap_kind, "gac": "GAC-D1"},
            )
            return
        await self._halt_goal_stage(
            goal_id,
            project_id,
            stage="DELIVERY_GAP_RESUME_BLOCKED",
            message=recovery.message or "人工批准后仍无法重开交付恢复。",
            terminal=GoalCommand.WAIT_FOR_HUMAN,
            actor=actor,
            event_type="HUMAN_TASK_REQUIRED",
            extra={"gap_kind": recovery.gap_kind, "gac": "GAC-D1"},
        )

    async def _restart_discovery_for_lineage_gap(
        self,
        *,
        goal_id: uuid.UUID,
        project_id: uuid.UUID,
        actor: str,
    ) -> None:
        """Re-kick GoalExecution→Discovery when approve hit a goal with no gen lineage."""
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                return
            metadata = dict(goal.metadata_json or {})
            allow_actions = [
                a
                for a in list(metadata.get("decision_allow_actions") or [])
                if str(a) != "delivery_gap_intervene"
            ]
            if allow_actions:
                metadata["decision_allow_actions"] = allow_actions
            else:
                metadata.pop("decision_allow_actions", None)
            metadata["awaiting_human_intervention"] = False
            metadata.pop("pending_delivery_gap_human", None)
            metadata.pop("stale_progress_handoff_at", None)
            metadata["execution_stage"] = "DISCOVERING"
            goal.metadata_json = metadata
            from sqlalchemy.orm.attributes import flag_modified

            flag_modified(goal, "metadata_json")
            resume_key = make_idempotency_key(
                "lineage-restart-discovery",
                goal.id,
                f"{datetime.now(UTC).isoformat()}:{uuid.uuid4().hex[:8]}",
            )
            session.add(
                make_outbox_event(
                    EventEnvelope(
                        event_type=GOAL_EXECUTION_REQUESTED,
                        aggregate_type="goal",
                        aggregate_id=goal.id,
                        aggregate_version=goal.version,
                        payload={
                            "goal_id": str(goal.id),
                            "app_project_id": str(project_id),
                            "actor": actor,
                            "idempotency_key": resume_key,
                            "reason": "missing_generation_lineage_after_human_approve",
                        },
                        idempotency_key=resume_key,
                        correlation_id=goal.correlation_id,
                    )
                )
            )
            await self._append_conversation_event(
                session,
                project_id,
                "DISCOVERY_RESTARTED",
                "交付谱系未就绪，已重新发起发现/规划（已取消「总是允许·交付缺口」以免空转）。",
                {"goal_id": str(goal_id), "idempotency_key": resume_key},
            )
        logger.info(
            "restarted discovery after missing lineage on human approve",
            extra={"goal_id": str(goal_id)},
        )

    async def _execute_approved_preview_deployment(self, payload: dict[str, Any]) -> None:
        """Approve (already human-gated) candidate and run preview deployment."""
        goal_id = uuid.UUID(str(payload["goal_id"]))
        project_id = uuid.UUID(str(payload["app_project_id"]))
        candidate_id = uuid.UUID(str(payload["release_candidate_id"]))
        actor = str(payload.get("actor", "regent-core"))
        idempotency_key = str(payload.get("idempotency_key", ""))
        correlation_id = str(payload.get("correlation_id", ""))

        assert self._deployment_provider is not None
        release_service = ReleaseService(self._sessions, self._deployment_provider)
        try:
            await release_service.approve(
                candidate_id,
                actor=actor,
                reason="approved by human RELEASE_APPROVAL task",
            )

            work_id, run_id = await self._ensure_work_and_run_for_goal(
                goal_id, purpose="preview-deployment", actor=actor
            )
            permit_id = uuid.uuid4()
            if self._permits:
                permit_id = await self._permits.request(
                    PermitBinding(
                        goal_id=goal_id,
                        work_id=work_id,
                        run_id=run_id,
                        actor_id="preview-deployment-provider",
                        action="preview-deploy",
                        target=str(candidate_id),
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
                    release_candidate_id=candidate_id,
                    permit_id=permit_id,
                    environment="preview",
                    idempotency_key=idempotency_key,
                    correlation_id=correlation_id,
                )
            )
            result = await release_service.execute(deployment.id)
        except Exception as exc:
            # TS §13.8.3: route delivery recovery only via typed DeliveryRejection.
            if isinstance(exc, DeliveryRejection):
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
                    reasons = reasons_from_exception(exc)
                    recovery = await DeliveryGapRecoveryService(self._sessions).recover(
                        goal_id=goal_id,
                        project_id=project_id,
                        requirement_revision_id=req_uuid,
                        capability_resolution_plan_id=plan_uuid,
                        actor=actor,
                        gap_reasons=reasons,
                        halt_context={
                            "stage": "DEPLOY_DELIVERY_REJECTED",
                            "last_error": str(exc)[:400],
                            "message": str(exc)[:400],
                        },
                    )
                    if await self._apply_delivery_verdict(
                        recovery,
                        goal_id=goal_id,
                        project_id=project_id,
                        actor=actor,
                        recovered_log="delivery gap recovery scheduled",
                        stage_exhausted="DEPLOY_DELIVERY_REJECTED",
                        extra_exhausted={"gap_kind": recovery.gap_kind, "gac": "GAC-D5"},
                    ):
                        return
                await self._halt_goal_stage(
                    goal_id,
                    project_id,
                    stage="DEPLOY_DELIVERY_REJECTED",
                    message=(
                        "预览部署被交付审查拦截，且无法自动恢复；"
                        f"需要你介入（GAC-A5）：{exc}"
                    ),
                    terminal=GoalCommand.WAIT_FOR_HUMAN,
                    actor=actor,
                    event_type="HUMAN_TASK_REQUIRED",
                    extra={"error": str(exc)[:400]},
                )
                return
            logger.exception("deployment failed", extra={"goal_id": str(goal_id)})
            await self._recover_or_wait_after_deploy_gap(
                goal_id,
                project_id,
                actor=actor,
                stage="DEPLOY_FAILED",
                message=f"Preview deployment failed (GAC-A4): {type(exc).__name__}",
                gap_reasons=[f"deployment-failed: {type(exc).__name__}"],
                extra={"error": str(exc)[:400]},
            )
            return

        if result.status != "SUCCEEDED":
            logger.warning(
                "deployment did not succeed",
                extra={"deployment_id": str(result.id), "status": result.status},
            )
            await self._recover_or_wait_after_deploy_gap(
                goal_id,
                project_id,
                actor=actor,
                stage="DEPLOY_NOT_SUCCEEDED",
                message=f"Deployment status={result.status} (GAC-A4).",
                gap_reasons=[f"deployment-status: {result.status}"],
                extra={"deployment_id": str(result.id), "status": result.status},
            )
            return

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

        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal:
                metadata = dict(goal.metadata_json or {})
                metadata.pop("pending_release", None)
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

    async def _resolve_public_preview_url(
        self, deployment_id: uuid.UUID, endpoint: str
    ) -> str:
        public_url = endpoint
        async with self._sessions() as session:
            dep = await session.get(DeploymentModel, deployment_id)
            if dep is not None:
                ev = dict(dep.evidence or {})
                public_url = str(ev.get("materialized_browse_url") or endpoint)
        return public_url

    async def _enforce_live_preview_product_qa(
        self,
        *,
        goal_id: uuid.UUID,
        project_id: uuid.UUID,
        deployment_id: uuid.UUID,
        endpoint: str,
    ) -> tuple[bool, Any, str]:
        """Run Live Preview QA before any PREVIEW_SUCCEEDED / product_surface_ready.

        Returns (passed, qa_result_or_none, public_url). On failure, stamps
        PREVIEW_PRODUCT_QA_FAILED and returns passed=False.
        """
        from regent.application.live_preview_qa import run_live_preview_qa

        public_url = await self._resolve_public_preview_url(deployment_id, endpoint)
        if not public_url.startswith(("http://", "https://")):
            # Fabricate a failed QA-shaped object via a real call for consistency.
            qa = await run_live_preview_qa(public_url or "")
        else:
            qa = await run_live_preview_qa(public_url)
        if qa.passed:
            return True, qa, public_url

        logger.warning(
            "live preview product QA failed — refusing preview success",
            extra={
                "goal_id": str(goal_id),
                "deployment_id": str(deployment_id),
                "preview_url": public_url,
                "gaps": qa.failed_gap_codes(),
            },
        )
        try:
            from regent.application.failure_envelope import (
                FailureEnvelopeService,
                RecordFailureCommand,
            )

            await FailureEnvelopeService(self._sessions).record_failure(
                RecordFailureCommand(
                    goal_id=goal_id,
                    stage="preview_product_qa",
                    error_code="PREVIEW_PRODUCT_QA_FAILED",
                    error_summary=qa.summary or "; ".join(qa.failed_gap_codes()[:6]),
                    evidence_payload=qa.as_dict(),
                )
            )
        except Exception:
            logger.warning(
                "failure envelope record skipped for preview QA",
                extra={"goal_id": str(goal_id)},
                exc_info=True,
            )
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is not None:
                metadata = dict(goal.metadata_json or {})
                metadata["execution_stage"] = "PREVIEW_PRODUCT_QA_FAILED"
                metadata["last_gate_status"] = "PRODUCT_QA_FAILED"
                metadata["last_deployment_id"] = str(deployment_id)
                metadata["last_preview_endpoint"] = endpoint
                metadata["preview_url"] = public_url
                metadata["preview_ready"] = False
                metadata["product_surface_ready"] = False
                metadata["preview_mode"] = "runtime"
                metadata["delivery_soft_pass"] = False
                metadata["live_preview_qa"] = qa.as_dict()
                metadata["open_items"] = [
                    f"preview_qa:{code}" for code in qa.failed_gap_codes()[:8]
                ]
                goal.metadata_json = metadata
            await self._append_conversation_event(
                session,
                project_id,
                "PREVIEW_PRODUCT_QA_FAILED",
                (
                    "预览进程已起，但产品面未达标"
                    f"（样式/详情导航等）：{'; '.join(qa.failed_gap_codes()[:6])}"
                ),
                {
                    "goal_id": str(goal_id),
                    "deployment_id": str(deployment_id),
                    "preview_url": public_url,
                    "qa": qa.as_dict(),
                },
            )
        # PenguinHarness-style: evolve skill LESSONS from the failure Trace.
        await self._maybe_evolve_harness_from_qa(
            goal_id=goal_id,
            gaps=qa.failed_gap_codes(),
            preview_url=public_url,
            goal_context=f"deployment={deployment_id} preview_qa_failed",
        )
        return False, qa, public_url

    async def _maybe_evolve_harness_from_qa(
        self,
        *,
        goal_id: uuid.UUID,
        gaps: list[str],
        preview_url: str,
        goal_context: str = "",
    ) -> None:
        """PenguinHarness-style: product QA failure → evolve skill LESSONS (best-effort)."""
        if not gaps:
            return
        try:
            from pathlib import Path

            from regent.application.harness_evolution import HarnessEvolutionService
            from regent.config import get_settings
            from regent.model.factory import build_model_provider

            settings = get_settings()
            svc = HarnessEvolutionService(
                build_model_provider(settings),
                workspace_root=Path(settings.workspace_root),
            )
            receipt = await svc.evolve_from_gaps(
                gaps=list(gaps)[:16],
                actor="regent-core:harness-evolution",
                goal_context=goal_context[:4000],
                preview_url=preview_url or None,
            )
            async with self._sessions() as session, session.begin():
                goal = await session.get(GoalModel, goal_id, with_for_update=True)
                if goal is not None:
                    metadata = dict(goal.metadata_json or {})
                    metadata["harness_evolution"] = receipt.as_dict()
                    goal.metadata_json = metadata
            logger.info(
                "harness evolution from product QA",
                extra={
                    "goal_id": str(goal_id),
                    "status": receipt.status,
                    "skill_id": receipt.skill_id,
                    "baseline_score": receipt.baseline_score,
                    "candidate_score": receipt.candidate_score,
                },
            )
        except Exception:
            logger.warning(
                "harness evolution skipped",
                extra={"goal_id": str(goal_id)},
                exc_info=True,
            )

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

        smoke_service = DeploymentSmokeTestService(
            self._sessions, journey_runner=BrowserJourneyRunner()
        )
        # Observed: runtime binds 127.0.0.1:<port> on the worker host; API/smoke
        # containers cannot reach that loopback. Probe the public preview URL.
        smoke_endpoint = endpoint
        try:
            public_for_smoke = await self._resolve_public_preview_url(
                deployment_id, endpoint
            )
            if public_for_smoke.startswith(("http://", "https://")) and (
                "127.0.0.1" in str(endpoint)
                or "localhost" in str(endpoint)
                or not str(endpoint).startswith(("http://", "https://"))
            ):
                smoke_endpoint = public_for_smoke
        except Exception:
            logger.warning(
                "public preview URL resolve for smoke skipped",
                extra={"goal_id": str(goal_id), "deployment_id": str(deployment_id)},
                exc_info=True,
            )
        smoke_result = await smoke_service.run_smoke_test(
            goal_id, deployment_id, smoke_endpoint, actor=actor
        )
        if not smoke_result.passed:
            logger.warning(
                "smoke test failed — hard-stop (no soft-pass)",
                extra={
                    "goal_id": str(goal_id),
                    "deployment_id": str(deployment_id),
                    "errors": smoke_result.errors,
                },
            )
            try:
                from regent.application.failure_envelope import (
                    FailureEnvelopeService,
                    RecordFailureCommand,
                )

                await FailureEnvelopeService(self._sessions).record_failure(
                    RecordFailureCommand(
                        goal_id=goal_id,
                        stage="smoke",
                        error_code="SMOKE_FAILED",
                        error_summary="; ".join(str(e) for e in (smoke_result.errors or [])[:8])
                        or "smoke failed",
                        evidence_payload={
                            "deployment_id": str(deployment_id),
                            "errors": list(smoke_result.errors or [])[:12],
                        },
                    )
                )
            except Exception:
                logger.warning(
                    "failure envelope record skipped for smoke",
                    extra={"goal_id": str(goal_id)},
                    exc_info=True,
                )
            async with self._sessions() as session, session.begin():
                goal = await session.get(GoalModel, goal_id, with_for_update=True)
                if goal is not None:
                    metadata = dict(goal.metadata_json or {})
                    metadata["execution_stage"] = "SMOKE_FAILED"
                    metadata["preview_ready"] = False
                    metadata["product_surface_ready"] = False
                    metadata["delivery_soft_pass"] = False
                    metadata["open_items"] = [
                        f"smoke:{e}" for e in list(smoke_result.errors or [])[:6]
                    ]
                    goal.metadata_json = metadata
                await self._append_conversation_event(
                    session,
                    project_id,
                    "SMOKE_FAILED",
                    "预览冒烟未通过，拒绝软通过。",
                    {
                        "goal_id": str(goal_id),
                        "deployment_id": str(deployment_id),
                        "errors": list(smoke_result.errors or [])[:12],
                    },
                )
            return

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
                from regent.config import get_settings as _gs_gate

                gates_mode = str(
                    getattr(_gs_gate(), "delivery_product_gates_mode", "soft") or "soft"
                ).lower()
                # Soft/off: require live product QA; process-up alone is not ready.
                if gates_mode in {"soft", "off"} and endpoint:
                    qa_ok, qa, public_url = await self._enforce_live_preview_product_qa(
                        goal_id=goal_id,
                        project_id=project_id,
                        deployment_id=deployment_id,
                        endpoint=endpoint,
                    )
                    if not qa_ok:
                        # Ship-first: do not leave the Goal parked on QA fail —
                        # feed gaps back and resume the same Session to repair.
                        try:
                            reasons = [
                                f"PREVIEW_PRODUCT_QA_FAILED: {code}"
                                for code in (qa.failed_gap_codes() if qa else [])[:8]
                            ] or ["PREVIEW_PRODUCT_QA_FAILED"]
                            await self._recover_or_wait_after_deploy_gap(
                                goal_id,
                                project_id,
                                actor=actor,
                                stage="PREVIEW_PRODUCT_QA_FAILED",
                                message=(
                                    "预览已起但产品面 QA 未达标，回会话继续修："
                                    + "; ".join(reasons[:4])
                                ),
                                gap_reasons=reasons,
                                extra={
                                    "preview_url": public_url,
                                    "deployment_id": str(deployment_id),
                                    "error": (qa.summary if qa else "")[:400],
                                },
                            )
                        except Exception:
                            logger.warning(
                                "preview product QA recovery skipped",
                                extra={"goal_id": str(goal_id)},
                                exc_info=True,
                            )
                        return

                    async with self._sessions() as session, session.begin():
                        goal = await session.get(GoalModel, goal_id, with_for_update=True)
                        if goal is not None:
                            metadata = dict(goal.metadata_json or {})
                            metadata["execution_stage"] = "PREVIEW_SUCCEEDED"
                            metadata["last_gate_status"] = "SOFT_PASS_INSUFFICIENT"
                            metadata["last_deployment_id"] = str(deployment_id)
                            metadata["last_preview_endpoint"] = endpoint
                            metadata["preview_url"] = public_url
                            metadata["preview_ready"] = True
                            metadata["product_surface_ready"] = True
                            metadata["preview_mode"] = "runtime"
                            metadata["delivery_soft_pass"] = True
                            metadata["live_preview_qa"] = qa.as_dict() if qa else {}
                            goal.metadata_json = metadata
                        await self._append_conversation_event(
                            session,
                            project_id,
                            "PREVIEW_SUCCEEDED",
                            f"产品面就绪（QA 通过，仍待产品验收）：{public_url}",
                            {
                                "goal_id": str(goal_id),
                                "deployment_id": str(deployment_id),
                                "endpoint": endpoint,
                                "preview_url": public_url,
                                "product_surface_ready": True,
                                "delivery_soft_pass": True,
                            },
                        )
                    logger.info(
                        "soft-pass insufficient gate → preview ready (product QA ok)",
                        extra={
                            "goal_id": str(goal_id),
                            "deployment_id": str(deployment_id),
                            "preview_url": public_url,
                        },
                    )
                    return

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

            # Gate PASSED/FAILED decision path: still require Live QA before
            # advertising PREVIEW_SUCCEEDED / product_surface_ready.
            qa_ok, qa, public_url = await self._enforce_live_preview_product_qa(
                goal_id=goal_id,
                project_id=project_id,
                deployment_id=deployment_id,
                endpoint=endpoint or "",
            )
            if not qa_ok:
                return

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
                    metadata["preview_url"] = public_url
                    metadata["preview_ready"] = True
                    metadata["product_surface_ready"] = True
                    metadata["live_preview_qa"] = qa.as_dict() if qa else {}
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
                    f"质量评估完成，决策：{decision.decision}（产品面 QA 已通过）。",
                    {
                        "goal_id": str(goal_id),
                        "decision": decision.decision,
                        "gate_status": gate.status,
                        "decision_id": str(decision.id),
                        "product_surface_ready": True,
                        "preview_url": public_url,
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
                # Gate failed and reorganization exhausted: need human, not fake "完成".
                await transitions.transition_goal(
                    TransitionContext(
                        goal_id, expected_version, actor, correlation_id
                    ),
                    GoalCommand.WAIT_FOR_HUMAN,
                )
                async with self._sessions() as session, session.begin():
                    goal = await session.get(GoalModel, goal_id)
                    if goal is not None:
                        metadata = dict(goal.metadata_json or {})
                        metadata["execution_stage"] = "WAITING_HUMAN"
                        metadata["termination"] = {
                            "reason": "iteration_stop_needs_human",
                            "gate_status": gate.status,
                            "gac": "GAC-A1",
                        }
                        goal.metadata_json = metadata
                    await self._append_conversation_event(
                        session,
                        project_id,
                        "HUMAN_TASK_REQUIRED",
                        "质量评估未通过，自动修复预算已用尽，需要你介入后继续。",
                        {
                            "goal_id": str(goal_id),
                            "deployment_id": str(deployment_id),
                            "gate_status": gate.status,
                        },
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
                # No metrics bound yet — still require Live product QA.
                logger.warning(
                    "feedback evaluation skipped: no metric definitions",
                    extra={"goal_id": str(goal_id), "deployment_id": str(deployment_id)},
                )
                qa_ok, qa, public_url = await self._enforce_live_preview_product_qa(
                    goal_id=goal_id,
                    project_id=project_id,
                    deployment_id=deployment_id,
                    endpoint=endpoint or "",
                )
                if not qa_ok:
                    return
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
                        metadata["preview_url"] = public_url
                        metadata["preview_ready"] = True
                        metadata["product_surface_ready"] = True
                        metadata["live_preview_qa"] = qa.as_dict() if qa else {}
                        goal.metadata_json = metadata
                        expected_version = goal.version
                        correlation_id = goal.correlation_id
                    await self._append_conversation_event(
                        session,
                        project_id,
                        "PREVIEW_SUCCEEDED",
                        f"产品面就绪（无 metrics，QA 通过）：{public_url}",
                        {
                            "goal_id": str(goal_id),
                            "deployment_id": str(deployment_id),
                            "endpoint": endpoint,
                            "preview_url": public_url,
                            "product_surface_ready": True,
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
        """GAC-Q1: QualityApprovalCompleted → ACHIEVE or WAITING_HUMAN (never calm EXHAUST)."""
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
            # User rejected quality — wait for direction, do not calm-EXHAUST as "任务结束".
            await TransitionService(self._sessions).transition_goal(
                TransitionContext(goal_id, expected_version, actor, correlation_id),
                GoalCommand.WAIT_FOR_HUMAN,
            )
            async with self._sessions() as session, session.begin():
                goal = await session.get(GoalModel, goal_id)
                if goal is not None:
                    metadata = dict(goal.metadata_json or {})
                    metadata["execution_stage"] = "WAITING_HUMAN"
                    metadata["awaiting_human_intervention"] = True
                    metadata["termination"] = {
                        "reason": "quality_rejected_needs_human",
                        "feedback": feedback_text,
                        "gac": "GAC-Q1",
                        "handoff": "WAITING_HUMAN",
                    }
                    goal.metadata_json = metadata
                await self._append_conversation_event(
                    session,
                    project_id,
                    "HUMAN_TASK_REQUIRED",
                    (
                        "质量未通过确认。"
                        f"反馈：{feedback_text or '（无）'}。"
                        "请补充修改方向后继续；不会标记为已完成。"
                    ),
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
        """GAC-C2: DurableTimer → continue recovery or WAITING_HUMAN (never calm EXHAUST)."""
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
        # GAC-D4: escalate capability/org; resume generation when lineage exists.
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
            # Also try full delivery-gap ladder when generation lineage exists.
            req_uuid, plan_uuid = await self._resolve_generation_ids(goal_id)
            if req_uuid is not None and plan_uuid is not None:
                recovery = await DeliveryGapRecoveryService(self._sessions).recover(
                    goal_id=goal_id,
                    project_id=project_id,
                    requirement_revision_id=req_uuid,
                    capability_resolution_plan_id=plan_uuid,
                    actor=actor,
                    gap_reasons=["gate-insufficient-evidence-timeout"],
                    halt_context={
                        "stage": "GATE_INSUFFICIENT_EVIDENCE",
                        "message": "gate insufficient evidence timeout",
                        "last_error": "gate-insufficient-evidence-timeout",
                    },
                )
                if recovery.recovered:
                    return

        # Auto paths exhausted → WAITING_HUMAN (not calm EXHAUSTED「任务结束」).
        await TransitionService(self._sessions).transition_goal(
            TransitionContext(goal_id, expected_version, actor, correlation_id),
            GoalCommand.WAIT_FOR_HUMAN,
        )
        if project_id is not None:
            async with self._sessions() as session, session.begin():
                goal = await session.get(GoalModel, goal_id)
                if goal is not None:
                    metadata = dict(goal.metadata_json or {})
                    metadata["execution_stage"] = "WAITING_HUMAN"
                    metadata["awaiting_human_intervention"] = True
                    metadata["termination"] = {
                        "reason": "gate_insufficient_timeout_needs_human",
                        "gac": "GAC-C2",
                        "handoff": "WAITING_HUMAN",
                    }
                    metadata.pop("gate_insufficient_timer_id", None)
                    goal.metadata_json = metadata
                await self._append_conversation_event(
                    session,
                    project_id,
                    "HUMAN_TASK_REQUIRED",
                    (
                        "等待运行数据超时；自动重组与交付恢复已用尽。"
                        "需要你补充观察数据、授权来源或修改方向后继续，不会标记为已完成。"
                    ),
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

            # Final milestone or SMALL → require verification PASS (or soft-pass) before ACHIEVE.
            from regent.application.delivery_success_policy import (
                verification_allows_achieve,
            )
            from regent.application.delivery_state import DeliveryState

            metadata = dict(goal.metadata_json or {})
            verification = dict(metadata.get("delivery_verification") or {})
            verdict = str(verification.get("verdict") or "").upper()
            goal_scale = str(
                (plan.goal_scale if plan else None)
                or metadata.get("goal_scale")
                or ""
            )
            has_preview = bool(
                metadata.get("last_preview_endpoint")
                or metadata.get("preview_url")
            )
            allow_achieve, achieve_reason = verification_allows_achieve(
                verification,
                goal_scale=goal_scale,
                has_preview=has_preview,
            )
            # Soft ACHIEVE also requires Live product QA to have passed.
            product_ready = metadata.get("product_surface_ready") in (True, "true", "1")
            live_qa = dict(metadata.get("live_preview_qa") or {})
            if (
                allow_achieve
                and achieve_reason == "soft_pass_preview"
                and not (product_ready and live_qa.get("passed") is True)
            ):
                allow_achieve = False
                achieve_reason = "product_surface_not_ready"
            if not allow_achieve:
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
                if achieve_reason == "soft_pass_preview":
                    metadata["delivery_state"] = DeliveryState.DELIVERED_FOR_REVIEW.value
                    metadata["delivery_soft_pass"] = True
                    # Soft ACHIEVE = delivered for review, not verified COMPLETE.
                    from regent.application.agent_loop_exit import (
                        apply_exit_to_metadata,
                        build_exit,
                        build_result_bundle,
                    )

                    metadata = apply_exit_to_metadata(
                        metadata,
                        build_exit(
                            exit_kind="COMPLETE",
                            stop_reason="soft_preview",
                            session_id=metadata.get("project_agent_session_id"),
                            epoch=metadata.get("project_agent_session_epoch"),
                            result_bundle=build_result_bundle(
                                summary="产品面 QA 通过，已交付审阅（非完整验收 COMPLETE）",
                                preview_url=metadata.get("preview_url")
                                or metadata.get("last_preview_endpoint"),
                                open_items=[
                                    "soft_pass_preview: awaiting human product acceptance"
                                ],
                            ),
                        ),
                    )
                goal.metadata_json = metadata
                version = goal.version
                corr = goal.correlation_id

                await self._append_conversation_event(
                    session,
                    project_id,
                    "QUALITY_SELF_VERIFIED",
                    (
                        "预览已就绪且无阻断缺口，按软通过完成目标。"
                        if achieve_reason == "soft_pass_preview"
                        else "对抗式交付验证通过，正在完成目标。"
                    ),
                    {
                        "goal_id": str(goal_id),
                        "deployment_id": str(deployment_id),
                        "goal_scale": (plan.goal_scale if plan else "UNKNOWN"),
                        "delivery_verification": verification,
                        "achieve_reason": achieve_reason,
                        "gac": "P0-4",
                    },
                )

        if not allow_achieve:
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

        # Accepting Agent ACHIEVEs with Verification PASS or SMALL soft-pass evidence.
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
                # Drop stale mid-loop halt so console does not show prior
                # build/deploy failure next to an achieved goal.
                metadata.pop("halt", None)
                metadata.pop("awaiting_human_intervention", None)
                metadata.pop("awaiting_verification", None)
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
                    "achieve_reason": achieve_reason,
                },
            )
        logger.info(
            "goal achieved with verification",
            extra={
                "goal_id": str(goal_id),
                "actor": actor,
                "achieve_reason": achieve_reason,
            },
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

    async def _summarize_build_failure(self, build_id: uuid.UUID) -> str:
        """Extract a short human-readable reason from verification evidence."""
        async with self._sessions() as session:
            report = await session.scalar(
                select(VerificationReportModel)
                .where(VerificationReportModel.app_build_id == build_id)
                .limit(1)
            )
            build = await session.get(AppBuildModel, build_id)
        if report is not None:
            for check in report.checks or []:
                stdout = str(check.get("stdout") or "")
                stderr = str(check.get("stderr") or "")
                blob = (stdout or stderr).strip()
                if not blob:
                    continue
                # Prefer the first SyntaxError / Error compiling line.
                for line in blob.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("*** Error") or "SyntaxError" in stripped:
                        return stripped[:400]
                return blob[:400]
            if build is not None and build.failure_code:
                return str(build.failure_code)
            return "verification checks failed"
        if build is not None:
            if build.failure_code:
                return str(build.failure_code)
            if build.log_uri:
                return f"status={build.status} log={build.log_uri}"
            return f"status={build.status}"
        return "unknown build failure"

    async def _resolve_generation_ids(
        self, goal_id: uuid.UUID
    ) -> tuple[uuid.UUID | None, uuid.UUID | None]:
        """Locate requirement + capability plan ids for recovery after build failure."""
        async with self._sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            meta = dict((goal.metadata_json if goal else None) or {})
            raw_plan = meta.get("capability_resolution_plan_id")
            raw_req = meta.get("requirement_revision_id")
            if raw_plan and raw_req:
                return uuid.UUID(str(raw_req)), uuid.UUID(str(raw_plan))
            if raw_plan:
                plan = await session.get(
                    CapabilityResolutionPlanModel, uuid.UUID(str(raw_plan))
                )
                if plan is not None:
                    return plan.requirement_revision_id, plan.id
            gen = await session.scalar(
                select(GenerationPlanModel)
                .join(
                    RequirementRevisionModel,
                    RequirementRevisionModel.id
                    == GenerationPlanModel.requirement_revision_id,
                )
                .join(
                    ProductHypothesisModel,
                    ProductHypothesisModel.id == RequirementRevisionModel.hypothesis_id,
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
                return gen.requirement_revision_id, gen.capability_resolution_plan_id
        return None, None

    async def _recover_or_wait_after_deploy_gap(
        self,
        goal_id: uuid.UUID,
        project_id: uuid.UUID,
        *,
        actor: str,
        stage: str,
        message: str,
        gap_reasons: list[str],
        extra: dict[str, object] | None = None,
    ) -> None:
        """GAC-A4: deploy miss → recover/replan; never ACHIEVE or fake-complete STOP.

        Prefer DeliveryGapRecovery (REUSE→COMPOSE→BUILD → regenerate). When the
        ladder is exhausted or generation ids are missing, WAIT_FOR_HUMAN instead
        of FAIL/EXHAUST so the UI does not present “完成/未通过”.
        """
        await self._halt_goal_stage(
            goal_id,
            project_id,
            stage=stage,
            message=message,
            terminal=None,
            actor=actor,
            event_type="ATTAINMENT_RECOVERY_STARTED",
            extra=extra,
        )
        req_uuid, plan_uuid = await self._resolve_generation_ids(goal_id)
        if req_uuid is not None and plan_uuid is not None:
            recovery = await DeliveryGapRecoveryService(self._sessions).recover(
                goal_id=goal_id,
                project_id=project_id,
                requirement_revision_id=req_uuid,
                capability_resolution_plan_id=plan_uuid,
                actor=actor,
                gap_reasons=gap_reasons,
                halt_context={
                    "stage": stage,
                    "message": message,
                    "last_error": str((extra or {}).get("error") or message)[:400],
                    **{k: v for k, v in dict(extra or {}).items() if k != "error"},
                },
            )
            if await self._apply_delivery_verdict(
                recovery,
                goal_id=goal_id,
                project_id=project_id,
                actor=actor,
                recovered_log="deploy failure recovery scheduled",
                stage_exhausted=f"{stage}_NEEDS_HUMAN",
                extra_exhausted={
                    "gap_kind": recovery.gap_kind,
                    "attempts": recovery.attempts,
                    "gac": "GAC-A4",
                    **(extra or {}),
                },
            ):
                return
        await self._halt_goal_stage(
            goal_id,
            project_id,
            stage=f"{stage}_NEEDS_HUMAN",
            message=(
                f"{message} 无法自动重规划（缺少生成链路上下文），需要你介入后继续。"
            ),
            terminal=GoalCommand.WAIT_FOR_HUMAN,
            actor=actor,
            event_type="HUMAN_TASK_REQUIRED",
            extra={"gac": "GAC-A4", **(extra or {})},
        )

    async def _record_generator_mismatch_evidence(
        self,
        *,
        goal_id: uuid.UUID,
        message: str,
        strategy: str,
        generator_ref: str,
    ) -> None:
        """Persist Evidence for fail-closed generator metadata mismatch (GQ-1)."""
        from regent.application.p1_contracts import canonical_hash as _ch

        payload = {
            "kind": "generator-metadata-mismatch",
            "strategy": strategy,
            "expected_generator_ref": generator_ref,
            "message": message,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        digest = _ch(payload)
        async with self._sessions() as session, session.begin():
            session.add(
                EvidenceModel(
                    id=uuid.uuid4(),
                    goal_id=goal_id,
                    evidence_type="generator-metadata-mismatch",
                    uri=None,
                    content_hash=digest,
                    producer_ref="gq1-generator-consistency",
                    quality_tier="OBSERVED",
                    payload=payload,
                )
            )

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
        event_type: str = "GOAL_EXECUTION_STAGE_HALTED",
        append_conversation: bool = True,
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
            if terminal == GoalCommand.WAIT_FOR_HUMAN:
                metadata["awaiting_human_intervention"] = True
            goal.metadata_json = metadata
            from sqlalchemy.orm.attributes import flag_modified

            flag_modified(goal, "metadata_json")
            if append_conversation:
                from regent.application.confirmation_present import (
                    confirmation_for_human_task,
                    enrich_halt_extra,
                )

                event_meta = enrich_halt_extra(
                    event_type,
                    stage,
                    message,
                    {"goal_id": str(goal_id), "stage": stage, **(extra or {})},
                )
                # HUMAN_TASK_REQUIRED without a real HumanTask id leaves Console
                # TaskCard stuck on「缺少 task id」— allow/always-allow cannot complete.
                # Delivery-gap intervene is not a permission gate: never mint that card.
                if event_type == "HUMAN_TASK_REQUIRED" and not (
                    event_meta.get("id") or event_meta.get("human_task_id")
                ):
                    task_type = str(
                        event_meta.get("task_type") or "DELIVERY_GAP_INTERVENE"
                    )
                    if task_type.upper() == "DELIVERY_GAP_INTERVENE" or str(
                        stage
                    ).upper().startswith("DELIVERY_GAP"):
                        event_type = "DELIVERY_SOFT_PAUSE"
                        event_meta.pop("confirmation", None)
                        event_meta.pop("task_type", None)
                        metadata["awaiting_human_intervention"] = False
                        metadata.pop("pending_delivery_gap_human", None)
                        metadata["execution_stage"] = "DELIVERY_SOFT_PAUSE"
                        goal.metadata_json = metadata
                        flag_modified(goal, "metadata_json")
                    else:
                        task_id = uuid.uuid4()
                        confirmation = event_meta.get("confirmation")
                        if not isinstance(confirmation, dict):
                            confirmation = confirmation_for_human_task(
                                task_type=task_type,
                                summary=message[:200],
                                rationale=f"阶段 {stage} 需要人工确认",
                                detail=message[:500],
                                prompt=message,
                                extra_rules=[f"stage:{stage}"],
                            )
                            event_meta["confirmation"] = confirmation
                        timeout_sec = int(
                            (confirmation or {}).get("timeout_seconds") or 300
                        )
                        session.add(
                            HumanTaskModel(
                                id=task_id,
                                goal_id=goal_id,
                                work_id=None,
                                run_id=None,
                                task_type=task_type,
                                prompt=message[:2000],
                                requested_by=actor,
                                due_at=datetime.now(UTC)
                                + timedelta(seconds=max(timeout_sec, 60)),
                                status="OPEN",
                            )
                        )
                        event_meta["id"] = str(task_id)
                        event_meta["human_task_id"] = str(task_id)
                        event_meta["task_type"] = task_type
                        metadata["pending_delivery_gap_human"] = {
                            "human_task_id": str(task_id),
                            "gap_kind": str((extra or {}).get("gap_kind") or ""),
                            "stage": stage,
                        }
                        goal.metadata_json = metadata
                        flag_modified(goal, "metadata_json")
                await self._append_conversation_event(
                    session,
                    project_id,
                    event_type,
                    message,
                    event_meta,
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

    async def _open_work_plan_items(self, goal_id: uuid.UUID) -> list[str]:
        """W2: COMPLETE result_bundle lists unfinished plan items (Q3)."""
        try:
            from regent.application.execution_plan import ExecutionPlanService
            from regent.application.work_plan import open_plan_item_contents

            items = await ExecutionPlanService(self._sessions).list_items(goal_id)
            return open_plan_item_contents([i.as_dict() for i in items])
        except Exception:
            logger.warning(
                "open work plan items lookup failed",
                extra={"goal_id": str(goal_id)},
                exc_info=True,
            )
            return []

    async def _stamp_agent_loop_complete(
        self,
        *,
        goal_id: uuid.UUID,
        project_id: uuid.UUID,
        generation_run_id: uuid.UUID,
        summary: str,
        open_items: list[str] | None = None,
        preview_url: str | None = None,
    ) -> None:
        """A0: persist COMPLETE exit (does not ACHIEVE Goal — Q4)."""
        from sqlalchemy.orm.attributes import flag_modified

        from regent.application.agent_loop_exit import (
            apply_exit_to_metadata,
            build_ask_envelope,
            build_exit,
            build_result_bundle,
            conversation_copy_for_exit,
            evaluate_complete_allowed,
            progress_loop_detected,
        )

        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                return
            meta = dict(goal.metadata_json or {})
            outcome = "progress_loop" if progress_loop_detected(meta) else "success"
            verdict = evaluate_complete_allowed(
                outcome,
                metadata=meta,
                open_items=open_items,
            )
            if not verdict["safe"]:
                # O0: never forge COMPLETE — degrade to ASK_HUMAN.
                ask = build_ask_envelope(
                    question=(
                        "本轮未达诚实完成条件，需要你确认后再继续。"
                        f"\n原因：{verdict['reason']}"
                    ),
                    why_blocked=str(verdict["reason"]),
                    ask_type="complete_guard",
                    gap_kind=str(verdict.get("blocker") or "complete_blocked"),
                )
                exit_payload = build_exit(
                    exit_kind="ASK_HUMAN",
                    stop_reason=f"complete_guard:{verdict.get('blocker')}",
                    lease_id=generation_run_id,
                    session_id=meta.get("project_agent_session_id"),
                    epoch=meta.get("project_agent_session_epoch"),
                    ask_envelope=ask,
                    draft_uri=meta.get("last_good_draft_uri"),
                )
            else:
                exit_payload = build_exit(
                    exit_kind="COMPLETE",
                    stop_reason="verified_pass",
                    lease_id=generation_run_id,
                    session_id=meta.get("project_agent_session_id"),
                    epoch=meta.get("project_agent_session_epoch"),
                    result_bundle=build_result_bundle(
                        summary=summary,
                        preview_url=preview_url or meta.get("last_preview_endpoint"),
                        artifact_uri=meta.get("last_good_draft_uri"),
                        evidence_summary="verification passed for this lease",
                        open_items=open_items or [],
                    ),
                )
            meta = apply_exit_to_metadata(meta, exit_payload)
            # Clear soft-pause / ask markers after real COMPLETE.
            meta.pop("ops_soft_pause", None)
            meta.pop("pending_agent_loop_ask", None)
            meta["session_resume_attempts"] = 0
            msg_type, content = conversation_copy_for_exit(exit_payload)
            from regent.application.live_action import merge_live_action_into_metadata

            meta = merge_live_action_into_metadata(
                meta,
                content.split("\n")[0][:120],
                stage="AGENT_LOOP_COMPLETE",
                event_type=msg_type,
            )
            goal.metadata_json = meta
            flag_modified(goal, "metadata_json")
            await self._append_conversation_message(
                session,
                project_id,
                role="ASSISTANT",
                message_type=msg_type,
                content=content,
                metadata={
                    "goal_id": str(goal_id),
                    "agent_loop_exit": exit_payload,
                    "generation_run_id": str(generation_run_id),
                },
            )

    async def _apply_delivery_verdict(
        self,
        recovery: "DeliveryGapRecoveryResult",
        *,
        goal_id: uuid.UUID,
        project_id: uuid.UUID,
        actor: str,
        recovered_log: str,
        stage_exhausted: str,
        extra_exhausted: dict[str, object] | None = None,
        append_conversation: bool = True,
    ) -> bool:
        """收口 DeliveryGapRecovery 终态（AC1 集中化）。

        复刻既有 ``if recovered … elif terminal_exhaust: _halt_goal_stage(WAIT_FOR_HUMAN)``
        行为；返回 True 表示已处理（调用方应 return），否则调用方继续其既有 fallback。

        CD-1.2/CD-5: 用 ``decide_delivery_verdict`` 把 recovery 结果映射为显式
        ``DeliveryState``，写入 ``goal.metadata_json`` 并发 ``DeliveryStateChanged``
        Outbox 事件，使状态转移可被观测/统计（north_star handoff_rate 等）。
        """
        if recovery.recovered:
            verdict = decide_delivery_verdict(
                success=False,
                needs_human=False,
                recoverable=True,
                budget_left=True,
            )
        elif recovery.terminal_exhaust:
            verdict = decide_delivery_verdict(
                success=False,
                needs_human=True,
                recoverable=True,
                budget_left=False,
                review_prompt=recovery.message,
            )
        else:
            verdict = decide_delivery_verdict(
                success=False,
                needs_human=False,
                recoverable=False,
                budget_left=False,
            )
        await self._record_delivery_state(
            goal_id,
            state=verdict.state,
            gap_kind=recovery.gap_kind,
            attempts=recovery.attempts,
        )
        if recovery.recovered:
            logger.info(
                recovered_log,
                extra={"goal_id": str(goal_id), "attempts": recovery.attempts},
            )
            return True
        if recovery.terminal_exhaust:
            # Soft-pause is not a permission gate: do not WAIT_FOR_HUMAN /
            # HUMAN_TASK_REQUIRED (that mints Console「总是允许」cards).
            if recovery.method in {"SOFT_PAUSE", "ASK_HUMAN", "STOP"}:
                logger.info(
                    "delivery gap exited without permission TaskCard",
                    extra={
                        "goal_id": str(goal_id),
                        "gap_kind": recovery.gap_kind,
                        "attempts": recovery.attempts,
                        "method": recovery.method,
                    },
                )
                return True
            await self._halt_goal_stage(
                goal_id,
                project_id,
                stage=stage_exhausted,
                message=recovery.message,
                terminal=GoalCommand.WAIT_FOR_HUMAN,
                actor=actor,
                event_type="HUMAN_TASK_REQUIRED",
                extra=extra_exhausted,
                append_conversation=append_conversation,
            )
            return True
        return False

    async def _record_delivery_state(
        self,
        goal_id: uuid.UUID,
        *,
        state: DeliveryState,
        gap_kind: str,
        attempts: int,
    ) -> None:
        """CD-1.2/CD-5: persist ``delivery_state`` + emit ``DeliveryStateChanged``."""
        from sqlalchemy.orm.attributes import flag_modified

        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                return
            metadata = dict(goal.metadata_json or {})
            metadata["delivery_state"] = state.value
            goal.metadata_json = metadata
            flag_modified(goal, "metadata_json")
            session.add(
                make_outbox_event(
                    EventEnvelope(
                        event_type=DELIVERY_STATE_CHANGED,
                        aggregate_type="goal",
                        aggregate_id=goal.id,
                        aggregate_version=goal.version,
                        payload={
                            "goal_id": str(goal.id),
                            "delivery_state": state.value,
                            "gap_kind": gap_kind,
                            "attempts": attempts,
                        },
                        correlation_id=goal.correlation_id,
                    )
                )
            )

    # ---------------------------------------------------------------------------
    # Helper methods
    # ---------------------------------------------------------------------------

    @staticmethod
    def _local_path_from_uri(uri: str | None) -> Path | None:
        from urllib.parse import unquote, urlparse

        raw = str(uri or "").strip()
        if not raw:
            return None
        if not raw.startswith("file:"):
            path = Path(raw)
            return path if path.exists() else None
        parsed = urlparse(raw)
        raw_path = unquote(parsed.path)
        if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        path = Path(raw_path)
        return path if path.exists() else None

    async def _resolve_revise_base_workspace(
        self, goal_id: uuid.UUID
    ) -> Path | None:
        """P0-5/R1: accepted → recoverable snapshot → last_good_draft."""
        from regent.agent.accepted_workspace import clone_accepted_snapshot
        from regent.config import get_settings

        async with self._sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                return None
            meta = dict(goal.metadata_json or {})
            workspace_root = Path(get_settings().workspace_root)
            accepted_uri = str(meta.get("last_accepted_workspace_uri") or "").strip()
            if accepted_uri:
                dest = (
                    workspace_root
                    / "revise_from_accepted"
                    / str(goal_id)
                    / str(uuid.uuid4())
                )
                return clone_accepted_snapshot(accepted_uri, dest)
            recoverable_uri = str(
                meta.get("last_recoverable_workspace_uri") or ""
            ).strip()
            if recoverable_uri:
                dest = (
                    workspace_root
                    / "revise_from_recoverable"
                    / str(goal_id)
                    / str(uuid.uuid4())
                )
                try:
                    return clone_accepted_snapshot(recoverable_uri, dest)
                except (FileNotFoundError, OSError):
                    pass
            draft = self._local_path_from_uri(str(meta.get("last_good_draft_uri") or ""))
            if draft is not None and draft.is_dir():
                return draft
            return None

    async def _resolve_last_good_draft_workspace(
        self, goal_id: uuid.UUID
    ) -> Path | None:
        """Deprecated alias — prefer accepted snapshot via ``_resolve_revise_base_workspace``."""
        return await self._resolve_revise_base_workspace(goal_id)

    async def _remember_generation_attempt(
        self,
        goal_id: uuid.UUID,
        *,
        generation_run_id: uuid.UUID,
        plan_id: uuid.UUID | None,
        completed: bool = False,
    ) -> None:
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                return
            meta = dict(goal.metadata_json or {})
            meta["last_generation_run_id"] = str(generation_run_id)
            if plan_id is not None:
                meta["last_generation_plan_id"] = str(plan_id)
            history = list(meta.get("generation_attempt_history") or [])
            history.append(
                {
                    "generation_run_id": str(generation_run_id),
                    "plan_id": str(plan_id) if plan_id else None,
                    "completed": completed,
                    "at": datetime.now(UTC).isoformat(),
                }
            )
            meta["generation_attempt_history"] = history[-12:]
            goal.metadata_json = meta
            flag_modified(goal, "metadata_json")

    async def _record_generation_failure_memory(
        self,
        *,
        goal_id: uuid.UUID,
        project_id: uuid.UUID,
        generation_run_id: uuid.UUID | None,
        plan_id: uuid.UUID | None,
        exc: BaseException,
    ) -> None:
        """Persist FailureEnvelope + visible attempt message so errors can be learned."""
        from regent.application.failure_envelope import (
            FailureEnvelopeService,
            RecordFailureCommand,
        )

        reasons = reasons_from_exception(exc)
        error_code = (
            exc.code.value
            if isinstance(exc, DomainError)
            else type(exc).__name__
        )
        draft_uri = getattr(exc, "draft_uri", None)
        summary = "; ".join(reasons[:6]) if reasons else str(exc)[:400]
        if generation_run_id is not None:
            await self._remember_generation_attempt(
                goal_id,
                generation_run_id=generation_run_id,
                plan_id=plan_id,
                completed=False,
            )
        try:
            await FailureEnvelopeService(self._sessions).record_failure(
                RecordFailureCommand(
                    goal_id=goal_id,
                    stage="generation",
                    error_summary=summary,
                    error_code=error_code,
                    generation_plan_id=plan_id,
                    generation_run_id=generation_run_id,
                    evidence_artifact_uri=str(draft_uri) if draft_uri else None,
                    evidence_payload={
                        "reasons": reasons[:12],
                        "exception_type": type(exc).__name__,
                    },
                )
            )
        except Exception:
            logger.warning(
                "generation FailureEnvelope record skipped",
                extra={"goal_id": str(goal_id)},
                exc_info=True,
            )
        try:
            async with self._sessions() as session, session.begin():
                await self._append_conversation_message(
                    session,
                    project_id,
                    role="ASSISTANT",
                    message_type="GENERATION_ATTEMPT_FAILED",
                    content=(
                        "本轮生成未通过，已记录失败原因并将带着约束重试："
                        f"{summary[:240]}"
                    ),
                    metadata={
                        "goal_id": str(goal_id),
                        "generation_run_id": str(generation_run_id) if generation_run_id else None,
                        "plan_id": str(plan_id) if plan_id else None,
                        "error_code": error_code,
                        "gap_reasons": reasons[:12],
                        "draft_uri": str(draft_uri) if draft_uri else None,
                        "learning": True,
                    },
                )
                goal = await session.get(GoalModel, goal_id)
                if goal is not None:
                    from regent.application.goal_runtime_plan import append_failure_lesson

                    meta = dict(goal.metadata_json or {})
                    meta = append_failure_lesson(
                        meta,
                        code=str(error_code or "GENERATION_FAILED"),
                        summary=summary[:400],
                        avoid=(
                            "下次生成须避开本轮失败模式；优先采用 failure_lessons "
                            "与 learned_constraints 中的约束"
                        ),
                        gap_kind="generation",
                        extra={
                            "generation_run_id": (
                                str(generation_run_id) if generation_run_id else None
                            ),
                            "plan_id": str(plan_id) if plan_id else None,
                            "reasons": reasons[:8],
                        },
                    )
                    if draft_uri:
                        meta["last_good_draft_uri"] = str(draft_uri)
                        # R1: failure path warm-start — snapshot so REVISE is not cold.
                        try:
                            from regent.agent.accepted_workspace import (
                                write_recoverable_workspace_snapshot,
                            )
                            from regent.config import get_settings

                            draft_path = self._local_path_from_uri(str(draft_uri))
                            if draft_path is not None and draft_path.is_dir():
                                meta["last_recoverable_workspace_uri"] = (
                                    write_recoverable_workspace_snapshot(
                                        draft_path,
                                        Path(get_settings().workspace_root),
                                        reason=str(error_code or "generation_failed"),
                                    )
                                )
                            else:
                                meta["last_recoverable_workspace_uri"] = str(draft_uri)
                        except Exception:
                            meta["last_recoverable_workspace_uri"] = str(draft_uri)
                    goal.metadata_json = meta
                    flag_modified(goal, "metadata_json")
        except Exception:
            logger.warning(
                "generation attempt conversation skipped",
                extra={"goal_id": str(goal_id)},
                exc_info=True,
            )

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
        """Append event message to conversation timeline and refresh live_action."""
        await ExecutionOrchestrator._append_conversation_message(
            session,
            project_id,
            role="EVENT",
            message_type=message_type,
            content=content,
            metadata=dict(metadata),
        )
        goal_raw = metadata.get("goal_id")
        if not goal_raw:
            return
        try:
            goal_id = uuid.UUID(str(goal_raw))
        except ValueError:
            return
        goal = await session.get(GoalModel, goal_id)
        if goal is None:
            return
        from regent.application.live_action import apply_live_action_on_goal, summary_for_event

        stage = None
        if isinstance(goal.metadata_json, dict):
            stage = goal.metadata_json.get("execution_stage")
        apply_live_action_on_goal(
            goal,
            summary_for_event(message_type, content),
            stage=str(stage) if stage else None,
            detail=content[:240] if content else None,
            event_type=message_type,
        )


# ---------------------------------------------------------------------------
# P1 main chain event handler mapping (for worker registration)
# ---------------------------------------------------------------------------


async def _ack_delivery_state_changed(payload: dict[str, object]) -> None:
    """Ack DeliveryStateChanged — metadata already written; no side effects."""
    return None


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
        RELEASE_APPROVAL_COMPLETED: orchestrator.handle_release_approval_completed,
        DELIVERY_GAP_HUMAN_APPROVED: orchestrator.handle_delivery_gap_human_approved,
        # Observability-only; state already persisted on goal.metadata.
        DELIVERY_STATE_CHANGED: _ack_delivery_state_changed,
        "TimerFired": orchestrator.handle_timer_fired,
        # P1-C: V3 domain event handlers
        "ReorganizationTriggered": orchestrator.handle_reorganization_triggered,
        "ConstraintViolated": orchestrator.handle_constraint_violated,
        "OrganizationSelected": orchestrator.handle_organization_selected,
    }
