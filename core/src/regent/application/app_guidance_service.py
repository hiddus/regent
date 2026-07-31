import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from regent.application.evidence_policy import extract_urls_from_text
from regent.application.execution_events import (
    DISCOVERY_ROUND_REQUESTED,
    EventEnvelope,
    make_idempotency_key,
    make_outbox_event,
)
from regent.application.goal_execution_service import GoalExecutionService
from regent.application.human_task_service import HumanTaskService
from regent.application.p1_contracts import canonical_hash
from regent.application.transition_service import TransitionContext, TransitionService
from regent.domain.errors import DomainError, ErrorCode
from regent.domain.transitions import GoalCommand
from regent.infrastructure.models import (
    AgentSpecModel,
    AppBuildModel,
    AppPreviewReleaseModel,
    AppProjectModel,
    CapabilityResolutionPlanModel,
    ConversationCommandModel,
    ConversationMessageModel,
    ConversationModel,
    DeploymentModel,
    DiscoveryRoundModel,
    GenerationPlanModel,
    GenerationRunModel,
    GoalModel,
    GoalSpecModel,
    HumanTaskModel,
    HypothesisDecisionModel,
    OutboxEventModel,
    ProductHypothesisModel,
    ReleaseCandidateModel,
    RequirementRevisionModel,
    WorkModel,
    WorkspaceSnapshotModel,
)
from regent.model import ModelProvider

# Console-facing role labels (Simplified Chinese).
_AGENT_ROLE_LABELS: dict[str, str] = {
    "core": "主助手",
    "executor": "执行",
    "pm": "产品",
    "dev": "开发",
    "qa": "质检",
    "coordinator": "协调",
    "reviewer": "审查",
}

_ACTIVE_TASK_STATUSES = frozenset({"CREATED", "OFFERED", "ACCEPTED", "RUNNING", "RECONCILING"})
_DONE_TASK_STATUSES = frozenset({"SUCCEEDED", "CANCELLED"})
_FAILED_TASK_STATUSES = frozenset({"FAILED_RETRYABLE", "FAILED_TERMINAL", "TIMED_OUT", "UNKNOWN"})
_ACTIVE_DEPLOY_STATUSES = frozenset({"OPERATING", "DEPLOYED", "UPGRADING"})


# ---------------------------------------------------------------------------
# Stage labels for human-friendly display
# ---------------------------------------------------------------------------

_STAGE_LABELS: dict[str, str] = {
    "NOT_STARTED": "未开始",
    "QUEUED": "排队中",
    "DISCOVERING": "产品发现中",
    "DECIDED": "方案已决策",
    "RESOLVED": "能力已解析",
    "GENERATING": "代码生成中",
    "SNAPSHOT_READY": "快照就绪",
    "BUILD_PASSED": "构建通过",
    "DEPLOYED": "预览已部署",
    "RESEARCH_MORE": "研究中",
    "PREVIEW_SUCCEEDED": "预览成功",
    "GATE_INSUFFICIENT_EVIDENCE": "证据不足",
    "GATE_PASSED": "门禁通过",
    "GATE_FAILED": "门禁未通过",
    "FAILED": "失败",
}


class GuidanceInterpretation(BaseModel):
    """LLM-interpreted user guidance command."""

    command_type: Literal[
        "QUERY",
        "MODIFY",
        "CONTINUE",
        "PAUSE",
        "RESUME",
        "CORRECT",
        "APPROVE",
        "REJECT",
    ] = Field(
        description=(
            "QUERY: read status/history. "
            "MODIFY: change goal objectives/significantly redirect (creates new revision). "
            "CONTINUE: proceed without changing the goal. "
            "PAUSE: temporarily halt execution. "
            "RESUME: resume after pause. "
            "CORRECT: lightweight mid-execution correction (e.g. 'use REST not GraphQL', "
            "'add dark mode', 'change the API response format'). Creates a newer GoalSpec snapshot without requiring confirmation. "
            "APPROVE: approve a pending gate or human task. "
            "REJECT: reject a pending gate result, trigger revision."
        )
    )
    summary: str = Field(min_length=1)
    # MODIFY fields
    objective: str | None = None
    product_intent: str | None = None
    target_users: str | None = None
    problem: str | None = None
    first_deliverable: str | None = None
    success_criteria: dict[str, str | int | float | bool] | None = None
    explicit_constraints: dict[str, str | int | float | bool] | None = None
    non_goals: list[str] | None = None
    unknowns: list[str] | None = None
    # CORRECT fields
    correction_target: str | None = Field(
        default=None,
        description="What aspect to correct: 'requirements', 'design', 'api', 'constraints', 'behavior', 'other'",
    )
    correction_detail: str | None = Field(
        default=None,
        description="Detailed description of the correction to apply",
    )
    # APPROVE/REJECT fields
    rejection_reason: str | None = None


@dataclass(frozen=True, slots=True)
class GuidanceReceipt:
    command_id: uuid.UUID
    command_type: str
    resulting_goal_id: uuid.UUID | None
    requires_confirmation: bool
    response: str


