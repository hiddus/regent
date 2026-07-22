import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.human_task_service import HumanTaskService
from regent.application.permit_service import PermitBinding, PermitService

router = APIRouter(prefix="/v1/governance", tags=["governance"])


class PermitRequest(BaseModel):
    goal_id: uuid.UUID
    work_id: uuid.UUID
    run_id: uuid.UUID
    actor_id: str = Field(min_length=1, max_length=255)
    action: str = Field(min_length=1, max_length=255)
    target: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    data_scope: dict[str, Any] = Field(default_factory=dict)
    network_scope: dict[str, Any] = Field(default_factory=dict)
    resource_limit: dict[str, Any] = Field(default_factory=dict)
    risk_level: str
    valid_until: datetime
    idempotency_key: str = Field(min_length=8, max_length=255)


class DecisionRequest(BaseModel):
    reason: str = Field(min_length=1)


class ClaimRequest(BaseModel):
    actor_id: str = Field(min_length=1)


class ConsumeRequest(BaseModel):
    nonce: uuid.UUID


class HumanTaskRequest(BaseModel):
    goal_id: uuid.UUID
    work_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    task_type: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    requested_by: str = Field(min_length=1)
    due_at: datetime


class HumanPermitDecisionRequest(BaseModel):
    assigned_to: str = Field(min_length=1)
    approved: bool
    reason: str = Field(min_length=1)


class HumanCompleteRequest(BaseModel):
    assigned_to: str = Field(min_length=1)
    response: dict[str, Any]


def permits(request: Request) -> PermitService:
    return PermitService(request.app.state.sessions)


def humans(request: Request) -> HumanTaskService:
    return HumanTaskService(request.app.state.sessions)


PermitDep = Annotated[PermitService, Depends(permits)]
HumanDep = Annotated[HumanTaskService, Depends(humans)]


@router.post("/permits", status_code=status.HTTP_201_CREATED)
async def request_permit(
    payload: PermitRequest, service: PermitDep, human_service: HumanDep
) -> dict[str, uuid.UUID | None]:
    permit_id = await service.request(PermitBinding(**payload.model_dump()))
    human_task_id = None
    if payload.risk_level not in {"NONE", "LOW"}:
        human_task_id = await human_service.create(
            goal_id=payload.goal_id,
            work_id=payload.work_id,
            run_id=payload.run_id,
            task_type="PERMIT_APPROVAL",
            prompt=f"Approve {payload.action} on {payload.target}?",
            requested_by=payload.actor_id,
            due_at=payload.valid_until,
        )
    return {"permit_id": permit_id, "human_task_id": human_task_id}


@router.post("/permits/{permit_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
async def approve_permit(
    permit_id: uuid.UUID, payload: DecisionRequest, service: PermitDep
) -> None:
    await service.approve(permit_id, payload.reason)


@router.post("/permits/{permit_id}/deny", status_code=status.HTTP_204_NO_CONTENT)
async def deny_permit(permit_id: uuid.UUID, payload: DecisionRequest, service: PermitDep) -> None:
    await service.deny(permit_id, payload.reason)


@router.post("/permits/{permit_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_permit(permit_id: uuid.UUID, payload: DecisionRequest, service: PermitDep) -> None:
    await service.revoke(permit_id, payload.reason)


@router.post("/permits/{permit_id}/claim")
async def claim_permit(
    permit_id: uuid.UUID, payload: ClaimRequest, service: PermitDep
) -> dict[str, Any]:
    claimed = await service.claim(permit_id, actor_id=payload.actor_id)
    return {"permit_id": claimed.id, "nonce": claimed.nonce, "binding": claimed.binding}


@router.post("/permits/{permit_id}/consume", status_code=status.HTTP_204_NO_CONTENT)
async def consume_permit(permit_id: uuid.UUID, payload: ConsumeRequest, service: PermitDep) -> None:
    await service.consume(permit_id, nonce=payload.nonce)


@router.post("/human-tasks", status_code=status.HTTP_201_CREATED)
async def create_human_task(payload: HumanTaskRequest, service: HumanDep) -> dict[str, uuid.UUID]:
    return {"human_task_id": await service.create(**payload.model_dump())}


@router.post("/human-tasks/{task_id}/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_human_task(
    task_id: uuid.UUID, payload: HumanCompleteRequest, service: HumanDep
) -> None:
    await service.complete(task_id, **payload.model_dump())


@router.post(
    "/human-tasks/{task_id}/permit/{permit_id}/decision",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def decide_permit_from_human_task(
    task_id: uuid.UUID,
    permit_id: uuid.UUID,
    payload: HumanPermitDecisionRequest,
    human_service: HumanDep,
    permit_service: PermitDep,
) -> None:
    await human_service.complete(
        task_id,
        assigned_to=payload.assigned_to,
        response={"approved": payload.approved, "reason": payload.reason},
    )
    if payload.approved:
        await permit_service.approve(permit_id, payload.reason)
    else:
        await permit_service.deny(permit_id, payload.reason)


class ReplayDeadLetterBody(BaseModel):
    actor: str = Field(min_length=1, max_length=255)
    reset_attempts: bool = True


@router.get("/outbox/dead-letters")
async def list_dead_letters(request: Request, limit: int = 50) -> dict[str, object]:
    from regent.application.outbox_dead_letter_service import OutboxDeadLetterService

    service = OutboxDeadLetterService(request.app.state.sessions)
    rows = await service.list_dead_letters(limit=min(limit, 200))
    return {
        "items": [
            {
                "id": str(row.id),
                "event_type": row.event_type,
                "attempt": row.attempt,
                "last_error": row.last_error,
                "correlation_id": str(row.correlation_id),
                "available_at": row.available_at.isoformat(),
                "occurred_at": row.occurred_at.isoformat(),
            }
            for row in rows
        ]
    }


@router.post("/outbox/dead-letters/{event_id}/replay")
async def replay_dead_letter(
    event_id: uuid.UUID, payload: ReplayDeadLetterBody, request: Request
) -> dict[str, object]:
    from regent.application.outbox_dead_letter_service import OutboxDeadLetterService

    service = OutboxDeadLetterService(request.app.state.sessions)
    receipt = await service.replay(
        event_id, actor=payload.actor, reset_attempts=payload.reset_attempts
    )
    return {
        "id": str(receipt.id),
        "status": receipt.status,
        "attempt": receipt.attempt,
        "replayed_by": receipt.replayed_by,
        "replayed_at": receipt.replayed_at.isoformat(),
    }
