"""HumanTask API — list and complete pending human tasks."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from regent.application.confirmation_present import action_key_for_task_type
from regent.application.human_task_service import HumanTaskService
from regent.domain.errors import DomainError
from regent.infrastructure.models import (
    ConversationMessageModel,
    ConversationModel,
    GoalModel,
    HumanTaskModel,
)

router = APIRouter(prefix="/v1", tags=["human-tasks"])


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def human_task_service(request: Request) -> HumanTaskService:
    return HumanTaskService(request.app.state.sessions)


HumanTaskServiceDep = Annotated[HumanTaskService, Depends(human_task_service)]


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class HumanTaskResponse(BaseModel):
    id: uuid.UUID
    goal_id: uuid.UUID
    work_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    task_type: str
    prompt: str
    status: str
    assigned_to: str | None = None
    response: dict[str, Any] | None = None
    due_at: datetime
    completed_at: datetime | None = None
    created_at: datetime


class CompleteHumanTaskBody(BaseModel):
    assigned_to: str = Field(min_length=1, max_length=255)
    response: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/goals/{goal_id}/human-tasks",
    response_model=list[HumanTaskResponse],
)
async def list_human_tasks(
    goal_id: uuid.UUID,
    request: Request,
    status_filter: str | None = None,
) -> list[HumanTaskResponse]:
    """List human tasks for a goal. Defaults to OPEN tasks only."""
    async with request.app.state.sessions() as session:
        stmt = select(HumanTaskModel).where(HumanTaskModel.goal_id == goal_id)
        if status_filter is not None:
            stmt = stmt.where(HumanTaskModel.status == status_filter)
        else:
            stmt = stmt.where(HumanTaskModel.status == "OPEN")
        stmt = stmt.order_by(HumanTaskModel.created_at.desc())
        rows = (await session.execute(stmt)).scalars().all()
        return [
            HumanTaskResponse(
                id=row.id,
                goal_id=row.goal_id,
                work_id=row.work_id,
                run_id=row.run_id,
                task_type=row.task_type,
                prompt=row.prompt,
                status=row.status,
                assigned_to=row.assigned_to,
                response=row.response,
                due_at=row.due_at,
                completed_at=row.completed_at,
                created_at=row.created_at,
            )
            for row in rows
        ]


@router.post(
    "/human-tasks/{task_id}/complete",
    response_model=HumanTaskResponse,
)
async def complete_human_task(
    task_id: uuid.UUID,
    payload: CompleteHumanTaskBody,
    request: Request,
) -> HumanTaskResponse:
    """Complete a pending human task.

    Side effects (outbox gate events + live_action) live in HumanTaskService.complete
    so chat APPROVE and this HTTP path behave the same for RELEASE/QUALITY gates.
    """
    service = HumanTaskService(request.app.state.sessions)
    try:
        await service.complete(
            task_id,
            assigned_to=payload.assigned_to,
            response=payload.response,
        )
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.message,
        ) from exc

    async with request.app.state.sessions() as session:
        row = await session.get(HumanTaskModel, task_id)
        if row is None:
            raise HTTPException(status_code=404, detail="human task not found")

        # Record a chat-surface result so progress nodes / TaskCard clear after refresh.
        decision = str(payload.response.get("decision", "")).upper()
        approved = decision == "APPROVE" or bool(payload.response.get("approved", False))
        if decision == "REJECT":
            approved = False
        goal = await session.get(GoalModel, row.goal_id)
        project_id = goal.app_project_id if goal is not None else None

        # CD-3.5: "总是允许" persistence — record the action in goal metadata so future
        # confirmations for the same action can be auto-resolved by decision_policy.
        always_allow = bool(
            payload.response.get("always_allow") or payload.response.get("always")
        )
        metadata_dirty = False
        if always_allow and goal is not None:
            metadata = dict(goal.metadata_json or {})
            allow_actions = list(metadata.get("decision_allow_actions") or [])
            action_key = action_key_for_task_type(row.task_type)
            if action_key not in allow_actions:
                allow_actions.append(action_key)
                metadata["decision_allow_actions"] = allow_actions
                goal.metadata_json = metadata
                flag_modified(goal, "metadata_json")
                metadata_dirty = True

        if project_id is not None:
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
                content = (
                    f"已批准任务: {row.task_type}"
                    if approved
                    else f"已拒绝任务: {row.task_type}"
                )
                session.add(
                    ConversationMessageModel(
                        id=uuid.uuid4(),
                        conversation_id=conversation.id,
                        ordinal=(last or 0) + 1,
                        role="ASSISTANT",
                        message_type="APPROVE_RESULT" if approved else "REJECT_RESULT",
                        content=content,
                        metadata_json={
                            "task_id": str(task_id),
                            "task_type": row.task_type,
                            "approved": approved,
                            "source": "human-tasks-api",
                        },
                        created_by=payload.assigned_to,
                    )
                )
                await session.commit()
            elif metadata_dirty:
                await session.commit()
        elif metadata_dirty:
            await session.commit()

        return HumanTaskResponse(
            id=row.id,
            goal_id=row.goal_id,
            work_id=row.work_id,
            run_id=row.run_id,
            task_type=row.task_type,
            prompt=row.prompt,
            status=row.status,
            assigned_to=row.assigned_to,
            response=row.response,
            due_at=row.due_at,
            completed_at=row.completed_at,
            created_at=row.created_at,
        )
