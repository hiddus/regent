import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from regent.application.execution_events import (
    DELIVERY_GAP_HUMAN_APPROVED,
    QUALITY_APPROVAL_COMPLETED,
    RELEASE_APPROVAL_COMPLETED,
    EventEnvelope,
    make_idempotency_key,
    make_outbox_event,
)
from regent.application.live_action import merge_live_action_into_metadata
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import GoalModel, HumanTaskModel, ReleaseCandidateModel


class HumanTaskService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self,
        *,
        goal_id: uuid.UUID,
        work_id: uuid.UUID | None,
        run_id: uuid.UUID | None,
        task_type: str,
        prompt: str,
        requested_by: str,
        due_at: datetime,
    ) -> uuid.UUID:
        if due_at.tzinfo is None:
            raise ValueError("due_at must be timezone-aware")
        task_id = uuid.uuid4()
        async with self._sessions() as session, session.begin():
            session.add(
                HumanTaskModel(
                    id=task_id,
                    goal_id=goal_id,
                    work_id=work_id,
                    run_id=run_id,
                    task_type=task_type,
                    prompt=prompt,
                    requested_by=requested_by,
                    due_at=due_at,
                    status="OPEN",
                )
            )
        return task_id

    async def complete(
        self,
        task_id: uuid.UUID,
        *,
        assigned_to: str,
        response: dict[str, Any],
    ) -> None:
        """Mark task COMPLETED and emit gate-completion outbox events when needed.

        Chat guidance and HTTP `/human-tasks/{id}/complete` both call this path so
        RELEASE_APPROVAL / QUALITY_APPROVAL always resume the orchestrator.
        """
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(HumanTaskModel)
                    .where(
                        HumanTaskModel.id == task_id,
                        HumanTaskModel.status == "OPEN",
                        HumanTaskModel.due_at > func.now(),
                    )
                    .values(
                        status="COMPLETED",
                        assigned_to=assigned_to,
                        response=response,
                        completed_at=func.now(),
                    )
                ),
            )
            if result.rowcount != 1:
                raise DomainError(ErrorCode.INVALID_STATE, "human task is unavailable or expired")

            row = await session.get(HumanTaskModel, task_id)
            if row is None:
                return
            await self._emit_gate_completion_events(
                session, row, assigned_to=assigned_to, response=response
            )

    @staticmethod
    def _response_approved(response: dict[str, Any]) -> bool:
        decision = str(response.get("decision", "")).upper()
        if decision == "APPROVE":
            return True
        if decision == "REJECT":
            return False
        return bool(response.get("approved", False))

    async def _emit_gate_completion_events(
        self,
        session: AsyncSession,
        row: HumanTaskModel,
        *,
        assigned_to: str,
        response: dict[str, Any],
    ) -> None:
        goal_id = row.goal_id
        task_id = row.id
        approved = self._response_approved(response)

        if row.task_type == "QUALITY_APPROVAL":
            feedback = str(response.get("feedback", ""))
            event_idempotency = make_idempotency_key(
                "quality_approval_completed", goal_id, str(task_id)
            )
            session.add(
                make_outbox_event(
                    EventEnvelope(
                        event_type=QUALITY_APPROVAL_COMPLETED,
                        aggregate_type="goal",
                        aggregate_id=goal_id,
                        aggregate_version=0,
                        payload={
                            "goal_id": str(goal_id),
                            "task_id": str(task_id),
                            "approved": approved,
                            "feedback": feedback,
                            "actor": assigned_to,
                        },
                        idempotency_key=event_idempotency,
                        correlation_id=uuid.uuid4(),
                    )
                )
            )

        if row.task_type == "RELEASE_APPROVAL":
            pending: dict[str, Any] = {}
            goal = await session.get(GoalModel, goal_id)
            if goal is not None:
                meta = dict(goal.metadata_json or {})
                pending = dict(meta.get("pending_release") or {})
                meta["awaiting_human_intervention"] = False
                summary = (
                    "已确认，正在继续部署预览"
                    if approved
                    else "已拒绝预览发布，等待后续处理"
                )
                goal.metadata_json = merge_live_action_into_metadata(
                    meta,
                    summary,
                    stage="RELEASE_APPROVED" if approved else "RELEASE_REJECTED",
                    event_type="RELEASE_APPROVAL_COMPLETED",
                )
                flag_modified(goal, "metadata_json")
            event_idempotency = make_idempotency_key(
                "release_approval_completed", goal_id, str(task_id)
            )
            session.add(
                make_outbox_event(
                    EventEnvelope(
                        event_type=RELEASE_APPROVAL_COMPLETED,
                        aggregate_type="goal",
                        aggregate_id=goal_id,
                        aggregate_version=0,
                        payload={
                            "goal_id": str(goal_id),
                            "task_id": str(task_id),
                            "approved": approved,
                            "actor": assigned_to,
                            "release_candidate_id": pending.get("release_candidate_id"),
                            "app_project_id": pending.get("app_project_id"),
                            "idempotency_key": pending.get("idempotency_key"),
                            "correlation_id": pending.get("correlation_id"),
                        },
                        idempotency_key=event_idempotency,
                        correlation_id=uuid.uuid4(),
                    )
                )
            )

        if row.task_type == "DELIVERY_GAP_INTERVENE" and approved:
            goal = await session.get(GoalModel, goal_id)
            project_id = None
            if goal is not None:
                meta = dict(goal.metadata_json or {})
                meta["awaiting_human_intervention"] = False
                project_id = goal.app_project_id
                goal.metadata_json = merge_live_action_into_metadata(
                    meta,
                    "已批准，正在重新规划并继续生成",
                    stage="GENERATING",
                    event_type=DELIVERY_GAP_HUMAN_APPROVED,
                )
                flag_modified(goal, "metadata_json")
            if project_id is not None:
                event_idempotency = make_idempotency_key(
                    "delivery_gap_human_approved", goal_id, str(task_id)
                )
                feedback = str(response.get("message") or response.get("feedback") or "")
                session.add(
                    make_outbox_event(
                        EventEnvelope(
                            event_type=DELIVERY_GAP_HUMAN_APPROVED,
                            aggregate_type="goal",
                            aggregate_id=goal_id,
                            aggregate_version=0,
                            payload={
                                "goal_id": str(goal_id),
                                "app_project_id": str(project_id),
                                "task_id": str(task_id),
                                "approved": True,
                                "actor": assigned_to,
                                "message": feedback[:400],
                                "idempotency_key": event_idempotency,
                            },
                            idempotency_key=event_idempotency,
                            correlation_id=uuid.uuid4(),
                        )
                    )
                )

    async def reemit_stuck_release_approval(
        self,
        goal_id: uuid.UUID,
        *,
        assigned_to: str,
    ) -> dict[str, str] | None:
        """Re-emit ReleaseApprovalCompleted when task is COMPLETED but deploy never resumed.

        Covers the pre-fix chat APPROVE path that marked the task done without outbox.
        """
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                return None
            meta = dict(goal.metadata_json or {})
            pending = dict(meta.get("pending_release") or {})
            task_id_raw = pending.get("human_task_id")
            if not task_id_raw:
                return None
            task_id = uuid.UUID(str(task_id_raw))
            row = await session.get(HumanTaskModel, task_id)
            if row is None or row.task_type != "RELEASE_APPROVAL" or row.status != "COMPLETED":
                return None
            candidate_raw = pending.get("release_candidate_id")
            if candidate_raw:
                candidate = await session.get(
                    ReleaseCandidateModel, uuid.UUID(str(candidate_raw))
                )
                if candidate is not None and candidate.status in {"APPROVED", "REJECTED"}:
                    return None
            response = dict(row.response or {})
            if "decision" not in response and response.get("approved"):
                response["decision"] = "APPROVE"
                row.response = response
                flag_modified(row, "response")
            await self._emit_gate_completion_events(
                session,
                row,
                assigned_to=assigned_to,
                response=response,
            )
            return {"task_id": str(task_id), "task_type": row.task_type}

    async def timeout_due(self) -> int:
        """Mark due OPEN tasks timed out and apply preference default decisions.

        Avoids dead-wait: RELEASE_APPROVAL / QUALITY_APPROVAL emit completion
        events with the timeout default (deny for balanced/conservative).
        """
        from regent.application.confirmation import TimeoutDefault, preference_timeout_default
        from regent.application.decision_policy import load_decision_policy_from_config
        from sqlalchemy import select

        applied = 0
        async with self._sessions() as session, session.begin():
            rows = (
                await session.execute(
                    select(HumanTaskModel).where(
                        HumanTaskModel.status == "OPEN",
                        HumanTaskModel.due_at <= func.now(),
                    )
                )
            ).scalars().all()
            if not rows:
                return 0
            try:
                policy = load_decision_policy_from_config()
                timeout_default = preference_timeout_default(policy.preference)
            except Exception:
                timeout_default = TimeoutDefault.DENY

            for row in rows:
                approved = timeout_default is TimeoutDefault.ALLOW
                # Safety / high-stakes approvals never auto-allow on timeout.
                if row.task_type in {"RELEASE_APPROVAL", "QUALITY_APPROVAL", "PERMIT_APPROVAL"}:
                    if timeout_default is not TimeoutDefault.ALLOW:
                        approved = False
                    # balanced/conservative → deny; aggressive may allow only
                    # when preference timeout default is allow AND not deny-listed.
                    from regent.config import get_settings

                    settings = get_settings()
                    deny = {
                        a.strip()
                        for a in (settings.decision_deny_actions or "").split(",")
                        if a.strip()
                    }
                    action_key = row.task_type.lower()
                    if action_key in deny or row.task_type.lower() in deny:
                        approved = False
                    if settings.decision_preference != "aggressive":
                        approved = False

                response = {
                    "approved": approved,
                    "decision": "APPROVE" if approved else "REJECT",
                    "reason": "timeout_default",
                    "default_on_timeout": timeout_default.value,
                }
                row.status = "COMPLETED"
                row.assigned_to = "regent-core:timeout-default"
                row.response = response
                row.completed_at = datetime.now(UTC)
                flag_modified(row, "response")
                await self._emit_gate_completion_events(
                    session,
                    row,
                    assigned_to="regent-core:timeout-default",
                    response=response,
                )
                applied += 1
        return applied
