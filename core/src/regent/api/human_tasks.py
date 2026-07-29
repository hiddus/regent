"""HumanTask API — list and complete pending human tasks."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from regent.application.execution_events import (
    QUALITY_APPROVAL_COMPLETED,
    EventEnvelope,
    make_idempotency_key,
    make_outbox_event,
)
from regent.application.human_task_service import HumanTaskService
from regent.domain.errors import DomainError
from regent.infrastructure.models import HumanTaskModel

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

    For QUALITY_APPROVAL tasks, emits a QUALITY_APPROVAL_COMPLETED event
    so the orchestrator can ACHIEVE or EXHAUST the goal.
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

        # GAC-Q1: Emit event for quality approval tasks
        if row.task_type == "QUALITY_APPROVAL":
            approved = bool(payload.response.get("approved", True))
            feedback = str(payload.response.get("feedback", ""))
            goal_id = row.goal_id
            event_idempotency = make_idempotency_key(
                "quality_approval_completed", goal_id, str(task_id)
            )
            outbox_event = make_outbox_event(
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
                        "actor": payload.assigned_to,
                    },
                    idempotency_key=event_idempotency,
                    correlation_id=uuid.uuid4(),
                )
            )
            session.add(outbox_event)

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