class AppGuidanceService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: ModelProvider,
    ) -> None:
        self._sessions = sessions
        self._provider = provider

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def guide(self, project_id: uuid.UUID, *, message: str, actor: str) -> GuidanceReceipt:
        context = await self._context(project_id)
        history = await self._conversation_history(project_id, limit=10)

        generated = await self._provider.generate_structured(
            system_prompt=self._system_prompt(context, history),
            user_prompt=str({"current_state": context, "recent_messages": history, "user_message": message}),
            response_model=GuidanceInterpretation,
        )
        interpretation = generated.output

        # Check for URL-based research resume first
        resumed = await self._maybe_resume_research_more(project_id, message, actor)
        if resumed is not None:
            return resumed

        handler = {
            "QUERY": self._handle_query,
            "CONTINUE": self._handle_continue,
            "MODIFY": self._handle_modify,
            "PAUSE": self._handle_pause,
            "RESUME": self._handle_resume,
            "CORRECT": self._handle_correct,
            "APPROVE": self._handle_approve,
            "REJECT": self._handle_reject,
        }.get(interpretation.command_type)
        if handler is None:
            return await self._handle_query(
                project_id, message, actor, interpretation, generated.model
            )
        return await handler(project_id, message, actor, interpretation, generated.model)

    # ------------------------------------------------------------------
    # System prompt builder — gives LLM full context
    # ------------------------------------------------------------------

    def _system_prompt(self, context: dict[str, Any], history: list[dict[str, Any]]) -> str:
        goal_status = context.get("goal", {}).get("status", "UNKNOWN")
        stage_info = context.get("goal", {}).get("execution_stage", {})
        stage = stage_info.get("stage", "UNKNOWN") if isinstance(stage_info, dict) else "UNKNOWN"
        stage_label = _STAGE_LABELS.get(stage, stage)
        pending_tasks = context.get("pending_human_tasks", [])
        active_corrections = context.get("active_corrections", [])

        parts = [
            "You are Regent Core's conversation assistant. Classify the user's follow-up message.",
            "",
            f"Current goal status: {goal_status}",
            f"Current execution stage: {stage} ({stage_label})",
        ]
        if pending_tasks:
            parts.append(f"Pending human tasks: {len(pending_tasks)} (user can APPROVE or REJECT)")
        if active_corrections:
            parts.append(f"Active corrections applied: {len(active_corrections)}")
        if stage == "RESEARCH_MORE":
            parts.append("Hint: user may paste authorized source URLs to resume discovery.")
        if goal_status == "PAUSED":
            parts.append("Hint: goal is paused, user likely wants to RESUME or CORRECT.")
        if goal_status == "WAITING_HUMAN":
            parts.append("Hint: goal is waiting for human input, user should APPROVE or REJECT.")

        parts.extend([
            "",
            "Command types:",
            "- QUERY: user asks about status, progress, or details. Answer informatively.",
            "- MODIFY: user wants to significantly change objectives/users/problem/deliverable. Creates and starts a new goal revision without a confirmation gate.",
            "- CONTINUE: user says 'go ahead', 'continue', 'proceed'. Starts or retries execution.",
            "- PAUSE: user says 'pause', 'stop', 'wait', 'hold'. Temporarily halts execution.",
            "- RESUME: user says 'resume', 'continue after pause', 'go'. Resumes a paused goal.",
            "- CORRECT: user gives a specific mid-execution correction (e.g. 'use REST not GraphQL', "
            "'add dark mode', 'change API format to JSON', 'add error handling'). "
            "This creates a newer GoalSpec snapshot so later stages use the correction. "
            "Set correction_target and correction_detail fields.",
            "- APPROVE: user says 'approve', 'looks good', 'accept', 'yes'. Approves pending gate/human task.",
            "- REJECT: user says 'reject', 'no', 'wrong', 'this is not right'. Rejects pending gate, triggers revision.",
            "",
            "For CORRECT, always set correction_target (one of: requirements, design, api, constraints, behavior, other) "
            "and correction_detail (the specific change requested).",
            "For MODIFY, return a complete revised proposal using supplied context and the user's message.",
            "Never execute or grant permissions.",
        ])
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Context gathering
    # ------------------------------------------------------------------

    async def status(self, project_id: uuid.UUID) -> dict[str, Any]:
        return await self._context(project_id)

    async def _context(self, project_id: uuid.UUID) -> dict[str, Any]:
        async with self._sessions() as session:
            project = await session.get(AppProjectModel, project_id)
            if project is None:
                raise DomainError(ErrorCode.NOT_FOUND, "app project not found")
            goal = await session.scalar(
                select(GoalModel)
                .where(GoalModel.app_project_id == project_id)
                .order_by(GoalModel.created_at.desc())
            )
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, "app project goal not found")
            spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == goal.id)
                .order_by(GoalSpecModel.version.desc())
            )
            preview = await session.scalar(
                select(AppPreviewReleaseModel)
                .where(AppPreviewReleaseModel.goal_id == goal.id)
                .order_by(AppPreviewReleaseModel.created_at.desc())
            )
            work_rows = (
                await session.execute(
                    select(WorkModel.status, func.count())
                    .where(WorkModel.goal_id == goal.id)
                    .group_by(WorkModel.status)
                )
            ).all()
            work_states: dict[str, int] = {
                str(work_status): int(count) for work_status, count in work_rows
            }
            stage = await self._project_execution_stage(session, goal.id)

            # Fetch pending human tasks
            human_tasks = (
                await session.execute(
                    select(HumanTaskModel)
                    .where(
                        HumanTaskModel.goal_id == goal.id,
                        HumanTaskModel.status == "OPEN",
                    )
                    .order_by(HumanTaskModel.created_at.desc())
                    .limit(5)
                )
            ).scalars().all()
            from regent.application.confirmation_present import confirmation_for_human_task

            pending_tasks = [
                {
                    "id": str(t.id),
                    "task_type": t.task_type,
                    "prompt": t.prompt,
                    "due_at": t.due_at.isoformat() if t.due_at else None,
                    "confirmation": confirmation_for_human_task(
                        task_type=t.task_type,
                        summary=t.task_type,
                        prompt=t.prompt,
                    ),
                }
                for t in human_tasks
            ]

            # Fetch active corrections from metadata
            metadata = goal.metadata_json or {}
            active_corrections = metadata.get("active_corrections", [])

            preview_payload: dict[str, Any] | None = None
            if preview is not None:
                preview_payload = {
                    "id": str(preview.id),
                    "status": preview.status,
                    "endpoint": preview.preview_endpoint,
                    "failure_code": preview.failure_code,
                    "failure_summary": preview.failure_summary,
                }
            else:
                # P1 durable path stores the live preview on goal metadata, not
                # app_preview_releases. Surface it so the console artifact panel
                # can show the deliverable after ACHIEVE.
                endpoint = metadata.get("last_preview_endpoint")
                if isinstance(endpoint, str) and endpoint.strip():
                    preview_payload = {
                        "id": None,
                        "status": "PREVIEW_READY",
                        "endpoint": endpoint.strip(),
                        "failure_code": None,
                        "failure_summary": None,
                        "source": "goal_metadata",
                    }

            agents = await self._goal_agents_for_console(
                session,
                goal_id=goal.id,
                goal_status=goal.status,
                metadata=metadata if isinstance(metadata, dict) else {},
            )

            return {
                "project": {
                    "name": project.name,
                    "product_intent": project.product_intent,
                    "status": project.status,
                },
                "goal": {
                    "id": str(goal.id),
                    "objective": goal.original_input,
                    "status": goal.status,
                    "version": goal.version,
                    "metadata": goal.metadata_json,
                    "execution_stage": stage,
                },
                "goal_spec": {
                    "explicit_constraints": spec.explicit_constraints if spec else {},
                    "success_criteria": spec.success_criteria if spec else {},
                    "id": str(spec.id) if spec else None,
                    "version": spec.version if spec else None,
                    "status": spec.status if spec else None,
                    "content_hash": spec.content_hash if spec else None,
                    "unknowns": spec.unknowns if spec else [],
                },
                "work_states": work_states,
                "preview": preview_payload,
                "pending_human_tasks": pending_tasks,
                "active_corrections": active_corrections,
                "agents": agents,
            }

    async def _conversation_history(
        self, project_id: uuid.UUID, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Fetch recent conversation messages for LLM context."""
        async with self._sessions() as session:
            conversation = await session.scalar(
                select(ConversationModel).where(ConversationModel.app_project_id == project_id)
            )
            if conversation is None:
                return []
            rows = (
                await session.execute(
                    select(ConversationMessageModel)
                    .where(ConversationMessageModel.conversation_id == conversation.id)
                    .order_by(ConversationMessageModel.ordinal.desc())
                    .limit(limit)
                )
            ).scalars().all()
            return [
                {
                    "role": msg.role,
                    "type": msg.message_type,
                    "content": msg.content[:500] if msg.content else "",
                }
                for msg in reversed(rows)
            ]

    # ------------------------------------------------------------------
    # Console agents (Hive deployments / AgentSpec / live_action fallback)
    # ------------------------------------------------------------------

    @staticmethod
    async def _goal_agents_for_console(
        session: AsyncSession,
        *,
        goal_id: uuid.UUID,
        goal_status: str,
        metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Read-only agent roster for the console right panel.

        Prefers Hive AgentDeployment rows; falls back to scoped AgentSpec;
        always includes a synthetic Core/主助手 entry driven by live_action.
        """
        live = metadata.get("live_action") if isinstance(metadata.get("live_action"), dict) else {}
        live_summary = str(live.get("summary") or "").strip() or None
        goal_active = goal_status in {
            "ACTIVE",
            "WAITING_HUMAN",
            "PAUSED",
            "READY",
            "BLOCKED",
            "EXHAUSTED",
        }
        terminal = goal_status in {"ACHIEVED", "CANCELLED", "FAILED"}

        core_activity = "idle"
        if terminal:
            core_activity = "done" if goal_status == "ACHIEVED" else "failed"
        elif goal_status == "WAITING_HUMAN":
            core_activity = "waiting"
        elif goal_active and live_summary:
            core_activity = "active"
        elif goal_active:
            core_activity = "ready"

        agents: list[dict[str, Any]] = [
            {
                "id": "core",
                "name": "主助手",
                "role": "core",
                "role_label": _AGENT_ROLE_LABELS["core"],
                "kind": "core",
                "activity": core_activity,
                "detail": live_summary,
                "is_main": True,
            }
        ]

        # Prefer durable hive deployments when present.
        try:
            from regent.infrastructure.aar1_models import AgentDeploymentModel, AgentTaskModel

            deployments = list(
                await session.scalars(
                    select(AgentDeploymentModel)
                    .where(AgentDeploymentModel.goal_id == goal_id)
                    .order_by(AgentDeploymentModel.created_at.asc())
                )
            )
            if deployments:
                # Latest task per deployment for activity.
                task_by_dep: dict[uuid.UUID, Any] = {}
                tasks = list(
                    await session.scalars(
                        select(AgentTaskModel)
                        .where(AgentTaskModel.goal_id == goal_id)
                        .order_by(AgentTaskModel.updated_at.desc())
                        .limit(80)
                    )
                )
                for task in tasks:
                    dep_id = task.target_deployment_id
                    if dep_id not in task_by_dep:
                        task_by_dep[dep_id] = task

                for dep in deployments:
                    role = str(dep.role or "executor")
                    task = task_by_dep.get(dep.id)
                    activity = "idle"
                    detail: str | None = None
                    if dep.status in {"FAILED", "RETIRED", "SUSPENDED"}:
                        activity = "failed" if dep.status == "FAILED" else "idle"
                    elif task is not None:
                        detail = str(task.task_type or "") or None
                        if task.status in _ACTIVE_TASK_STATUSES:
                            activity = "active"
                        elif task.status in _DONE_TASK_STATUSES:
                            activity = "done"
                        elif task.status in _FAILED_TASK_STATUSES:
                            activity = "failed"
                        elif task.status == "MANUAL_REVIEW":
                            activity = "waiting"
                        elif dep.status in _ACTIVE_DEPLOY_STATUSES:
                            activity = "ready"
                    elif dep.status in _ACTIVE_DEPLOY_STATUSES:
                        activity = "ready" if goal_active else "idle"
                    elif dep.status == "PENDING":
                        activity = "ready" if goal_active else "idle"

                    short_id = str(dep.id).replace("-", "")[:8]
                    display = _AGENT_ROLE_LABELS.get(role) or role
                    agents.append(
                        {
                            "id": str(dep.id),
                            "name": f"{display} · {short_id}",
                            "role": role,
                            "role_label": display,
                            "kind": "hive",
                            "activity": activity,
                            "detail": detail,
                            "is_main": False,
                            "deployment_status": dep.status,
                        }
                    )
                return agents
        except Exception:
            # Tables may be absent in older envs; fall through to AgentSpec.
            pass

        # Legacy organization AgentSpec scoped to this goal.
        try:
            specs = list(
                await session.scalars(
                    select(AgentSpecModel)
                    .where(
                        AgentSpecModel.scope_goal_id == goal_id,
                        AgentSpecModel.status != "REVOKED",
                    )
                    .order_by(AgentSpecModel.created_at.asc())
                )
            )
            for spec in specs:
                constraints = spec.constraints if isinstance(spec.constraints, dict) else {}
                role = str(constraints.get("hive_role") or constraints.get("role") or "executor")
                display = _AGENT_ROLE_LABELS.get(role) or role
                short_id = str(spec.id).replace("-", "")[:8]
                activity = "ready" if (goal_active and spec.status == "ACTIVE") else "idle"
                if terminal and goal_status == "ACHIEVED":
                    activity = "done"
                agents.append(
                    {
                        "id": str(spec.id),
                        "name": f"{display} · {short_id}",
                        "role": role,
                        "role_label": display,
                        "kind": "spec",
                        "activity": activity,
                        "detail": None,
                        "is_main": False,
                        "spec_status": spec.status,
                    }
                )
        except Exception:
            pass

        return agents

    # ------------------------------------------------------------------
    # Execution stage detection (unchanged)
    # ------------------------------------------------------------------

    @staticmethod
    async def _project_execution_stage(
        session: AsyncSession, goal_id: uuid.UUID
    ) -> dict[str, str]:
        """Project execution stage from underlying objects."""
        deployment = await session.scalar(
            select(DeploymentModel)
            .join(
                ReleaseCandidateModel,
                DeploymentModel.release_candidate_id == ReleaseCandidateModel.id,
            )
            .join(AppBuildModel, ReleaseCandidateModel.app_build_id == AppBuildModel.id)
            .join(
                WorkspaceSnapshotModel,
                AppBuildModel.workspace_snapshot_id == WorkspaceSnapshotModel.id,
            )
            .join(
                GenerationRunModel,
                WorkspaceSnapshotModel.generation_run_id == GenerationRunModel.id,
            )
            .join(
                GenerationPlanModel,
                GenerationRunModel.plan_id == GenerationPlanModel.id,
            )
            .join(
                RequirementRevisionModel,
                GenerationPlanModel.requirement_revision_id
                == RequirementRevisionModel.id,
            )
            .where(
                RequirementRevisionModel.goal_id == goal_id,
                DeploymentModel.status == "SUCCEEDED",
            )
            .order_by(DeploymentModel.created_at.desc())
            .limit(1)
        )
        if deployment is not None:
            return {"stage": "DEPLOYED", "object_id": str(deployment.id)}

        build = await session.scalar(
            select(AppBuildModel)
            .join(
                WorkspaceSnapshotModel,
                AppBuildModel.workspace_snapshot_id == WorkspaceSnapshotModel.id,
            )
            .join(
                GenerationRunModel,
                WorkspaceSnapshotModel.generation_run_id == GenerationRunModel.id,
            )
            .join(
                GenerationPlanModel,
                GenerationRunModel.plan_id == GenerationPlanModel.id,
            )
            .join(
                RequirementRevisionModel,
                GenerationPlanModel.requirement_revision_id
                == RequirementRevisionModel.id,
            )
            .where(
                RequirementRevisionModel.goal_id == goal_id,
                AppBuildModel.status == "PASSED",
            )
            .order_by(AppBuildModel.created_at.desc())
            .limit(1)
        )
        if build is not None:
            return {"stage": "BUILD_PASSED", "object_id": str(build.id)}

        snapshot = await session.scalar(
            select(WorkspaceSnapshotModel)
            .join(
                GenerationRunModel,
                WorkspaceSnapshotModel.generation_run_id == GenerationRunModel.id,
            )
            .join(
                GenerationPlanModel,
                GenerationRunModel.plan_id == GenerationPlanModel.id,
            )
            .join(
                RequirementRevisionModel,
                GenerationPlanModel.requirement_revision_id
                == RequirementRevisionModel.id,
            )
            .where(RequirementRevisionModel.goal_id == goal_id)
            .order_by(WorkspaceSnapshotModel.created_at.desc())
            .limit(1)
        )
        if snapshot is not None:
            return {"stage": "SNAPSHOT_READY", "object_id": str(snapshot.id)}

        gen_run = await session.scalar(
            select(GenerationRunModel)
            .join(
                GenerationPlanModel,
                GenerationRunModel.plan_id == GenerationPlanModel.id,
            )
            .join(
                RequirementRevisionModel,
                GenerationPlanModel.requirement_revision_id
                == RequirementRevisionModel.id,
            )
            .where(RequirementRevisionModel.goal_id == goal_id)
            .order_by(GenerationRunModel.created_at.desc())
            .limit(1)
        )
        if gen_run is not None:
            return {"stage": "GENERATING", "object_id": str(gen_run.id)}

        resolution = await session.scalar(
            select(CapabilityResolutionPlanModel)
            .join(
                RequirementRevisionModel,
                CapabilityResolutionPlanModel.requirement_revision_id
                == RequirementRevisionModel.id,
            )
            .where(
                RequirementRevisionModel.goal_id == goal_id,
                CapabilityResolutionPlanModel.status == "SATISFIED",
            )
            .order_by(CapabilityResolutionPlanModel.created_at.desc())
            .limit(1)
        )
        if resolution is not None:
            return {"stage": "RESOLVED", "object_id": str(resolution.id)}

        decision = await session.scalar(
            select(HypothesisDecisionModel)
            .join(
                ProductHypothesisModel,
                HypothesisDecisionModel.selected_hypothesis_id
                == ProductHypothesisModel.id,
            )
            .join(
                DiscoveryRoundModel,
                ProductHypothesisModel.round_id == DiscoveryRoundModel.id,
            )
            .where(
                DiscoveryRoundModel.goal_id == goal_id,
                HypothesisDecisionModel.decision == "SELECT",
            )
            .order_by(HypothesisDecisionModel.created_at.desc())
            .limit(1)
        )
        if decision is not None:
            return {"stage": "DECIDED", "object_id": str(decision.id)}

        discovery = await session.scalar(
            select(DiscoveryRoundModel)
            .where(DiscoveryRoundModel.goal_id == goal_id)
            .order_by(DiscoveryRoundModel.created_at.desc())
            .limit(1)
        )
        if discovery is not None:
            return {"stage": "DISCOVERING", "object_id": str(discovery.id)}

        execution_event = await session.scalar(
            select(OutboxEventModel).where(
                OutboxEventModel.aggregate_type == "goal",
                OutboxEventModel.aggregate_id == goal_id,
                OutboxEventModel.event_type == "GoalExecutionRequested",
            )
        )
        if execution_event is not None:
            return {"stage": "QUEUED", "object_id": str(execution_event.id)}

        return {"stage": "NOT_STARTED", "object_id": ""}

    # ------------------------------------------------------------------
    # Conversation helpers
    # ------------------------------------------------------------------

    async def _conversation(
        self, session: AsyncSession, project_id: uuid.UUID
    ) -> ConversationModel:
        conversation = await session.scalar(
            select(ConversationModel).where(ConversationModel.app_project_id == project_id)
        )
        if conversation is None:
            raise DomainError(ErrorCode.NOT_FOUND, "app conversation not found")
        return conversation

    async def _next_ordinal(self, session: AsyncSession, conversation_id: uuid.UUID) -> int:
        value = await session.scalar(
            select(func.max(ConversationMessageModel.ordinal)).where(
                ConversationMessageModel.conversation_id == conversation_id
            )
        )
        return (value or 0) + 1

    async def _persist_message_pair(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
        ordinal: int,
        user_message: str,
        actor: str,
        assistant_content: str,
        assistant_type: str,
        assistant_metadata: dict[str, Any] | None = None,
    ) -> ConversationMessageModel:
        """Persist a USER+ASSISTANT message pair, return the user message model."""
        user_msg = ConversationMessageModel(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            ordinal=ordinal,
            role="USER",
            message_type="GUIDANCE",
            content=user_message,
            metadata_json={},
            created_by=actor,
        )
        session.add(user_msg)
        await session.flush()
        session.add(
            ConversationMessageModel(
                id=uuid.uuid4(),
                conversation_id=conversation_id,
                ordinal=ordinal + 1,
                role="ASSISTANT",
                message_type=assistant_type,
                content=assistant_content,
                metadata_json=assistant_metadata or {},
                created_by="regent-core",
            )
        )
        return user_msg

    async def _persist_command(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
        project_id: uuid.UUID,
        user_message_id: uuid.UUID,
        command_type: str,
        interpretation: GuidanceInterpretation,
        model: str,
        actor: str,
        resulting_goal_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        command_id = uuid.uuid4()
        payload = interpretation.model_dump(mode="json")
        session.add(
            ConversationCommandModel(
                id=command_id,
                app_project_id=project_id,
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                command_type=command_type,
                status="APPLIED",
                interpretation_json=payload,
                interpretation_hash=canonical_hash(payload),
                resulting_goal_id=resulting_goal_id,
                model_ref=model,
                created_by=actor,
            )
        )
        return command_id

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _handle_query(
        self,
        project_id: uuid.UUID,
        message: str,
        actor: str,
        interpretation: GuidanceInterpretation,
        model: str,
    ) -> GuidanceReceipt:
        context = await self._context(project_id)
        goal = context["goal"]
        stage_info = goal.get("execution_stage", {})
        stage = stage_info.get("stage", goal["status"]) if isinstance(stage_info, dict) else goal["status"]
        stage_label = _STAGE_LABELS.get(stage, stage)
        work = context.get("work_states", {})
        pending = context.get("pending_human_tasks", [])
        corrections = context.get("active_corrections", [])

        parts = [interpretation.summary]
        parts.append(f"\n当前状态: {goal['status']} | 阶段: {stage_label}")
        if work:
            parts.append(f"工作项: {work}")
        if pending:
            parts.append(f"待处理任务: {len(pending)} 个")
            for t in pending[:3]:
                parts.append(f"  - [{t['task_type']}] {t['prompt'][:80]}")
        if corrections:
            parts.append(f"已应用修正: {len(corrections)} 条")
            for c in corrections[-3:]:
                parts.append(f"  - [{c.get('target', '?')}] {c.get('detail', '')[:80]}")
        if context.get("preview"):
            pv = context["preview"]
            if pv.get("endpoint"):
                parts.append(f"预览地址: {pv['endpoint']}")

        response = "\n".join(parts)
        return await self._persist_simple(
            project_id, message, actor, interpretation, model, response
        )

    async def _handle_continue(
        self,
        project_id: uuid.UUID,
        message: str,
        actor: str,
        interpretation: GuidanceInterpretation,
        model: str,
    ) -> GuidanceReceipt:
        context = await self._context(project_id)
        goal_status = str(context["goal"]["status"])
        stage_info = context["goal"].get("execution_stage", {"stage": goal_status})
        stage = (
            stage_info.get("stage", goal_status)
            if isinstance(stage_info, dict)
            else goal_status
        )
        should_start = goal_status in {"DRAFT", "READY"} or (
            goal_status == "ACTIVE" and stage == "FAILED"
        )

        if goal_status == "READY":
            response = "Core 已接受继续请求并开始执行。"
        elif goal_status == "ACTIVE":
            response = (
                "Core 正在安全重试。" if should_start else f"Core 正在执行。当前阶段: {_STAGE_LABELS.get(stage, stage)}。"
            )
        elif goal_status == "PAUSED":
            response = "目标已暂停。发送“恢复”或“resume”以继续执行。"
        else:
            response = f"当前 Goal 状态为 {goal_status}, 当前不可直接继续。"
        receipt = await self._persist_simple(
            project_id, message, actor, interpretation, model, response
        )
        if should_start:
            await GoalExecutionService(self._sessions).start(
                uuid.UUID(str(context["goal"]["id"])),
                actor=actor,
                idempotency_key=f"guidance:{receipt.command_id}",
            )
        return receipt

    async def _handle_pause(
        self,
        project_id: uuid.UUID,
        message: str,
        actor: str,
        interpretation: GuidanceInterpretation,
        model: str,
    ) -> GuidanceReceipt:
        context = await self._context(project_id)
        goal_id = uuid.UUID(str(context["goal"]["id"]))
        goal_status = str(context["goal"]["status"])
        goal_version = int(context["goal"].get("version", 0))

        if goal_status != "ACTIVE":
            response = f"目标当前状态为 {goal_status}，只有执行中(ACTIVE)的目标可以暂停。"
            return await self._persist_simple(
                project_id, message, actor, interpretation, model, response
            )

        try:
            await TransitionService(self._sessions).transition_goal(
                TransitionContext(
                    aggregate_id=goal_id,
                    expected_version=goal_version,
                    actor=actor,
                    correlation_id=uuid.uuid4(),
                ),
                GoalCommand.PAUSE,
            )
            response = "已暂停执行。你可以发送修正指令，或发送“恢复”继续。"
        except DomainError as exc:
            response = f"暂停失败: {exc.message}"

        command_id = uuid.uuid4()
        async with self._sessions() as session, session.begin():
            conversation = await self._conversation(session, project_id)
            ordinal = await self._next_ordinal(session, conversation.id)
            user_msg = await self._persist_message_pair(
                session, conversation.id, ordinal, message, actor,
                response, "PAUSE_RESULT",
                {"command_id": str(command_id), "goal_id": str(goal_id)},
            )
            cid = await self._persist_command(
                session, conversation.id, project_id, user_msg.id,
                "PAUSE", interpretation, model, actor,
            )
            command_id = cid
        return GuidanceReceipt(command_id, "PAUSE", None, False, response)

    async def _handle_resume(
        self,
        project_id: uuid.UUID,
        message: str,
        actor: str,
        interpretation: GuidanceInterpretation,
        model: str,
    ) -> GuidanceReceipt:
        context = await self._context(project_id)
        goal_id = uuid.UUID(str(context["goal"]["id"]))
        goal_status = str(context["goal"]["status"])
        goal_version = int(context["goal"].get("version", 0))

        if goal_status != "PAUSED":
            response = f"目标当前状态为 {goal_status}，只有已暂停(PAUSED)的目标可以恢复。"
            return await self._persist_simple(
                project_id, message, actor, interpretation, model, response
            )

        try:
            await TransitionService(self._sessions).transition_goal(
                TransitionContext(
                    aggregate_id=goal_id,
                    expected_version=goal_version,
                    actor=actor,
                    correlation_id=uuid.uuid4(),
                ),
                GoalCommand.RESUME,
            )
            # Try to re-trigger execution; if it fails (e.g. already ACTIVE), that's OK
            # — the goal is back to ACTIVE and pending events will be processed.
            try:
                await GoalExecutionService(self._sessions).start(
                    goal_id,
                    actor=actor,
                    idempotency_key=f"guidance-resume:{uuid.uuid4()}",
                )
            except DomainError:
                pass  # Goal is ACTIVE, worker will pick up pending events
            response = "已恢复执行。Core 将继续从当前阶段推进。"
        except DomainError as exc:
            response = f"恢复失败: {exc.message}"

        command_id = uuid.uuid4()
        async with self._sessions() as session, session.begin():
            conversation = await self._conversation(session, project_id)
            ordinal = await self._next_ordinal(session, conversation.id)
            user_msg = await self._persist_message_pair(
                session, conversation.id, ordinal, message, actor,
                response, "RESUME_RESULT",
                {"command_id": str(command_id), "goal_id": str(goal_id)},
            )
            cid = await self._persist_command(
                session, conversation.id, project_id, user_msg.id,
                "RESUME", interpretation, model, actor,
            )
            command_id = cid
        return GuidanceReceipt(command_id, "RESUME", None, False, response)

    async def _handle_correct(
        self,
        project_id: uuid.UUID,
        message: str,
        actor: str,
        interpretation: GuidanceInterpretation,
        model: str,
    ) -> GuidanceReceipt:
        """Lightweight mid-execution correction — stores in goal metadata, no new revision."""
        context = await self._context(project_id)
        goal_id = uuid.UUID(str(context["goal"]["id"]))
        goal_status = str(context["goal"]["status"])

        if goal_status not in ("DRAFT", "ACTIVE", "PAUSED", "READY", "WAITING_HUMAN"):
            response = f"目标当前状态为 {goal_status}，无法应用修正。"
            return await self._persist_simple(
                project_id, message, actor, interpretation, model, response
            )

        target = interpretation.correction_target or "other"
        detail = interpretation.correction_detail or message

        command_id = uuid.uuid4()
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, "goal not found")
            metadata = dict(goal.metadata_json or {})
            corrections = list(metadata.get("active_corrections", []))
            corrections.append({
                "target": target,
                "detail": detail,
                "original_message": message,
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "actor": actor,
                "summary": interpretation.summary,
            })
            metadata["active_corrections"] = corrections
            latest_spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == goal_id)
                .order_by(GoalSpecModel.version.desc())
                .with_for_update()
            )
            if latest_spec is None:
                raise DomainError(ErrorCode.NOT_FOUND, "goal spec not found")

            constraints = dict(latest_spec.explicit_constraints or {})
            if interpretation.explicit_constraints:
                constraints.update(interpretation.explicit_constraints)
            inferences = dict(latest_spec.system_inferences or {})
            for key, value in {
                "target_users": interpretation.target_users,
                "problem": interpretation.problem,
                "first_deliverable": interpretation.first_deliverable,
            }.items():
                if value:
                    inferences[key] = value
            progressive = list(inferences.get("progressive_corrections", []))
            progressive.append({"target": target, "detail": detail, "actor": actor})
            inferences["progressive_corrections"] = progressive
            unknowns = (
                [{"question": item, "blocking": False} for item in interpretation.unknowns]
                if interpretation.unknowns is not None
                else list(latest_spec.unknowns or [])
            )
            success_criteria = (
                interpretation.success_criteria or dict(latest_spec.success_criteria or {})
            )
            spec_content = {
                "explicit_constraints": constraints,
                "system_inferences": inferences,
                "unknowns": unknowns,
                "success_criteria": success_criteria,
                "source_refs": [
                    *list(latest_spec.source_refs or []),
                    {"type": "guidance_command", "id": str(command_id)},
                ],
            }
            latest_spec.status = "SUPERSEDED"
            next_spec = GoalSpecModel(
                id=uuid.uuid4(),
                goal_id=goal_id,
                version=latest_spec.version + 1,
                status="FROZEN" if goal.status != "DRAFT" else "DRAFT",
                content_hash=canonical_hash(spec_content),
                confirmed_by="regent-core:progressive-snapshot" if goal.status != "DRAFT" else None,
                confirmed_at=datetime.now(UTC) if goal.status != "DRAFT" else None,
                **spec_content,
            )
            session.add(next_spec)
            metadata["goal_clarity_state"] = "EXPLORING" if unknowns else "CLARIFIED"
            metadata["latest_goal_spec_version"] = next_spec.version
            goal.metadata_json = metadata
            flag_modified(goal, "metadata_json")

            conversation = await self._conversation(session, project_id)
            ordinal = await self._next_ordinal(session, conversation.id)
            user_msg = await self._persist_message_pair(
                session, conversation.id, ordinal, message, actor,
                f"已记录修正: [{target}] {detail}\n修正将在下一个执行步骤中生效。",
                "CORRECTION_APPLIED",
                {
                    "command_id": str(command_id),
                    "goal_id": str(goal_id),
                    "correction_target": target,
                    "correction_detail": detail,
                    "total_corrections": len(corrections),
                },
            )
            cid = await self._persist_command(
                session, conversation.id, project_id, user_msg.id,
                "CORRECT", interpretation, model, actor,
            )
            command_id = cid

        response = f"已记录修正: [{target}] {detail}"
        return GuidanceReceipt(command_id, "CORRECT", None, False, response)

    async def _handle_approve(
        self,
        project_id: uuid.UUID,
        message: str,
        actor: str,
        interpretation: GuidanceInterpretation,
        model: str,
    ) -> GuidanceReceipt:
        """Approve pending human tasks for this goal."""
        context = await self._context(project_id)
        goal_id = uuid.UUID(str(context["goal"]["id"]))
        pending = context.get("pending_human_tasks", [])

        if not pending:
            # Recovery: RELEASE_APPROVAL was COMPLETED (e.g. chat path before fix)
            # but ReleaseApprovalCompleted never resumed the pipeline.
            resumed = await HumanTaskService(self._sessions).reemit_stuck_release_approval(
                goal_id, assigned_to=actor
            )
            if resumed is not None:
                response = (
                    f"已重新触发批准后续: {resumed['task_type']}\n"
                    f"任务 {resumed['task_id']} 将继续部署。"
                )
                return await self._persist_simple(
                    project_id, message, actor, interpretation, model, response,
                    assistant_type="APPROVE_RESULT",
                    assistant_metadata={
                        "task_id": resumed["task_id"],
                        "task_type": resumed["task_type"],
                        "approved": True,
                        "recovered": True,
                    },
                )
            # If goal is WAITING_HUMAN but no HumanTask records, try transitioning
            goal_status = str(context["goal"]["status"])
            if goal_status == "WAITING_HUMAN":
                goal_version = int(context["goal"].get("version", 0))
                try:
                    await TransitionService(self._sessions).transition_goal(
                        TransitionContext(
                            aggregate_id=goal_id,
                            expected_version=goal_version,
                            actor=actor,
                            correlation_id=uuid.uuid4(),
                        ),
                        GoalCommand.HUMAN_RESOLVED,
                    )
                    response = "已批准。目标恢复执行。"
                except DomainError as exc:
                    response = f"批准失败: {exc.message}"
            else:
                response = "当前没有待批准的任务。"
            return await self._persist_simple(
                project_id, message, actor, interpretation, model, response
            )

        # Complete the first pending human task
        task_id = uuid.UUID(pending[0]["id"])
        await HumanTaskService(self._sessions).complete(
            task_id,
            assigned_to=actor,
            response={"approved": True, "decision": "APPROVE", "message": message},
        )

        # If goal is WAITING_HUMAN, transition to ACTIVE
        goal_status = str(context["goal"]["status"])
        if goal_status == "WAITING_HUMAN":
            goal_version = int(context["goal"].get("version", 0))
            try:
                await TransitionService(self._sessions).transition_goal(
                    TransitionContext(
                        aggregate_id=goal_id,
                        expected_version=goal_version,
                        actor=actor,
                        correlation_id=uuid.uuid4(),
                    ),
                    GoalCommand.HUMAN_RESOLVED,
                )
            except DomainError:
                pass  # Task completed even if transition fails

        response = f"已批准任务: {pending[0]['task_type']}\n{pending[0]['prompt'][:100]}"

        command_id = uuid.uuid4()
        async with self._sessions() as session, session.begin():
            conversation = await self._conversation(session, project_id)
            ordinal = await self._next_ordinal(session, conversation.id)
            user_msg = await self._persist_message_pair(
                session, conversation.id, ordinal, message, actor,
                response, "APPROVE_RESULT",
                {
                    "command_id": str(command_id),
                    "task_id": str(task_id),
                    "task_type": pending[0].get("task_type"),
                    "approved": True,
                },
            )
            cid = await self._persist_command(
                session, conversation.id, project_id, user_msg.id,
                "APPROVE", interpretation, model, actor,
            )
            command_id = cid
        return GuidanceReceipt(command_id, "APPROVE", None, False, response)

    async def _handle_reject(
        self,
        project_id: uuid.UUID,
        message: str,
        actor: str,
        interpretation: GuidanceInterpretation,
        model: str,
    ) -> GuidanceReceipt:
        """Reject pending human tasks, trigger revision."""
        context = await self._context(project_id)
        goal_id = uuid.UUID(str(context["goal"]["id"]))
        pending = context.get("pending_human_tasks", [])
        reason = interpretation.rejection_reason or message

        task_id: uuid.UUID | None = None
        if pending:
            task_id = uuid.UUID(pending[0]["id"])
            await HumanTaskService(self._sessions).complete(
                task_id,
                assigned_to=actor,
                response={
                    "approved": False,
                    "decision": "REJECT",
                    "rejection_reason": reason,
                    "message": message,
                },
            )

        goal_status = str(context["goal"]["status"])
        if goal_status == "WAITING_HUMAN":
            goal_version = int(context["goal"].get("version", 0))
            try:
                await TransitionService(self._sessions).transition_goal(
                    TransitionContext(
                        aggregate_id=goal_id,
                        expected_version=goal_version,
                        actor=actor,
                        correlation_id=uuid.uuid4(),
                    ),
                    GoalCommand.HUMAN_BLOCKED,
                )
            except DomainError:
                pass

        response = f"已拒绝。原因: {reason}\nCore 将根据反馈重新规划。"

        command_id = uuid.uuid4()
        async with self._sessions() as session, session.begin():
            conversation = await self._conversation(session, project_id)
            ordinal = await self._next_ordinal(session, conversation.id)
            user_msg = await self._persist_message_pair(
                session, conversation.id, ordinal, message, actor,
                response, "REJECT_RESULT",
                {
                    "command_id": str(command_id),
                    "goal_id": str(goal_id),
                    "reason": reason,
                    "task_id": str(task_id) if task_id else None,
                    "approved": False,
                },
            )
            cid = await self._persist_command(
                session, conversation.id, project_id, user_msg.id,
                "REJECT", interpretation, model, actor,
            )
            command_id = cid
        return GuidanceReceipt(command_id, "REJECT", None, False, response)

    # ------------------------------------------------------------------
    # URL-based research resume (unchanged)
    # ------------------------------------------------------------------

    async def _maybe_resume_research_more(
        self, project_id: uuid.UUID, message: str, actor: str
    ) -> GuidanceReceipt | None:
        """Optional human URL override when auto capability recovery is exhausted or blocked."""
        urls = extract_urls_from_text(message)
        if not urls:
            return None
        command_id = uuid.uuid4()
        async with self._sessions() as session, session.begin():
            project = await session.get(AppProjectModel, project_id)
            goal = await session.scalar(
                select(GoalModel)
                .where(GoalModel.app_project_id == project_id)
                .order_by(GoalModel.created_at.desc())
                .with_for_update()
            )
            if project is None or goal is None or goal.status != "ACTIVE":
                return None
            metadata = dict(goal.metadata_json or {})
            stage = str(metadata.get("execution_stage") or "")
            awaiting = bool(metadata.get("awaiting_authorized_sources"))
            latest_decision = await session.scalar(
                select(HypothesisDecisionModel.decision)
                .where(
                    HypothesisDecisionModel.round_id.in_(
                        select(DiscoveryRoundModel.id).where(
                            DiscoveryRoundModel.goal_id == goal.id
                        )
                    )
                )
                .order_by(HypothesisDecisionModel.created_at.desc())
                .limit(1)
            )
            if (
                stage != "RESEARCH_MORE"
                and not awaiting
                and latest_decision != "RESEARCH_MORE"
            ):
                return None
            spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == goal.id)
                .order_by(GoalSpecModel.version.desc())
                .limit(1)
            )
            if spec is None or spec.status != "FROZEN":
                return None

            existing = list(metadata.get("authorized_source_urls") or [])
            merged = list(
                dict.fromkeys([*[str(item) for item in existing if item], *urls])
            )
            metadata["authorized_source_urls"] = merged
            metadata["awaiting_authorized_sources"] = False
            metadata["execution_stage"] = "DISCOVERING"
            goal.metadata_json = metadata
            flag_modified(goal, "metadata_json")

            conversation = await self._conversation(session, project_id)
            ordinal = await self._next_ordinal(session, conversation.id)
            session.add(
                ConversationMessageModel(
                    id=uuid.uuid4(),
                    conversation_id=conversation.id,
                    ordinal=ordinal,
                    role="USER",
                    message_type="GUIDANCE",
                    content=message,
                    metadata_json={"authorized_source_urls": urls},
                    created_by=actor,
                )
            )
            response = (
                "已记录授权来源 URL, 并重新启动产品发现。"
                f" 将抓取: {', '.join(merged[:5])}"
                + ("..." if len(merged) > 5 else "")
            )
            session.add(
                ConversationMessageModel(
                    id=uuid.uuid4(),
                    conversation_id=conversation.id,
                    ordinal=ordinal + 1,
                    role="ASSISTANT",
                    message_type="RESEARCH_MORE_SOURCES_ACCEPTED",
                    content=response,
                    metadata_json={
                        "goal_id": str(goal.id),
                        "authorized_source_urls": merged,
                    },
                    created_by="regent-core",
                )
            )

            idempotency_key = make_idempotency_key(
                "discovery-resume", goal.id, str(command_id)
            )
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
            snapshot = {
                "goal_id": str(goal.id),
                "goal_version": goal.version,
                "spec_version": spec.version,
                "constraints": spec.explicit_constraints,
                "success_criteria": spec.success_criteria,
                "authorized_source_urls": merged,
                "resume_of": "RESEARCH_MORE",
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
                            "resume_of": "RESEARCH_MORE",
                        },
                        idempotency_key=idempotency_key,
                        correlation_id=goal.correlation_id,
                    )
                )
            )
            session.add(
                ConversationMessageModel(
                    id=uuid.uuid4(),
                    conversation_id=conversation.id,
                    ordinal=ordinal + 2,
                    role="EVENT",
                    message_type="DISCOVERY_ROUND_REQUESTED",
                    content=(
                        f"Core has created discovery round {next_round} with authorized "
                        "source URLs and is collecting evidence."
                    ),
                    metadata_json={
                        "goal_id": str(goal.id),
                        "discovery_round_id": str(discovery_round.id),
                        "round": next_round,
                    },
                    created_by="regent-core",
                )
            )
        return GuidanceReceipt(
            command_id=command_id,
            command_type="PROVIDE_SOURCES",
            resulting_goal_id=None,
            requires_confirmation=False,
            response=response,
        )

    # ------------------------------------------------------------------
    # Simple persist (for QUERY/CONTINUE/failed transitions)
    # ------------------------------------------------------------------

    async def _persist_simple(
        self,
        project_id: uuid.UUID,
        message: str,
        actor: str,
        interpretation: GuidanceInterpretation,
        model: str,
        response: str,
        *,
        assistant_type: str | None = None,
        assistant_metadata: dict[str, Any] | None = None,
    ) -> GuidanceReceipt:
        command_id = uuid.uuid4()
        async with self._sessions() as session, session.begin():
            conversation = await self._conversation(session, project_id)
            ordinal = await self._next_ordinal(session, conversation.id)
            user_message = ConversationMessageModel(
                id=uuid.uuid4(),
                conversation_id=conversation.id,
                ordinal=ordinal,
                role="USER",
                message_type="GUIDANCE",
                content=message,
                metadata_json={},
                created_by=actor,
            )
            session.add(user_message)
            await session.flush()
            payload = interpretation.model_dump(mode="json")
            msg_type = assistant_type or f"{interpretation.command_type}_RESULT"
            session.add_all(
                (
                    ConversationCommandModel(
                        id=command_id,
                        app_project_id=project_id,
                        conversation_id=conversation.id,
                        user_message_id=user_message.id,
                        command_type=interpretation.command_type,
                        status="APPLIED",
                        interpretation_json=payload,
                        interpretation_hash=canonical_hash(payload),
                        resulting_goal_id=None,
                        model_ref=model,
                        created_by=actor,
                    ),
                    ConversationMessageModel(
                        id=uuid.uuid4(),
                        conversation_id=conversation.id,
                        ordinal=ordinal + 1,
                        role="ASSISTANT",
                        message_type=msg_type,
                        content=response,
                        metadata_json=assistant_metadata or {"command_id": str(command_id)},
                        created_by="regent-core",
                    ),
                )
            )
        return GuidanceReceipt(command_id, interpretation.command_type, None, False, response)

    # ------------------------------------------------------------------
    # MODIFY handler (creates new goal revision — unchanged logic)
    # ------------------------------------------------------------------

    async def _handle_modify(
        self,
        project_id: uuid.UUID,
        message: str,
        actor: str,
        interpretation: GuidanceInterpretation,
        model: str,
    ) -> GuidanceReceipt:
        command_id, goal_id, spec_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with self._sessions() as session, session.begin():
            project = await session.get(AppProjectModel, project_id, with_for_update=True)
            if project is None:
                raise DomainError(ErrorCode.NOT_FOUND, "app project not found")
            previous_goal = await session.scalar(
                select(GoalModel)
                .where(GoalModel.app_project_id == project_id)
                .order_by(GoalModel.created_at.desc())
                .with_for_update()
            )
            if previous_goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, "previous goal not found")
            previous_spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == previous_goal.id)
                .order_by(GoalSpecModel.version.desc())
            )
            if previous_spec is None:
                raise DomainError(ErrorCode.NOT_FOUND, "previous goal spec not found")
            conversation = await self._conversation(session, project_id)
            ordinal = await self._next_ordinal(session, conversation.id)
            user_message = ConversationMessageModel(
                id=uuid.uuid4(),
                conversation_id=conversation.id,
                ordinal=ordinal,
                role="USER",
                message_type="GUIDANCE",
                content=message,
                metadata_json={},
                created_by=actor,
            )
            session.add(user_message)
            await session.flush()
            metadata = previous_goal.metadata_json
            constraints = interpretation.explicit_constraints or previous_spec.explicit_constraints
            if interpretation.non_goals is not None:
                constraints = {**constraints, "non_goals": interpretation.non_goals}
            unknowns = (
                [{"question": item, "blocking": False} for item in interpretation.unknowns]
                if interpretation.unknowns is not None
                else previous_spec.unknowns
            )
            spec_content: dict[str, Any] = {
                "explicit_constraints": constraints,
                "system_inferences": {
                    "target_users": interpretation.target_users or metadata.get("target_users"),
                    "problem": interpretation.problem or metadata.get("problem"),
                    "first_deliverable": interpretation.first_deliverable
                    or metadata.get("first_deliverable"),
                },
                "unknowns": unknowns,
                "success_criteria": interpretation.success_criteria
                or previous_spec.success_criteria,
                "source_refs": [{"type": "conversation_message", "id": str(user_message.id)}],
            }
            goal = GoalModel(
                id=goal_id,
                app_project_id=project_id,
                original_input=interpretation.objective or message,
                status="DRAFT",
                version=0,
                created_by=actor,
                correlation_id=uuid.uuid4(),
                metadata_json={
                    "target_users": interpretation.target_users or metadata.get("target_users"),
                    "problem": interpretation.problem or metadata.get("problem"),
                    "first_deliverable": interpretation.first_deliverable
                    or metadata.get("first_deliverable"),
                    "predecessor_goal_id": str(previous_goal.id),
                    "guidance_model": model,
                },
            )
            spec = GoalSpecModel(
                id=spec_id,
                goal_id=goal_id,
                version=1,
                status="DRAFT",
                content_hash=canonical_hash(spec_content),
                **spec_content,
            )
            payload = interpretation.model_dump(mode="json")
            command = ConversationCommandModel(
                id=command_id,
                app_project_id=project_id,
                conversation_id=conversation.id,
                user_message_id=user_message.id,
                command_type="MODIFY",
                status="APPLIED",
                interpretation_json=payload,
                interpretation_hash=canonical_hash(payload),
                resulting_goal_id=goal_id,
                model_ref=model,
                created_by=actor,
            )
            session.add_all((goal, spec))
            await session.flush()
            understanding = {
                "app_name": project.name,
                "product_intent": interpretation.product_intent or project.product_intent,
                "target_users": spec_content["system_inferences"]["target_users"],
                "problem": spec_content["system_inferences"]["problem"],
                "first_deliverable": spec_content["system_inferences"]["first_deliverable"],
                "success_criteria": spec_content["success_criteria"],
                "explicit_constraints": constraints,
                "non_goals": constraints.get("non_goals", []),
                "unknowns": interpretation.unknowns or [],
            }
            session.add_all(
                (
                    command,
                    ConversationMessageModel(
                        id=uuid.uuid4(),
                        conversation_id=conversation.id,
                        ordinal=ordinal + 1,
                        role="ASSISTANT",
                        message_type="GOAL_UNDERSTANDING_READY",
                        content="我已根据你的指导形成新的目标版本，并将按当前理解继续探索。",
                        metadata_json={
                            "app_project_id": str(project_id),
                            "goal_id": str(goal_id),
                            "goal_spec_id": str(spec_id),
                            "goal_spec_hash": spec.content_hash,
                            "understanding": understanding,
                            "command_id": str(command_id),
                        },
                        created_by="regent-core",
                    ),
                )
            )
        if previous_goal.status not in {
            "ACHIEVED", "EXHAUSTED", "FAILED", "CANCELLED"
        }:
            await TransitionService(self._sessions).transition_goal(
                TransitionContext(
                    aggregate_id=previous_goal.id,
                    expected_version=previous_goal.version,
                    actor=actor,
                    correlation_id=previous_goal.correlation_id,
                ),
                GoalCommand.CANCEL,
            )
        await GoalExecutionService(self._sessions).start(
            goal_id,
            actor=actor,
            idempotency_key=f"guidance-modify:{command_id}",
        )
        return GuidanceReceipt(command_id, "MODIFY", goal_id, False, interpretation.summary)
