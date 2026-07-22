import uuid
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.goal_execution_service import GoalExecutionService
from regent.application.p1_contracts import canonical_hash
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
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
    HypothesisDecisionModel,
    OutboxEventModel,
    ProductHypothesisModel,
    ReleaseCandidateModel,
    RequirementRevisionModel,
    WorkModel,
    WorkspaceSnapshotModel,
)
from regent.model import ModelProvider


class GuidanceInterpretation(BaseModel):
    command_type: Literal["QUERY", "MODIFY", "CONTINUE"]
    summary: str = Field(min_length=1)
    objective: str | None = None
    product_intent: str | None = None
    target_users: str | None = None
    problem: str | None = None
    first_deliverable: str | None = None
    success_criteria: dict[str, str | int | float | bool] | None = None
    explicit_constraints: dict[str, str | int | float | bool] | None = None
    non_goals: list[str] | None = None
    unknowns: list[str] | None = None


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

    async def guide(self, project_id: uuid.UUID, *, message: str, actor: str) -> GuidanceReceipt:
        context = await self._context(project_id)
        generated = await self._provider.generate_structured(
            system_prompt=(
                "Classify a follow-up message for an existing App. QUERY only reads status or "
                "history. MODIFY changes objective, users, problem, deliverable, success criteria, "
                "constraints, or non-goals. CONTINUE asks to proceed without changing the frozen "
                "goal. For MODIFY, return a complete revised proposal using supplied context and "
                "the user's message. Never execute or grant permissions."
            ),
            user_prompt=str({"current": context, "message": message}),
            response_model=GuidanceInterpretation,
        )
        interpretation = generated.output
        if interpretation.command_type == "QUERY":
            return await self._record_query(
                project_id, message, actor, interpretation, generated.model
            )
        if interpretation.command_type == "CONTINUE":
            return await self._record_continue(
                project_id, message, actor, interpretation, generated.model
            )
        return await self._create_revision(
            project_id, message, actor, interpretation, generated.model
        )

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
                    "metadata": goal.metadata_json,
                    "execution_stage": await self._project_execution_stage(session, goal.id),
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
                "preview": (
                    {
                        "id": str(preview.id),
                        "status": preview.status,
                        "endpoint": preview.preview_endpoint,
                        "failure_code": preview.failure_code,
                        "failure_summary": preview.failure_summary,
                    }
                    if preview
                    else None
                ),
            }

    @staticmethod
    async def _project_execution_stage(
        session: AsyncSession, goal_id: uuid.UUID
    ) -> dict[str, str]:
        """Project execution stage from underlying objects.

        Returns {"stage": "...", "object_id": "..."} dict.
        """
        # Deployment SUCCEEDED -> DEPLOYED
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

        # AppBuild PASSED -> BUILD_PASSED
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

        # WorkspaceSnapshot -> SNAPSHOT_READY
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

        # GenerationRun -> GENERATING
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

        # CapabilityResolutionPlan SATISFIED -> RESOLVED
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

        # HypothesisDecision SELECT -> DECIDED
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

        # DiscoveryRound -> DISCOVERING
        discovery = await session.scalar(
            select(DiscoveryRoundModel)
            .where(DiscoveryRoundModel.goal_id == goal_id)
            .order_by(DiscoveryRoundModel.created_at.desc())
            .limit(1)
        )
        if discovery is not None:
            return {"stage": "DISCOVERING", "object_id": str(discovery.id)}

        # GoalExecutionRequested outbox event -> QUEUED
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

    async def _record_query(
        self,
        project_id: uuid.UUID,
        message: str,
        actor: str,
        interpretation: GuidanceInterpretation,
        model: str,
    ) -> GuidanceReceipt:
        context = await self._context(project_id)
        response = (
            f"{interpretation.summary}\n\n当前 Goal: {context['goal']['status']}; "
            f"工作状态: {context['work_states']}。"
        )
        return await self._persist_simple(
            project_id, message, actor, interpretation, model, response
        )

    async def _record_continue(
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
        should_start = goal_status == "READY" or (
            goal_status == "ACTIVE" and stage == "FAILED"
        )

        if goal_status == "READY":
            response = "Core 已接受继续请求并开始执行。"
        elif goal_status == "ACTIVE":
            response = (
                "Core 正在安全重试。" if should_start else f"Core 正在执行。当前阶段: {stage}。"
            )
        else:
            response = f"当前 Goal 状态为 {goal_status}, 需要先确认或创建新目标, 不能直接继续。"
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

    async def _persist_simple(
        self,
        project_id: uuid.UUID,
        message: str,
        actor: str,
        interpretation: GuidanceInterpretation,
        model: str,
        response: str,
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
                        message_type=f"{interpretation.command_type}_RESULT",
                        content=response,
                        metadata_json={"command_id": str(command_id)},
                        created_by="regent-core",
                    ),
                )
            )
        return GuidanceReceipt(command_id, interpretation.command_type, None, False, response)

    async def _create_revision(
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
                        message_type="APP_CONFIRMATION_REQUIRED",
                        content="我已根据你的指导形成新一轮目标草案。确认前不会重新规划或执行。",
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
        return GuidanceReceipt(command_id, "MODIFY", goal_id, True, interpretation.summary)
