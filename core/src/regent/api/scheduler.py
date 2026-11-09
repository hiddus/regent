"""P2-1 scheduler HTTP API."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.scheduler_service import (
    DEFAULT_PRICE_BOOK,
    EnqueueWork,
    EnsureQuota,
    ScheduleOnce,
    SchedulerService,
)

router = APIRouter(tags=["scheduler"])


def service(request: Request) -> SchedulerService:
    return SchedulerService(request.app.state.sessions)


ServiceDep = Annotated[SchedulerService, Depends(service)]


class EnqueueBody(BaseModel):
    goal_id: uuid.UUID
    work_id: uuid.UUID | None = None
    org_key: str = Field(min_length=1, max_length=128)
    base_priority: int = 0
    resource_request: dict[str, int] = Field(default_factory=lambda: {"cpu": 1})
    actor: str = Field(default="regent-core", min_length=1)


class EnsureQuotaBody(BaseModel):
    org_key: str = Field(min_length=1, max_length=128)
    resource_name: str = Field(min_length=1, max_length=64)
    limit_amount: int = Field(ge=0)
    price_book_version: str = DEFAULT_PRICE_BOOK


class ScheduleBody(BaseModel):
    org_key: str = Field(min_length=1, max_length=128)
    actor: str = Field(min_length=1)
    price_book_version: str = DEFAULT_PRICE_BOOK
    policy_version: str = "goal-priority-v1"


class ReleaseBody(BaseModel):
    actor: str = Field(min_length=1)


class TickBody(BaseModel):
    org_key: str = Field(min_length=1, max_length=128)
    actor: str = Field(default="worker:scheduler", min_length=1)


class PreemptBody(BaseModel):
    org_key: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=255)
    actor: str = Field(min_length=1)


class CheckpointBody(BaseModel):
    org_key: str = Field(min_length=1, max_length=128)
    decision_id: uuid.UUID
    actor: str = Field(min_length=1)


class ResumeBody(BaseModel):
    actor: str = Field(min_length=1)


@router.post("/v1/scheduler/quotas", status_code=status.HTTP_201_CREATED)
async def ensure_quota(payload: EnsureQuotaBody, scheduler: ServiceDep) -> dict[str, Any]:
    model = await scheduler.ensure_quota(EnsureQuota(**payload.model_dump()))
    return {
        "id": str(model.id),
        "org_key": model.org_key,
        "resource_name": model.resource_name,
        "price_book_version": model.price_book_version,
        "limit_amount": model.limit_amount,
        "held_amount": model.held_amount,
    }


@router.post("/v1/scheduler/queue", status_code=status.HTTP_201_CREATED)
async def enqueue(payload: EnqueueBody, scheduler: ServiceDep) -> dict[str, Any]:
    model = await scheduler.enqueue(EnqueueWork(**payload.model_dump()))
    return {
        "id": str(model.id),
        "goal_id": str(model.goal_id),
        "work_id": str(model.work_id) if model.work_id else None,
        "org_key": model.org_key,
        "status": model.status,
        "base_priority": model.base_priority,
        "aging_score": model.aging_score,
    }


@router.get("/v1/scheduler/queue")
async def list_queue(org_key: str, scheduler: ServiceDep) -> list[dict[str, Any]]:
    rows = await scheduler.list_queue(org_key)
    return [
        {
            "id": str(row.id),
            "goal_id": str(row.goal_id),
            "work_id": str(row.work_id) if row.work_id else None,
            "status": row.status,
            "base_priority": row.base_priority,
            "aging_score": row.aging_score,
            "enqueued_at": row.enqueued_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/v1/scheduler/schedule", status_code=status.HTTP_201_CREATED)
async def schedule_once(payload: ScheduleBody, scheduler: ServiceDep) -> dict[str, Any]:
    decision = await scheduler.schedule_once(ScheduleOnce(**payload.model_dump()))
    replay = SchedulerService.replay_hashes(decision)
    return {
        "id": str(decision.id),
        "policy_version": decision.policy_version,
        "price_book_version": decision.price_book_version,
        "queue_snapshot_hash": decision.queue_snapshot_hash,
        "quota_snapshot_hash": decision.quota_snapshot_hash,
        "output": decision.output_json,
        "replay": replay,
    }


@router.get("/v1/scheduler/decisions/{decision_id}")
async def get_decision(decision_id: uuid.UUID, scheduler: ServiceDep) -> dict[str, Any]:
    decision = await scheduler.get_decision(decision_id)
    return {
        "id": str(decision.id),
        "policy_version": decision.policy_version,
        "price_book_version": decision.price_book_version,
        "queue_snapshot_hash": decision.queue_snapshot_hash,
        "quota_snapshot_hash": decision.quota_snapshot_hash,
        "input_snapshot": decision.input_snapshot_json,
        "output": decision.output_json,
        "replay": SchedulerService.replay_hashes(decision),
        "created_by": decision.created_by,
        "created_at": decision.created_at.isoformat(),
    }


@router.post("/v1/scheduler/reservations/{reservation_id}/release")
async def release_reservation(
    reservation_id: uuid.UUID, payload: ReleaseBody, scheduler: ServiceDep
) -> dict[str, Any]:
    model = await scheduler.release_reservation(reservation_id, actor=payload.actor)
    return {
        "id": str(model.id),
        "status": model.status,
        "resource_name": model.resource_name,
        "amount": model.amount,
    }


@router.post("/v1/scheduler/tick")
async def tick(payload: TickBody, scheduler: ServiceDep) -> dict[str, Any]:
    return await scheduler.tick(org_key=payload.org_key, actor=payload.actor)


@router.post("/v1/scheduler/queue/{entry_id}/preempt", status_code=status.HTTP_201_CREATED)
async def preempt(
    entry_id: uuid.UUID, payload: PreemptBody, scheduler: ServiceDep
) -> dict[str, Any]:
    model = await scheduler.preempt(
        org_key=payload.org_key,
        queue_entry_id=entry_id,
        reason=payload.reason,
        actor=payload.actor,
    )
    return {
        "id": str(model.id),
        "queue_entry_id": str(model.queue_entry_id),
        "reservation_id": str(model.reservation_id) if model.reservation_id else None,
        "reason": model.reason,
        "safe": model.safe,
        "created_by": model.created_by,
    }


@router.post("/v1/scheduler/checkpoints", status_code=status.HTTP_201_CREATED)
async def save_checkpoint(payload: CheckpointBody, scheduler: ServiceDep) -> dict[str, Any]:
    model = await scheduler.save_checkpoint(
        org_key=payload.org_key,
        decision_id=payload.decision_id,
        actor=payload.actor,
    )
    return {
        "id": str(model.id),
        "org_key": model.org_key,
        "scheduling_decision_id": str(model.scheduling_decision_id),
        "input_snapshot_hash": model.input_snapshot_hash,
        "created_by": model.created_by,
    }


@router.post("/v1/scheduler/checkpoints/{checkpoint_id}/resume")
async def resume_checkpoint(
    checkpoint_id: uuid.UUID, payload: ResumeBody, scheduler: ServiceDep
) -> dict[str, Any]:
    entry = await scheduler.resume_from_checkpoint(checkpoint_id, actor=payload.actor)
    return {
        "id": str(entry.id),
        "status": entry.status,
        "aging_score": entry.aging_score,
        "goal_id": str(entry.goal_id),
    }
