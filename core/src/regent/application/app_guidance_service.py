import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from regent.application.evidence_policy import extract_urls_from_text
from regent.model.chat import ToolSpec
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
    "product": "产品",
    "tech": "技术",
    "test": "测试",
    "ux": "体验",
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
        "SELECT_OPTION",
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
            "REJECT: reject a pending gate result, trigger revision. "
            "SELECT_OPTION: user chose a pending fork option (set selected_option_id)."
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
    feasibility_verdict: Literal[
        "FEASIBLE", "REVISION_REQUIRED", "NOT_FEASIBLE"
    ] | None = None
    feasibility_reasons: list[str] | None = None
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
    # SELECT_OPTION fields (run-think-learn L2)
    selected_option_id: str | None = Field(
        default=None,
        description="Id of pending fork_options entry the user selected",
    )
    # CD-4.1: bounded chaining — optional immediate follow-up command executed
    # within the same guide() call (e.g. QUERY to clarify state, then CONTINUE to
    # resume execution). See AppGuidanceService._MAX_GUIDANCE_STEPS.
    follow_up_command: Literal[
        "QUERY",
        "MODIFY",
        "CONTINUE",
        "PAUSE",
        "RESUME",
        "CORRECT",
        "APPROVE",
        "REJECT",
        "SELECT_OPTION",
    ] | None = Field(
        default=None,
        description=(
            "Optional immediate follow-up command to run right after this one "
            "resolves, e.g. command_type=QUERY with follow_up_command=CONTINUE when "
            "the user's message clearly implies 'tell me where we are, then proceed'. "
            "Only set when a single user message unambiguously implies two sequential "
            "steps; leave unset otherwise."
        ),
    )
    follow_up_summary: str | None = Field(
        default=None,
        description="Summary/message to use for the follow_up_command dispatch.",
    )


@dataclass(frozen=True, slots=True)
class GuidanceReceipt:
    command_id: uuid.UUID
    command_type: str
    resulting_goal_id: uuid.UUID | None
    requires_confirmation: bool
    response: str


