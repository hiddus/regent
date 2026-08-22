import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.live_action import build_live_action
from regent.application.project_agent_session import ProjectAgentSessionService
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


@dataclass(frozen=True, slots=True)
class GoalExecutionReceipt:
    goal_id: uuid.UUID
    project_id: uuid.UUID
    status: str
    stage: str
    event_id: uuid.UUID | None


class GoalExecutionService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def start(
        self,
        goal_id: uuid.UUID,
        *,
        actor: str,
        idempotency_key: str,
    ) -> GoalExecutionReceipt:
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None or goal.app_project_id is None:
                raise DomainError(ErrorCode.NOT_FOUND, "app goal not found")
            project = await session.get(AppProjectModel, goal.app_project_id)
            spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == goal_id)
                .order_by(GoalSpecModel.version.desc())
            )
            if project is None or spec is None:
                raise DomainError(ErrorCode.NOT_FOUND, "app goal context not found")
            meta = dict(goal.metadata_json or {})
            if meta.get("needs_user_fork"):
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    "goal awaits user fork selection before start",
                )
            try:
                budget_limit = float(meta.get("budget_limit"))
            except (TypeError, ValueError):
                budget_limit = 0.0
            if budget_limit <= 0:
                raise DomainError(
                    ErrorCode.POLICY_DENIED,
                    "goal requires a positive budget_limit before start",
                )
            if not bool(meta.get("execution_boundary_locked")):
                raise DomainError(
                    ErrorCode.POLICY_DENIED,
                    "goal boundary and feasibility must be confirmed before start",
                )
            if (
                spec.status != "FROZEN"
                or not spec.confirmed_by
                or meta.get("locked_spec_hash") != spec.content_hash
                or int(meta.get("locked_spec_version") or 0) != spec.version
            ):
                raise DomainError(ErrorCode.POLICY_DENIED, "confirmed GoalSpec lock mismatch")
            auto_prepared = False
            if goal.status == "DRAFT":
                # Explicit user start: freeze draft → EXPLORING/PROVISIONAL.
                spec.confirmed_by = actor
                spec.status = "FROZEN"
                audit = AuditRecordModel(
                    id=uuid.uuid4(),
                    goal_id=goal.id,
                    action="SNAPSHOT_GOAL_SPEC_FOR_EXECUTION",
                    detail={
                        "explicit_user_start": True,
                        "spec_version": spec.version,
                        "has_unknowns": bool(spec.unknowns),
                    },
                    created_by=actor,
                )
                session.add(audit)
                goal.status = "EXPLORING" if spec.unknowns else "PROVISIONAL"
                goal.version += 1
                goal.metadata_json = {
                    **meta,
                    "explicit_user_start": True,
                    "execution_idempotency_key": idempotency_key,
                    "execution_event_id": str(uuid.uuid4()),
                }
                return GoalExecutionReceipt(
                    goal.id, project.id, goal.status, "SNAPSHOT", None,
                )
            metadata = dict(goal.metadata_json)
            current_key = metadata.get("execution_idempotency_key")
            current_stage = str(metadata.get("execution_stage", "NOT_STARTED"))
            if current_key == idempotency_key and goal.status == "ACTIVE":
                return GoalExecutionReceipt(goal.id, project.id, goal.status, current_stage, None)
            retryable = goal.status == "ACTIVE" and (
                current_stage == "FAILED"
                or str(idempotency_key).startswith("guidance-continue:")
            )
            if (
                (goal.status != "READY" and not retryable)
                or spec.status != "FROZEN"
                or project.status != "ACTIVE"
            ):
                raise DomainError(
                    ErrorCode.INVALID_STATE, "startable or retryable app goal required"
                )
            event_id = uuid.uuid4()
            goal.status = "ACTIVE"
            goal.version += 1
            # I-A: ACTIVE project must have exactly one ProjectAgentSession.
            # Chassis only — does not create a third Agent loop.
            session_view = await ProjectAgentSessionService(self._sessions).ensure_active_session_in(
                session,
                app_project_id=project.id,
                goal_id=goal.id,
                actor=actor,
            )
            goal.metadata_json = {
                **dict(goal.metadata_json or {}),
                **metadata,
                "execution_idempotency_key": idempotency_key,
                "execution_event_id": str(event_id),
                "project_agent_session_id": str(session_view.id),
                "project_agent_session_epoch": session_view.epoch,
                "project_agent_session_workspace_uri": session_view.workspace_uri,
            }
            payload = {
                "goal_id": str(goal.id),
                "app_project_id": str(project.id),
                "actor": actor,
                "idempotency_key": idempotency_key,
                "auto_prepared": auto_prepared,
                "goal_spec_version": spec.version,
                "project_agent_session_id": str(session_view.id),
                "project_agent_session_epoch": session_view.epoch,
            }
            session.add_all(
                (
                    AuditRecordModel(
                        id=uuid.uuid4(),
                        aggregate_type="goal",
                        aggregate_id=goal.id,
                        aggregate_version=goal.version,
                        action="START_GOAL_EXECUTION",
                        actor=actor,
                        payload=payload,
                        correlation_id=goal.correlation_id,
                    ),
                    OutboxEventModel(
                        id=event_id,
                        event_type="GoalExecutionRequested",
                        aggregate_type="goal",
                        aggregate_id=goal.id,
                        aggregate_version=goal.version,
                        payload=payload,
                        correlation_id=goal.correlation_id,
                    ),
                )
            )
            await self._append_event(
                session,
                project.id,
                "GOAL_EXECUTION_QUEUED",
                "Core 已基于当前理解开始执行; 目标仍可继续补充和修正。",
                {"goal_id": str(goal.id), "event_id": str(event_id)},
            )
            await session.flush()
            return GoalExecutionReceipt(goal.id, project.id, goal.status, "QUEUED", event_id)

    async def update_stage(
        self,
        goal_id: uuid.UUID,
        stage: str,
        *,
        message: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None or goal.app_project_id is None:
                raise DomainError(ErrorCode.NOT_FOUND, "app goal not found")
            goal.metadata_json = {
                **goal.metadata_json,
                "execution_stage": stage,
                "live_action": build_live_action(
                    message,
                    stage=stage,
                    event_type=f"GOAL_EXECUTION_{stage}",
                ),
            }
            await self._append_event(
                session,
                goal.app_project_id,
                f"GOAL_EXECUTION_{stage}",
                message,
                {"goal_id": str(goal.id), "stage": stage, **(metadata or {})},
            )

    @staticmethod
    async def _append_event(
        session: AsyncSession,
        project_id: uuid.UUID,
        message_type: str,
        content: str,
        metadata: dict[str, str],
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
                role="EVENT",
                message_type=message_type,
                content=content,
                metadata_json=metadata,
                created_by="regent-core",
            )
        )
