import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.p1_contracts import canonical_hash
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AppProjectModel,
    AuditRecordModel,
    ConversationMessageModel,
    ConversationModel,
    GoalModel,
    GoalSpecModel,
    OutboxEventModel,
)
from regent.model import ModelProvider, ModelUsage


class ProductUnderstanding(BaseModel):
    app_name: str = Field(min_length=1, max_length=120)
    product_intent: str = Field(min_length=1)
    target_users: str = Field(min_length=1)
    problem: str = Field(min_length=1)
    first_deliverable: str = Field(min_length=1)
    success_criteria: dict[str, str | int | float | bool] = Field(min_length=1)
    explicit_constraints: dict[str, str | int | float | bool] = Field(default_factory=dict)
    non_goals: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AppProjectDraftReceipt:
    project: AppProjectModel
    goal: GoalModel
    spec: GoalSpecModel
    conversation: ConversationModel
    understanding: ProductUnderstanding
    model: str
    usage: ModelUsage


@dataclass(frozen=True, slots=True)
class ConfirmAppProjectReceipt:
    project: AppProjectModel
    goal: GoalModel
    spec: GoalSpecModel


class AppProjectService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: ModelProvider,
    ) -> None:
        self._sessions = sessions
        self._provider = provider

    async def create_draft(self, *, idea: str, actor: str) -> AppProjectDraftReceipt:
        response = await self._provider.generate_structured(
            system_prompt=(
                "Turn the product idea into a concise confirmation card. Do not execute, plan, "
                "browse, or invent user constraints. Success criteria must be externally "
                "observable. Keep the first deliverable small enough for a preview validation."
            ),
            user_prompt=idea,
            response_model=ProductUnderstanding,
        )
        understanding = response.output
        project_id, goal_id, spec_id, conversation_id = (uuid.uuid4() for _ in range(4))
        correlation_id = uuid.uuid4()
        constraints = {
            **understanding.explicit_constraints,
            "non_goals": understanding.non_goals,
        }
        unknowns = [
            {"question": question, "blocking": False} for question in understanding.unknowns
        ]
        spec_content = {
            "explicit_constraints": constraints,
            "system_inferences": {
                "target_users": understanding.target_users,
                "problem": understanding.problem,
                "first_deliverable": understanding.first_deliverable,
            },
            "unknowns": unknowns,
            "success_criteria": understanding.success_criteria,
            "source_refs": [{"type": "conversation", "conversation_id": str(conversation_id)}],
        }
        project = AppProjectModel(
            id=project_id,
            name=understanding.app_name,
            product_intent=understanding.product_intent,
            status="DRAFT",
            created_by=actor,
        )
        goal = GoalModel(
            id=goal_id,
            app_project_id=project_id,
            original_input=idea,
            status="DRAFT",
            version=0,
            created_by=actor,
            correlation_id=correlation_id,
            metadata_json={
                "target_users": understanding.target_users,
                "problem": understanding.problem,
                "first_deliverable": understanding.first_deliverable,
                "understanding_model": response.model,
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
        conversation = ConversationModel(
            id=conversation_id,
            app_project_id=project_id,
            title=understanding.app_name,
            status="ACTIVE",
            created_by=actor,
            metadata_json={"type": "APP"},
        )
        messages = (
            ConversationMessageModel(
                id=uuid.uuid4(),
                conversation_id=conversation_id,
                ordinal=1,
                role="USER",
                message_type="CREATE_APP_REQUEST",
                content=idea,
                metadata_json={},
                created_by=actor,
            ),
            ConversationMessageModel(
                id=uuid.uuid4(),
                conversation_id=conversation_id,
                ordinal=2,
                role="ASSISTANT",
                message_type="APP_CONFIRMATION_REQUIRED",
                content="我已经形成产品理解草案。确认前不会规划、生成、构建或发布。",
                metadata_json={
                    "app_project_id": str(project_id),
                    "goal_id": str(goal_id),
                    "goal_spec_id": str(spec_id),
                    "goal_spec_hash": spec.content_hash,
                    "goal_spec_version": spec.version,
                    "goal_spec_status": spec.status,
                    "understanding": understanding.model_dump(mode="json"),
                },
                created_by="regent-core",
            ),
        )
        async with self._sessions() as session, session.begin():
            session.add_all((project, goal, spec, conversation))
            await session.flush()
            session.add_all(messages)
        return AppProjectDraftReceipt(
            project, goal, spec, conversation, understanding, response.model, response.usage
        )

    async def list_projects(self, *, limit: int = 100) -> list[AppProjectModel]:
        async with self._sessions() as session:
            return list(
                await session.scalars(
                    select(AppProjectModel).order_by(AppProjectModel.updated_at.desc()).limit(limit)
                )
            )

    async def get(self, project_id: uuid.UUID) -> AppProjectModel:
        async with self._sessions() as session:
            project = await session.get(AppProjectModel, project_id)
            if project is None:
                raise DomainError(ErrorCode.NOT_FOUND, "app project not found")
            return project

    async def confirm(
        self, project_id: uuid.UUID, *, actor: str, expected_spec_hash: str
    ) -> ConfirmAppProjectReceipt:
        now = datetime.now(UTC)
        async with self._sessions() as session, session.begin():
            project = await session.get(AppProjectModel, project_id, with_for_update=True)
            if project is None:
                raise DomainError(ErrorCode.NOT_FOUND, "app project not found")
            goal = await session.scalar(
                select(GoalModel)
                .where(GoalModel.app_project_id == project_id)
                .order_by(GoalModel.created_at.desc())
                .with_for_update()
            )
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, "app project goal not found")
            spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == goal.id)
                .order_by(GoalSpecModel.version.desc())
                .with_for_update()
            )
            if spec is None:
                raise DomainError(ErrorCode.NOT_FOUND, "goal spec not found")
            if spec.content_hash != expected_spec_hash:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "goal proposal changed")
            if spec.status == "FROZEN" and goal.status in {"READY", "ACTIVE"}:
                return ConfirmAppProjectReceipt(project, goal, spec)
            if (
                project.status not in {"DRAFT", "ACTIVE"}
                or goal.status != "DRAFT"
                or spec.status != "DRAFT"
            ):
                raise DomainError(ErrorCode.INVALID_STATE, "app project cannot be confirmed")
            active_other = await session.scalar(
                select(GoalModel.id).where(
                    GoalModel.app_project_id == project_id,
                    GoalModel.id != goal.id,
                    GoalModel.status.in_(("READY", "ACTIVE")),
                )
            )
            if active_other is not None:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    "finish or stop the current goal before confirming a revision",
                )
            spec.status = "FROZEN"
            spec.confirmed_by = actor
            spec.confirmed_at = now
            project.status = "ACTIVE"
            goal.status = "READY"
            goal.version = 1
            payload = {
                "app_project_id": str(project.id),
                "goal_spec_id": str(spec.id),
                "goal_spec_version": spec.version,
                "content_hash": spec.content_hash,
                "confirmed_by": actor,
            }
            session.add_all(
                (
                    AuditRecordModel(
                        id=uuid.uuid4(),
                        aggregate_type="goal",
                        aggregate_id=goal.id,
                        aggregate_version=goal.version,
                        action="FREEZE_GOAL_SPEC",
                        actor=actor,
                        payload=payload,
                        correlation_id=goal.correlation_id,
                    ),
                    OutboxEventModel(
                        id=uuid.uuid4(),
                        event_type="GoalSpecFrozen",
                        aggregate_type="goal",
                        aggregate_id=goal.id,
                        aggregate_version=goal.version,
                        payload=payload,
                        correlation_id=goal.correlation_id,
                    ),
                )
            )
            conversation = await session.scalar(
                select(ConversationModel).where(ConversationModel.app_project_id == project_id)
            )
            if conversation is not None:
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
                        message_type="GOAL_CONFIRMED",
                        content="产品目标已确认并固化, Core 现在可以开始规划。",
                        metadata_json=payload,
                        created_by="regent-core",
                    )
                )
            await session.flush()
            return ConfirmAppProjectReceipt(project, goal, spec)