class AppGuidanceService:
    # CD-4.1: bounded multi-step loop — guide() may dispatch at most this many
    # commands (main + chained follow-ups) per call. Keeps chaining safe/finite
    # without requiring a full chat+tools provider rewrite.
    _MAX_GUIDANCE_STEPS = 5

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: ModelProvider,
    ) -> None:
        self._sessions = sessions
        self._provider = provider

    # ------------------------------------------------------------------
    # Internal tool table — the 8 _handle_* capabilities, addressable by name.
    # Not yet wired into a chat+tools provider loop; exposed so guide()'s
    # dispatch is table-driven and so future provider tool-calling (or tests)
    # can introspect available capabilities without re-deriving them.
    # ------------------------------------------------------------------

    def _handler_table(
        self,
    ) -> dict[
        str,
        Callable[
            [uuid.UUID, str, str, GuidanceInterpretation, str],
            Awaitable[GuidanceReceipt],
        ],
    ]:
        return {
            "QUERY": self._handle_query,
            "CONTINUE": self._handle_continue,
            "MODIFY": self._handle_modify,
            "PAUSE": self._handle_pause,
            "RESUME": self._handle_resume,
            "CORRECT": self._handle_correct,
            "APPROVE": self._handle_approve,
            "REJECT": self._handle_reject,
            "SELECT_OPTION": self._handle_select_option,
        }

    _TOOL_DESCRIPTIONS: dict[str, str] = {
        "QUERY": "Answer questions about goal status, progress, or pending tasks.",
        "CONTINUE": "Start or resume execution without changing the goal.",
        "MODIFY": "Create a new goal revision from a significant redirect.",
        "PAUSE": "Temporarily halt an ACTIVE goal.",
        "RESUME": "Resume a PAUSED goal.",
        "CORRECT": "Apply a lightweight mid-execution correction (no new revision).",
        "APPROVE": "Approve a pending human task / gate.",
        "REJECT": "Reject a pending human task / gate, trigger revision.",
        "SELECT_OPTION": "Choose a pending fork option so execution can proceed.",
    }

    def available_tools(self) -> list[ToolSpec]:
        """Expose the guidance handlers as ToolSpec entries (CD-4.1 tool table).

        Useful for introspection/tests today; a future provider that supports
        native tool-calling can pass this list straight to ``chat(tools=...)``.
        """
        return [
            ToolSpec(
                name=name.lower(),
                description=description,
                parameters={
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
            )
            for name, description in self._TOOL_DESCRIPTIONS.items()
        ]

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def guide(self, project_id: uuid.UUID, *, message: str, actor: str) -> GuidanceReceipt:
        context = await self._context(project_id)
        history = await self._conversation_history(project_id, limit=10)

        # L2: pending fork — match option id/label before LLM (deterministic).
        goal_meta = dict((context.get("goal") or {}).get("metadata") or {})
        if goal_meta.get("needs_user_fork"):
            matched = _match_fork_option(
                message, list(goal_meta.get("pending_fork_options") or [])
            )
            if matched is not None:
                interpretation = GuidanceInterpretation(
                    command_type="SELECT_OPTION",
                    summary=f"选择方案：{matched.get('label') or matched.get('id')}",
                    selected_option_id=str(matched.get("id") or ""),
                )
                return await self._dispatch(
                    project_id, message, actor, interpretation, "regent-core:fork-match"
                )

        generated = await self._provider.generate_structured(
            system_prompt=self._system_prompt(context, history),
            user_prompt=str({"current_state": context, "recent_messages": history, "user_message": message}),
            response_model=GuidanceInterpretation,
        )
        interpretation = generated.output

        # In boundary-confirmation mode, numbered free-text is an answer to the
        # questions Regent just asked. Do not let the model classify it as a
        # status QUERY, which would repeat the same unknowns forever.
        goal_status = str((context.get("goal") or {}).get("status") or "")
        spec_unknowns = list((context.get("goal_spec") or {}).get("unknowns") or [])
        numbered_answers = [
            value.strip()
            for value in re.findall(
                r"(?:^|[；;\n。]\s*)(?:\d+)\s*[、.．:]\s*(.*?)(?=(?:[；;\n。]\s*\d+\s*[、.．:])|$)",
                message.strip(),
            )
            if value.strip()
        ]
        if goal_status == "DRAFT" and spec_unknowns and numbered_answers:
            answered_count = min(len(numbered_answers), len(spec_unknowns), 3)
            answered = spec_unknowns[:answered_count]
            remaining = spec_unknowns[answered_count:]
            answer_map: dict[str, str] = {}
            for item, answer in zip(answered, numbered_answers, strict=False):
                question = item.get("question") if isinstance(item, dict) else item
                answer_map[str(question)] = answer
            interpretation = GuidanceInterpretation(
                command_type="CORRECT",
                summary=f"已确认 {answered_count} 项边界",
                correction_target="requirements",
                correction_detail=message,
                explicit_constraints={
                    "boundary_answers_json": json.dumps(answer_map, ensure_ascii=False)
                },
                unknowns=[
                    str(item.get("question") if isinstance(item, dict) else item)
                    for item in remaining
                ],
                feasibility_verdict="REVISION_REQUIRED" if remaining else "FEASIBLE",
                feasibility_reasons=(
                    ["仍有待确认边界，继续下一轮确认。"]
                    if remaining
                    else ["用户已回答全部边界问题；最小范围、验收和预算可进入锁定确认。"]
                ),
            )

        # WAITING_HUMAN + pending task: directional free text is approve+resume,
        # not a silent CORRECT that leaves the goal stuck.
        pending = context.get("pending_human_tasks") or []
        if (
            goal_status == "WAITING_HUMAN"
            and pending
            and interpretation.command_type in {"CORRECT", "CONTINUE", "MODIFY"}
        ):
            interpretation = GuidanceInterpretation(
                command_type="APPROVE",
                summary=interpretation.summary
                or interpretation.correction_detail
                or "按补充方向批准并继续",
                correction_detail=interpretation.correction_detail or message,
            )

        # Check for URL-based research resume first
        resumed = await self._maybe_resume_research_more(project_id, message, actor)
        if resumed is not None:
            return resumed

        receipt = await self._dispatch(project_id, message, actor, interpretation, generated.model)

        # CD-4.1: bounded multi-step loop. A single user message can trigger a
        # short chain (e.g. QUERY → CONTINUE) when the interpretation says so;
        # manually-constructed follow-up interpretations never carry their own
        # follow_up_command, so this naturally terminates after one hop today —
        # the loop bound exists so future richer follow-up chains stay capped.
        steps = 1
        pending = interpretation.follow_up_command
        follow_summary = interpretation.follow_up_summary
        while pending is not None and steps < self._MAX_GUIDANCE_STEPS:
            follow_interpretation = GuidanceInterpretation(
                command_type=pending,
                summary=follow_summary or f"自动继续跟进（{pending}）",
            )
            follow_receipt = await self._dispatch(
                project_id, message, actor, follow_interpretation, generated.model
            )
            receipt = GuidanceReceipt(
                command_id=follow_receipt.command_id,
                command_type=f"{receipt.command_type}+{follow_receipt.command_type}",
                resulting_goal_id=follow_receipt.resulting_goal_id or receipt.resulting_goal_id,
                requires_confirmation=receipt.requires_confirmation
                or follow_receipt.requires_confirmation,
                response=f"{receipt.response}\n\n{follow_receipt.response}",
            )
            steps += 1
            pending = follow_interpretation.follow_up_command
            follow_summary = follow_interpretation.follow_up_summary
        return receipt

    async def _dispatch(
        self,
        project_id: uuid.UUID,
        message: str,
        actor: str,
        interpretation: GuidanceInterpretation,
        model: str,
    ) -> GuidanceReceipt:
        handler = self._handler_table().get(interpretation.command_type)
        if handler is None:
            return await self._handle_query(project_id, message, actor, interpretation, model)
        return await handler(project_id, message, actor, interpretation, model)

    # ------------------------------------------------------------------
    # System prompt builder — gives LLM full context
    # ------------------------------------------------------------------

    def _system_prompt(self, context: dict[str, Any], history: list[dict[str, Any]]) -> str:
        goal_status = context.get("goal", {}).get("status", "UNKNOWN")
        stage_info = context.get("goal", {}).get("execution_stage", {})
        if isinstance(stage_info, dict):
            stage = str(stage_info.get("stage") or "UNKNOWN")
        else:
            stage = str(stage_info or "UNKNOWN")
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
            parts.append(
                "Hint: goal is waiting for human input. If there are pending human tasks, "
                "directional supplements (how to fix / what to change) MUST be APPROVE — "
                "the user message becomes the resume guidance. Use CORRECT only when there "
                "is no pending task. Pure status questions remain QUERY."
            )
        goal_meta = dict((context.get("goal") or {}).get("metadata") or {})
        if goal_meta.get("needs_user_fork"):
            parts.append(
                "Hint: needs_user_fork is true — if the user picks a fork option, "
                "use SELECT_OPTION with selected_option_id."
            )

        parts.extend([
            "",
            "Command types:",
            "- QUERY: user asks about status, progress, or details. Answer informatively.",
            "- MODIFY: user wants to significantly change objectives/users/problem/deliverable. "
            "Creates and starts a new goal revision (Goal is not one-shot — revisions are normal). "
            "No confirmation gate.",
            "- CONTINUE: user says 'go ahead', 'continue', 'proceed'. Starts or retries execution.",
            "- PAUSE: user says 'pause', 'stop', 'wait', 'hold'. Temporarily halts execution.",
            "- RESUME: user says 'resume', 'continue after pause', 'go'. Resumes a paused goal.",
            "- CORRECT: user gives a specific mid-execution correction (e.g. 'use REST not GraphQL', "
            "'add dark mode', 'change API format to JSON', 'add error handling', 'make it prettier'). "
            "Goals evolve through many CORRECT turns — treat each as a durable GoalSpec snapshot + "
            "steering brief that must interrupt the current lease and resume with the new direction. "
            "Set correction_target and correction_detail fields.",
            "- APPROVE: user says 'approve', 'looks good', 'accept', 'yes'. Approves pending gate/human task.",
            "- REJECT: user says 'reject', 'no', 'wrong', 'this is not right'. Rejects pending gate, triggers revision.",
            "- SELECT_OPTION: user chose a pending fork option; set selected_option_id.",
            "",
            "Goal lifecycle note: the user's first message is only the starting Goal. "
            "Expect repeated CORRECT and occasional MODIFY; never tell the user they must "
            "recreate the project to change direction.",
            "For CORRECT, always set correction_target (one of: requirements, design, api, constraints, behavior, other) "
            "and correction_detail (the specific change requested).",
            "For MODIFY, return a complete revised proposal using supplied context and the user's message.",
            "Never execute or grant permissions.",
            "",
            "Bounded chaining (CD-4.1): if the user's single message clearly implies two "
            "sequential steps (e.g. 'what's the status, and if it's fine just continue' → "
            "QUERY then CONTINUE), set follow_up_command to the second step's command_type "
            "and follow_up_summary to a short description of that step. Leave both unset for "
            "ordinary single-step messages — chaining is capped and should be used sparingly.",
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
                # Prefer path-prefixed public browse URL over worker-local 127.0.0.1:port.
                endpoint = (
                    metadata.get("preview_url")
                    or metadata.get("last_preview_endpoint")
                )
                if isinstance(endpoint, str) and endpoint.strip():
                    preview_payload = {
                        "id": None,
                        "status": "PREVIEW_READY" if metadata.get("preview_ready") else "PREVIEW_AVAILABLE",
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

            generation_progress = await self._generation_progress_for_console(
                session,
                goal_id=goal.id,
                goal_status=goal.status,
                metadata=metadata if isinstance(metadata, dict) else {},
                stage=stage,
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
                "generation_progress": generation_progress,
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
    async def _generation_progress_for_console(
        session: AsyncSession,
        *,
        goal_id: uuid.UUID,
        goal_status: str,
        metadata: dict[str, Any],
        stage: dict[str, str] | None,
    ) -> str:
        """Honest generation sub-state for console badges.

        queued         — GenerationRunRequested waiting in outbox
        calling_model  — active GENERATING run
        stalled        — stage says GENERATING but no open work
        needs_continue — terminal-ish failure states with continue CTA
        waiting_human  — human gate
        idle           — not in generation path
        """
        if goal_status in {"FAILED", "EXHAUSTED", "BLOCKED"}:
            return "needs_continue"
        if goal_status == "WAITING_HUMAN":
            return "waiting_human"

        stage_name = ""
        if isinstance(stage, dict):
            stage_name = str(stage.get("stage") or "")
        if not stage_name:
            stage_name = str(metadata.get("execution_stage") or "")

        has_run = await session.scalar(
            select(GenerationRunModel.id)
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
                GenerationRunModel.status == "GENERATING",
            )
            .limit(1)
        )
        if has_run is not None:
            # Soft-pause / diagnostic handoff must never look like "calling_model".
            if stage_name == "DELIVERY_SOFT_PAUSE" or metadata.get("diagnostic_delivery"):
                return "needs_continue"
            return "calling_model"

        has_queue = await session.scalar(
            select(OutboxEventModel.id).where(
                OutboxEventModel.aggregate_id == goal_id,
                OutboxEventModel.event_type == "GenerationRunRequested",
                OutboxEventModel.status.in_(("PENDING", "DISPATCHING", "FAILED")),
            ).limit(1)
        )
        if has_queue is not None:
            if stage_name == "DELIVERY_SOFT_PAUSE" or metadata.get("diagnostic_delivery"):
                return "needs_continue"
            return "queued"

        if stage_name == "DELIVERY_SOFT_PAUSE" or metadata.get("diagnostic_delivery"):
            return "needs_continue"
        if stage_name == "GENERATING" and goal_status == "ACTIVE":
            return "stalled"
        return "idle"

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
        elif str(metadata.get("execution_stage") or "") == "DELIVERY_SOFT_PAUSE" or metadata.get(
            "diagnostic_delivery"
        ):
            core_activity = "idle"
            live_summary = None
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
        if isinstance(stage_info, dict):
            stage = str(stage_info.get("stage") or goal["status"])
        else:
            stage = str(stage_info or goal["status"])
        stage_label = _STAGE_LABELS.get(stage, stage)
        work = context.get("work_states", {})
        pending = context.get("pending_human_tasks", [])
        corrections = context.get("active_corrections", [])

        def clarification_prompt() -> str:
            raw = context.get("goal_spec", {}).get("unknowns", [])
            questions: list[str] = []
            for item in raw:
                value = item.get("question") if isinstance(item, dict) else item
                if str(value or "").strip():
                    questions.append(str(value).strip())
            if not questions:
                questions = [
                    "本期最小交付物具体包含什么？",
                    "你将用什么可观察结果判断它通过验收？",
                    "本期明确不做什么？",
                ]
            lines = ["\n现在请你回答以下问题："]
            lines.extend(f"{index}. {question}" for index, question in enumerate(questions[:3], 1))
            lines.append("请按“1. ……；2. ……；3. ……”回复；不确定的项目可直接写“不确定”。")
            return "\n".join(lines)

        parts = [interpretation.summary]
        parts.append(f"\n当前状态: {goal['status']} | 阶段: {stage_label}")
        if str(goal["status"]) == "DRAFT":
            parts.append(clarification_prompt())
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
        if isinstance(stage_info, dict):
            stage = str(stage_info.get("stage") or goal_status)
        else:
            # _context may return a bare stage string (not {"stage": ...}).
            stage = str(stage_info or goal_status)
        goal_id = uuid.UUID(str(context["goal"]["id"]))
        if goal_status == "DRAFT":
            raw = context.get("goal_spec", {}).get("unknowns", [])
            questions: list[str] = []
            for item in raw:
                value = item.get("question") if isinstance(item, dict) else item
                if str(value or "").strip():
                    questions.append(str(value).strip())
            if not questions:
                questions = [
                    "本期最小交付物具体包含什么？",
                    "你将用什么可观察结果判断它通过验收？",
                    "本期明确不做什么？",
                ]
            response = "目标还不能开始，因为边界和可行性尚未确认。\n现在请你回答：\n"
            response += "\n".join(
                f"{index}. {question}" for index, question in enumerate(questions[:3], 1)
            )
            response += "\n请按编号回复；不确定的项目可直接写“不确定”，我会继续缩小问题。"
            return await self._persist_simple(
                project_id, message, actor, interpretation, model, response
            )
        needs_gap_resume = await self._goal_needs_delivery_gap_resume(goal_id)
        # Soft-pause (ACTIVE+DELIVERY_SOFT_PAUSE) or halted gap states: new direction → resume.
        # Do not intercept a healthy ACTIVE run that still has stale unrelated flags.
        soft_or_gap_continue = stage == "DELIVERY_SOFT_PAUSE" or (
            needs_gap_resume
            and goal_status in {
                "WAITING_HUMAN",
                "ACTIVE",
                "PAUSED",
                "EXHAUSTED",
                "FAILED",
                "BLOCKED",
            }
        )
        if soft_or_gap_continue:
            from regent.application.delivery_gap_recovery import DeliveryGapRecoveryService

            try:
                await self._ensure_project_agent_session_on_goal(
                    project_id=project_id, goal_id=goal_id, actor=actor
                )
            except DomainError as exc:
                return await self._persist_simple(
                    project_id,
                    message,
                    actor,
                    interpretation,
                    model,
                    f"无法续跑同一 Agent Session：{exc.message}",
                )

            msg_l = (message or "").lower()
            # RecoveryCard INSPECT / STOP must not blindly re-enter generation.
            if any(
                key in msg_l
                for key in (
                    "inspect_current_result",
                    "option:inspect",
                    "查看并下载",
                    "查看现有",
                    "先给我看",
                )
            ):
                return await self._persist_simple(
                    project_id,
                    message,
                    actor,
                    interpretation,
                    model,
                    "当前未验证草稿已保存在右侧「源码 / 产物」面板，可展开查看或下载。"
                    "需要继续生成时，请选择「继续修复当前版本」或直接说明修改方向。",
                )
            if any(
                key in msg_l
                for key in ("option:stop", "action:stop", "停止目标", "停止 goal")
            ) or msg_l.strip() in {"停止", "stop"}:
                return await self._persist_simple(
                    project_id,
                    message,
                    actor,
                    interpretation,
                    model,
                    "已记录停止请求。目标保持暂停；如需恢复请再发继续指令。",
                )

            # RecoveryCard primary actions → new Attempt from snapshot (never revive old run).
            continue_from_snap = any(
                key in msg_l
                for key in (
                    "continue_from_snapshot",
                    "option:continue_current",
                    "option:continue_budget",
                    "继续修复",
                )
            )
            revise_scope = any(
                key in msg_l
                for key in (
                    "revise_scope",
                    "option:narrow_scope",
                    "缩小范围",
                )
            )
            if continue_from_snap or revise_scope:
                direction = (
                    "缩小范围：先交付核心页面与主流程；"
                    if revise_scope
                    else "从已保存快照继续修复当前版本；"
                )
                human_msg = f"{direction} {message}".strip()
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
                    except DomainError as exc:
                        return await self._persist_simple(
                            project_id,
                            message,
                            actor,
                            interpretation,
                            model,
                            f"无法续跑：{exc.message}",
                        )
                recovery = await DeliveryGapRecoveryService(self._sessions).resume_after_human(
                    goal_id=goal_id,
                    project_id=project_id,
                    actor=actor,
                    human_message=human_msg,
                )
                response = (
                    "已在同一 Agent Session 续跑修复"
                    f"（{recovery.method}）。工作区与轨迹保持连续。"
                    if recovery.recovered
                    else (
                        f"已尝试同 Session 续跑：{recovery.message}"
                        if recovery.message
                        else "已收到继续修复请求。"
                    )
                )
                return await self._persist_simple(
                    project_id, message, actor, interpretation, model, response
                )

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
                except DomainError as exc:
                    return await self._persist_simple(
                        project_id,
                        message,
                        actor,
                        interpretation,
                        model,
                        f"无法续跑：{exc.message}",
                    )
            recovery = await DeliveryGapRecoveryService(self._sessions).resume_after_human(
                goal_id=goal_id,
                project_id=project_id,
                actor=actor,
                human_message=message,
            )
            response = (
                "已收到新方向，正在同一 Agent Session 续跑修复"
                f"（{recovery.method}）。"
                if recovery.recovered
                else (
                    f"已尝试同 Session 续跑：{recovery.message}"
                    if recovery.message
                    else "已收到继续请求，正在同一 Session 恢复执行。"
                )
            )
            return await self._persist_simple(
                project_id, message, actor, interpretation, model, response
            )

        should_start = goal_status in {"DRAFT", "READY"} or (
            goal_status == "ACTIVE" and stage == "FAILED"
        )
        should_replan = goal_status in {"EXHAUSTED", "FAILED", "BLOCKED"}

        if goal_status == "READY":
            response = "Core 已接受继续请求；将在同一 Agent Session 中开始执行。"
        elif goal_status == "ACTIVE":
            response = (
                "Core 正在同一 Agent Session 安全重试。"
                if should_start
                else f"Agent Session 执行中。当前阶段: {_STAGE_LABELS.get(stage, stage)}。"
            )
        elif goal_status == "PAUSED":
            response = "目标已暂停。发送“恢复”或“resume”以继续同一 Agent Session。"
        elif should_replan:
            response = "已收到继续请求，正在同一 Agent Session 从中断处续跑。"
        else:
            response = f"当前 Goal 状态为 {goal_status}, 当前不可直接继续。"
        receipt = await self._persist_simple(
            project_id, message, actor, interpretation, model, response
        )
        if should_replan:
            from regent.application.delivery_gap_recovery import DeliveryGapRecoveryService

            goal_id = uuid.UUID(str(context["goal"]["id"]))
            goal_version = int(context["goal"].get("version", 0))
            try:
                await TransitionService(self._sessions).transition_goal(
                    TransitionContext(
                        aggregate_id=goal_id,
                        expected_version=goal_version,
                        actor=actor,
                        correlation_id=uuid.uuid4(),
                    ),
                    GoalCommand.REPLAN,
                )
            except DomainError as exc:
                return await self._persist_simple(
                    project_id,
                    message,
                    actor,
                    interpretation,
                    model,
                    f"无法续跑：{exc.message}",
                )
            try:
                await self._ensure_project_agent_session_on_goal(
                    project_id=project_id, goal_id=goal_id, actor=actor
                )
            except DomainError as exc:
                return await self._persist_simple(
                    project_id,
                    message,
                    actor,
                    interpretation,
                    model,
                    f"无法续跑同一 Agent Session：{exc.message}",
                )
            # Prefer delivery-gap resume when lineage/pending gap exists.
            if await self._goal_needs_delivery_gap_resume(goal_id):
                await DeliveryGapRecoveryService(self._sessions).resume_after_human(
                    goal_id=goal_id,
                    project_id=project_id,
                    actor=actor,
                    human_message=message,
                )
            else:
                await GoalExecutionService(self._sessions).start(
                    goal_id,
                    actor=actor,
                    idempotency_key=f"guidance-continue:{receipt.command_id}",
                )
            return receipt
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
            # I-D: keep Session chassis in sync with Goal PAUSED.
            try:
                from regent.application.project_agent_session import ProjectAgentSessionService

                await ProjectAgentSessionService(self._sessions).pause(project_id, actor=actor)
            except DomainError:
                pass
            response = "已暂停执行（Agent Session 已挂起）。你可以发送修正指令，或发送“恢复”继续同一 Session。"
        except DomainError as exc:
            response = f"暂停失败: {exc.message}"

        command_id = uuid.uuid4()
        async with self._sessions() as session, session.begin():
            conversation = await self._conversation(session, project_id)
            ordinal = await self._next_ordinal(session, conversation.id)
            remaining = [
                str(item.get("question") if isinstance(item, dict) else item).strip()
                for item in list(next_spec.unknowns or [])
                if str(item.get("question") if isinstance(item, dict) else item).strip()
            ] if next_spec is not None else []
            next_prompt = ""
            if goal.status == "DRAFT":
                questions = remaining[:3] or [
                    "本期最小交付物具体包含什么？",
                    "你将用什么可观察结果判断它通过验收？",
                    "本期明确不做什么？",
                ]
                next_prompt = "\n下一步请回答：\n" + "\n".join(
                    f"{index}. {question}" for index, question in enumerate(questions, 1)
                ) + "\n请按编号回复；不确定的项目可直接写“不确定”。"
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
            # I-D: resume same ProjectAgentSession (bump epoch) before re-lease.
            from regent.application.project_agent_session import ProjectAgentSessionService

            sessions_svc = ProjectAgentSessionService(self._sessions)
            try:
                await sessions_svc.resume_from_paused(project_id, goal_id=goal_id)
            except DomainError:
                await sessions_svc.ensure_active_session(
                    app_project_id=project_id,
                    goal_id=goal_id,
                    actor=actor,
                )
            view = await sessions_svc.bump_epoch(
                project_id,
                checkpoint_patch={"resume_method": "GUIDANCE_RESUME", "actor": actor},
            )
            async with self._sessions() as session, session.begin():
                goal = await session.get(GoalModel, goal_id, with_for_update=True)
                if goal is not None:
                    meta = dict(goal.metadata_json or {})
                    meta["project_agent_session_id"] = str(view.id)
                    meta["project_agent_session_epoch"] = view.epoch
                    meta["project_agent_session_workspace_uri"] = view.workspace_uri
                    # Clear soft-pause markers so worker can proceed.
                    meta.pop("ops_soft_pause", None)
                    if str(meta.get("execution_stage") or "") == "DELIVERY_SOFT_PAUSE":
                        meta["execution_stage"] = "QUEUED"
                    goal.metadata_json = meta
            # Try to re-trigger execution; if it fails (e.g. already ACTIVE), that's OK
            # — the goal is back to ACTIVE and pending events will be processed.
            try:
                await GoalExecutionService(self._sessions).start(
                    goal_id,
                    actor=actor,
                    idempotency_key=f"guidance-continue:resume:{view.id}:{uuid.uuid4()}",
                )
            except DomainError:
                pass  # Goal is ACTIVE, worker will pick up pending events
            response = (
                f"已恢复同一 Agent Session（{view.id}）。"
                "Core 将在同一工作区继续，不会开空白新轨迹。"
            )
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
            # H1.4: durable steering brief for next AgentRunner seed.
            metadata["session_steer_brief"] = str(detail)[:1200]
            metadata["session_steer_at"] = datetime.now(timezone.utc).isoformat()
            # Correction is intentional progress — clear progress-loop streak.
            from regent.application.agent_loop_exit import META_PROGRESS_LOOP

            metadata.pop(META_PROGRESS_LOOP, None)
            metadata.pop("_progress_writes_mark", None)
            from regent.application.work_plan import looks_like_replan_request

            if looks_like_replan_request(message) or looks_like_replan_request(detail):
                metadata["work_plan_approved"] = False
                metadata.pop("skip_plan_approve", None)
                metadata["work_plan_replan_requested"] = True
            next_spec: GoalSpecModel | None = None
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
            metadata["clarification_rounds"] = int(metadata.get("clarification_rounds") or 0) + 1
            if interpretation.feasibility_verdict is not None:
                metadata["feasibility_verdict"] = interpretation.feasibility_verdict
            if interpretation.feasibility_reasons is not None:
                metadata["feasibility_reasons"] = list(interpretation.feasibility_reasons)
            metadata["execution_boundary_locked"] = False
            metadata["latest_goal_spec_version"] = next_spec.version
            goal.metadata_json = metadata
            flag_modified(goal, "metadata_json")

            conversation = await self._conversation(session, project_id)
            ordinal = await self._next_ordinal(session, conversation.id)
            user_msg = await self._persist_message_pair(
                session, conversation.id, ordinal, message, actor,
                f"已记录修正: [{target}] {detail}\n正在中断当前执行并以最新目标方向续跑。",
                "CORRECTION_APPLIED",
                {
                    "command_id": str(command_id),
                    "goal_id": str(goal_id),
                    "correction_target": target,
                    "correction_detail": detail,
                    "total_corrections": len(corrections),
                    "goal_evolving": True,
                    "spec_version": next_spec.version,
                },
            )
            cid = await self._persist_command(
                session, conversation.id, project_id, user_msg.id,
                "CORRECT", interpretation, model, actor,
            )
            command_id = cid

        if goal_status == "DRAFT":
            remaining_questions = [
                str(item.get("question") if isinstance(item, dict) else item).strip()
                for item in unknowns
                if str(item.get("question") if isinstance(item, dict) else item).strip()
            ]
            if remaining_questions:
                response = "已记录本轮回答。下一轮请回答：\n"
                response += "\n".join(
                    f"{index}. {question}"
                    for index, question in enumerate(remaining_questions[:3], 1)
                )
                response += "\n请按编号回复；不确定可直接写“不确定”。"
            else:
                response = "已记录全部边界回答。可行性条件已满足，请确认锁定当前目标版本后再开始执行。"
            return GuidanceReceipt(command_id, "CORRECT", None, False, response)

        # Interrupt in-flight lease so steering is not deferred to a vague "next step".
        # resume_after_human clears the abort flag before requeueing.
        from regent.application.agent_control import apply_abort_to_goal_metadata

        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is not None and goal.status == "ACTIVE":
                meta = apply_abort_to_goal_metadata(
                    dict(goal.metadata_json or {}),
                    str(goal_id),
                    actor=actor,
                    reason="goal_corrected",
                )
                meta["goal_revision_pending_resume"] = True
                meta["goal_revision_reason"] = "correct"
                goal.metadata_json = meta
                flag_modified(goal, "metadata_json")

        # Always try to resume same Session with the new GoalSpec + steer brief.
        resume_note = ""
        try:
            from regent.application.delivery_gap_recovery import DeliveryGapRecoveryService

            await self._ensure_project_agent_session_on_goal(
                project_id=project_id, goal_id=goal_id, actor=actor
            )
            recovery = await DeliveryGapRecoveryService(self._sessions).resume_after_human(
                goal_id=goal_id,
                project_id=project_id,
                actor=actor,
                human_message=f"[Goal corrected] {detail}",
                option_id="continue_fix",
            )
            if recovery.recovered:
                resume_note = f" 已按最新修正续跑（{recovery.method}）。"
            elif recovery.message:
                resume_note = f" 修正已落盘；续跑：{recovery.message}"
        except Exception as exc:  # noqa: BLE001 — correction must still succeed
            resume_note = f" 修正已落盘；自动续跑暂未触发（{type(exc).__name__}）。"

        response = f"已记录修正: [{target}] {detail}.{resume_note}"
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
        from regent.application.delivery_gap_recovery import DeliveryGapRecoveryService

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
            # No HumanTask: still resume soft-pause / plan_approve ASK (ACTIVE+DELIVERY_SOFT_PAUSE)
            # or WAITING_HUMAN gaps that never got a task row.
            goal_status = str(context["goal"]["status"])
            needs_gap_resume = await self._goal_needs_delivery_gap_resume(goal_id)
            meta = dict((context.get("goal") or {}).get("metadata") or {})
            pending_ask = dict(meta.get("pending_agent_loop_ask") or {})
            ask_type = str(pending_ask.get("ask_type") or "")
            gap_kind = str(meta.get("delivery_gap_kind") or "")
            soft_plan_approve = (
                needs_gap_resume
                or ask_type == "plan_approve"
                or gap_kind == "PLAN_APPROVE"
                or "approve_plan" in (message or "").lower()
            )
            if soft_plan_approve and goal_status in {
                "WAITING_HUMAN",
                "ACTIVE",
                "PAUSED",
                "EXHAUSTED",
                "FAILED",
                "BLOCKED",
            }:
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
                    except DomainError as exc:
                        response = f"批准失败: {exc.message}"
                        return await self._persist_simple(
                            project_id, message, actor, interpretation, model, response
                        )
                try:
                    await self._ensure_project_agent_session_on_goal(
                        project_id=project_id, goal_id=goal_id, actor=actor
                    )
                except DomainError as exc:
                    return await self._persist_simple(
                        project_id,
                        message,
                        actor,
                        interpretation,
                        model,
                        f"无法续跑同一 Agent Session：{exc.message}",
                    )
                msg_l = (message or "").lower()
                option_id = None
                for cand in (
                    "approve_plan",
                    "allow_always_session",
                    "allow_once",
                    "deny",
                    "stop",
                    "continue_fix",
                    "revise_plan",
                ):
                    if f"option:{cand}" in msg_l or cand in msg_l:
                        option_id = cand
                        break
                if option_id is None and ask_type == "plan_approve":
                    option_id = "approve_plan"
                if option_id is None and ask_type == "permission":
                    option_id = str(pending_ask.get("suggested") or "allow_once")
                recovery = await DeliveryGapRecoveryService(self._sessions).resume_after_human(
                    goal_id=goal_id,
                    project_id=project_id,
                    actor=actor,
                    human_message=message,
                    option_id=option_id,
                )
                if recovery.recovered:
                    response = (
                        "已批准计划。正在同一 Agent Session 继续执行"
                        f"（{recovery.method}）。"
                    )
                else:
                    response = (
                        "已批准并尝试恢复执行，但未能自动续跑："
                        f"{recovery.message or 'unknown'}"
                    )
                return await self._persist_simple(
                    project_id,
                    message,
                    actor,
                    interpretation,
                    model,
                    response,
                    assistant_type="APPROVE_RESULT",
                    assistant_metadata={
                        "approved": True,
                        "delivery_gap_resume": True,
                        "recovered": recovery.recovered,
                        "method": recovery.method,
                        "plan_approve": ask_type == "plan_approve" or gap_kind == "PLAN_APPROVE",
                    },
                )
            if goal_status == "WAITING_HUMAN":
                # Empty resume: still clear stuck "等待你确认" live_action strip.
                from regent.application.live_action import set_goal_live_action

                await set_goal_live_action(
                    self._sessions,
                    goal_id,
                    "已批准，正在继续执行",
                    stage="ACTIVE",
                    event_type="APPROVE_RESULT",
                )
                response = "已批准。目标恢复执行。"
            else:
                response = "当前没有待批准的任务。"
            return await self._persist_simple(
                project_id, message, actor, interpretation, model, response,
                assistant_type="APPROVE_RESULT",
                assistant_metadata={"approved": True, "empty_resume": True},
            )

        # Complete the first pending human task
        task_id = uuid.UUID(pending[0]["id"])
        task_type = str(pending[0].get("task_type") or "")
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

        # AGENT_LOOP_ASK (plan_approve / permission soft-pause): complete alone does not resume.
        if task_type == "AGENT_LOOP_ASK":
            try:
                await self._ensure_project_agent_session_on_goal(
                    project_id=project_id, goal_id=goal_id, actor=actor
                )
            except DomainError as exc:
                return await self._persist_simple(
                    project_id,
                    message,
                    actor,
                    interpretation,
                    model,
                    f"已关闭确认卡，但无法续跑 Session：{exc.message}",
                    assistant_type="APPROVE_RESULT",
                )
            meta = dict((context.get("goal") or {}).get("metadata") or {})
            pending_ask = dict(meta.get("pending_agent_loop_ask") or {})
            msg_l = (message or "").lower()
            option_id = None
            for cand in (
                "approve_plan",
                "allow_always_session",
                "allow_once",
                "deny",
                "stop",
                "continue_fix",
                "revise_plan",
            ):
                if f"option:{cand}" in msg_l or cand in msg_l:
                    option_id = cand
                    break
            ask_type = str(pending_ask.get("ask_type") or "")
            if option_id is None and ask_type == "plan_approve":
                option_id = "approve_plan"
            if option_id is None and ask_type == "permission":
                option_id = str(pending_ask.get("suggested") or "allow_once")
            if option_id is None:
                option_id = str(pending_ask.get("suggested") or "approve_plan")
            recovery = await DeliveryGapRecoveryService(self._sessions).resume_after_human(
                goal_id=goal_id,
                project_id=project_id,
                actor=actor,
                human_message=message,
                option_id=option_id,
            )
            response = (
                "已批准。正在同一 Agent Session 继续执行"
                f"（{recovery.method}）。"
                if recovery.recovered
                else (
                    f"已批准确认卡，但续跑未成功：{recovery.message or 'unknown'}"
                )
            )
            return await self._persist_simple(
                project_id,
                message,
                actor,
                interpretation,
                model,
                response,
                assistant_type="APPROVE_RESULT",
                assistant_metadata={
                    "approved": True,
                    "task_id": str(task_id),
                    "task_type": task_type,
                    "delivery_gap_resume": True,
                    "recovered": recovery.recovered,
                    "method": recovery.method,
                },
            )

        # DELIVERY_GAP_INTERVENE: HumanTaskService.complete emits DeliveryGapHumanApproved
        # which resumes the ladder; keep chat copy honest about replan.
        if task_type == "DELIVERY_GAP_INTERVENE":
            response = (
                "已批准。正在重新规划并继续生成交付物"
                "（能力阶梯计数已重置，将再次尝试交付恢复）。"
            )
        else:
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
                    "delivery_gap_resume": task_type == "DELIVERY_GAP_INTERVENE",
                },
            )
            cid = await self._persist_command(
                session, conversation.id, project_id, user_msg.id,
                "APPROVE", interpretation, model, actor,
            )
            command_id = cid
        return GuidanceReceipt(command_id, "APPROVE", None, False, response)

    async def _ensure_project_agent_session_on_goal(
        self,
        *,
        project_id: uuid.UUID,
        goal_id: uuid.UUID,
        actor: str,
    ) -> None:
        """I-D: CONTINUE/soft-pause resume must keep Session metadata on the Goal.

        Fail closed: Session ensure errors surface to the caller (no silent blank Run).
        """
        from regent.application.project_agent_session import ProjectAgentSessionService

        sessions_svc = ProjectAgentSessionService(self._sessions)
        try:
            view = await sessions_svc.resume_from_paused(project_id, goal_id=goal_id)
        except DomainError:
            view = await sessions_svc.ensure_active_session(
                app_project_id=project_id,
                goal_id=goal_id,
                actor=actor,
            )
        view = await sessions_svc.bump_epoch(
            project_id,
            checkpoint_patch={"resume_method": "GUIDANCE_CONTINUE", "actor": actor},
        )
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, "goal not found for session ensure")
            meta = dict(goal.metadata_json or {})
            meta["project_agent_session_id"] = str(view.id)
            meta["project_agent_session_epoch"] = view.epoch
            meta["project_agent_session_workspace_uri"] = view.workspace_uri
            goal.metadata_json = meta

    async def _goal_needs_delivery_gap_resume(self, goal_id: uuid.UUID) -> bool:
        async with self._sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                return False
            meta = dict(goal.metadata_json or {})
            termination = dict(meta.get("termination") or {})
            halt = dict(meta.get("halt") or {})
            if meta.get("awaiting_human_intervention"):
                return True
            if termination.get("ladder_exhausted"):
                return True
            if meta.get("pending_delivery_gap_human"):
                return True
            stage = str(meta.get("execution_stage") or "")
            halt_stage = str(halt.get("stage") or "")
            handoff = str(termination.get("handoff") or "")
            if stage == "DELIVERY_SOFT_PAUSE" or handoff == "SOFT_PAUSE":
                return True
            if "DELIVERY_GAP" in stage or stage.endswith("_NEEDS_HUMAN") or stage.endswith("_EXHAUSTED"):
                return True
            if "DELIVERY_GAP" in halt_stage or halt_stage.endswith("_EXHAUSTED"):
                return True
            return False

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

    async def _handle_select_option(
        self,
        project_id: uuid.UUID,
        message: str,
        actor: str,
        interpretation: GuidanceInterpretation,
        model: str,
    ) -> GuidanceReceipt:
        """Apply a pending fork choice, clear the gate, and start if still DRAFT."""
        context = await self._context(project_id)
        goal_id = uuid.UUID(str(context["goal"]["id"]))
        meta = dict((context.get("goal") or {}).get("metadata") or {})
        options = list(meta.get("pending_fork_options") or [])
        option_id = (interpretation.selected_option_id or "").strip()
        chosen = next(
            (o for o in options if isinstance(o, dict) and str(o.get("id")) == option_id),
            None,
        )
        if chosen is None:
            chosen = _match_fork_option(message, options)
        if chosen is None:
            labels = ", ".join(
                str(o.get("label") or o.get("id")) for o in options if isinstance(o, dict)
            )
            response = f"未识别到有效选项。请从以下方向中选择其一：{labels}"
            return await self._persist_simple(
                project_id, message, actor, interpretation, model, response
            )

        command_id = uuid.uuid4()
        should_start = False
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, "goal not found")
            metadata = dict(goal.metadata_json or {})
            metadata["needs_user_fork"] = False
            metadata["pending_fork_options"] = []
            metadata["selected_fork"] = {
                "id": str(chosen.get("id")),
                "label": str(chosen.get("label") or ""),
                "description": str(chosen.get("description") or ""),
                "actor": actor,
                "at": datetime.now(UTC).isoformat(),
            }
            metadata["goal_clarity_state"] = "FORK_RESOLVED"
            plan = dict(metadata.get("runtime_plan") or {})
            plan["needs_user_fork"] = False
            plan["selected_fork"] = metadata["selected_fork"]
            metadata["runtime_plan"] = plan

            latest_spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == goal_id)
                .order_by(GoalSpecModel.version.desc())
                .with_for_update()
            )
            if latest_spec is not None:
                constraints = dict(latest_spec.explicit_constraints or {})
                constraints["selected_fork_id"] = str(chosen.get("id"))
                constraints["selected_fork_label"] = str(chosen.get("label") or "")
                inferences = dict(latest_spec.system_inferences or {})
                inferences["selected_fork"] = metadata["selected_fork"]
                spec_content = {
                    "explicit_constraints": constraints,
                    "system_inferences": inferences,
                    "unknowns": list(latest_spec.unknowns or []),
                    "success_criteria": dict(latest_spec.success_criteria or {}),
                    "source_refs": [
                        *list(latest_spec.source_refs or []),
                        {"type": "fork_selection", "id": str(chosen.get("id"))},
                    ],
                }
                latest_spec.status = "SUPERSEDED"
                next_spec = GoalSpecModel(
                    id=uuid.uuid4(),
                    goal_id=goal_id,
                    version=latest_spec.version + 1,
                    status="DRAFT" if goal.status == "DRAFT" else "FROZEN",
                    content_hash=canonical_hash(spec_content),
                    confirmed_by=(
                        None if goal.status == "DRAFT" else "regent-core:fork-selection"
                    ),
                    confirmed_at=None if goal.status == "DRAFT" else datetime.now(UTC),
                    **spec_content,
                )
                session.add(next_spec)
                metadata["latest_goal_spec_version"] = next_spec.version

            goal.metadata_json = metadata
            flag_modified(goal, "metadata_json")

            conversation = await self._conversation(session, project_id)
            ordinal = await self._next_ordinal(session, conversation.id)
            label = str(chosen.get("label") or chosen.get("id"))
            user_msg = await self._persist_message_pair(
                session,
                conversation.id,
                ordinal,
                message,
                actor,
                f"已记录你的选择：{label}。该选择只确定交互方向，不会直接开工。{next_prompt}",
                "FORK_SELECTED",
                {
                    "command_id": str(command_id),
                    "goal_id": str(goal_id),
                    "selected_fork": metadata["selected_fork"],
                },
            )
            cid = await self._persist_command(
                session,
                conversation.id,
                project_id,
                user_msg.id,
                "SELECT_OPTION",
                interpretation,
                model,
                actor,
            )
            command_id = cid

        return GuidanceReceipt(
            command_id,
            "SELECT_OPTION",
            goal_id,
            False,
            f"已选择：{chosen.get('label') or chosen.get('id')}",
        )


def _match_fork_option(
    message: str, options: list[Any]
) -> dict[str, Any] | None:
    text = (message or "").strip()
    if not text or not options:
        return None
    lowered = text.lower()
    # Exact id / "option:id" / bare index 1..n
    for idx, raw in enumerate(options):
        if not isinstance(raw, dict):
            continue
        oid = str(raw.get("id") or "").strip()
        label = str(raw.get("label") or "").strip()
        if oid and (lowered == oid.lower() or f"option:{oid.lower()}" in lowered):
            return raw
        if label and (label.lower() in lowered or lowered in label.lower()):
            return raw
        if lowered in {str(idx + 1), f"选项{idx + 1}", f"方案{idx + 1}"}:
            return raw
    return None
