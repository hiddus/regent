"""AAR-1 Durable AgentTask service — at-least-once delivery, lease fencing, UNKNOWN reconcile."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.aar1_models import AgentDeploymentModel, AgentTaskModel

TERMINAL = frozenset(
    {"SUCCEEDED", "FAILED_TERMINAL", "TIMED_OUT", "CANCELLED", "MANUAL_REVIEW"}
)
CLAIMABLE = frozenset({"OFFERED", "FAILED_RETRYABLE"})
UNKNOWN_RECONCILE_MINUTES = 15


@dataclass(frozen=True, slots=True)
class AgentTaskView:
    id: uuid.UUID
    status: str
    attempt: int
    lease_token: str | None
    result_ref: str | None
    error_code: str | None
    replayed: bool = False


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AgentTaskService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        lease_seconds: int = 30,
    ) -> None:
        self._sessions = sessions
        self._lease_seconds = lease_seconds

    async def offer_task(
        self,
        *,
        goal_id: uuid.UUID,
        organization_version_id: uuid.UUID,
        source_deployment_id: uuid.UUID,
        target_deployment_id: uuid.UUID,
        task_type: str,
        idempotency_key: str,
        payload_digest: str,
        capability_scope: list[str],
        correlation_id: str,
        work_id: uuid.UUID | None = None,
        parent_task_id: uuid.UUID | None = None,
        permit_refs: list[str] | None = None,
        payload_ref: str | None = None,
        causation_id: str | None = None,
        deadline_at: datetime | None = None,
        max_attempts: int = 5,
        session: AsyncSession | None = None,
    ) -> AgentTaskView:
        async def _run(s: AsyncSession) -> AgentTaskView:
            target = await s.get(AgentDeploymentModel, target_deployment_id)
            if target is None:
                raise DomainError(ErrorCode.NOT_FOUND, "target deployment not found")
            if target.status in {"SUSPENDED", "RETIRED", "FAILED"}:
                raise DomainError(
                    ErrorCode.INVALID_AGENT_LIFECYCLE_TRANSITION,
                    f"deployment {target.status} cannot accept tasks",
                )
            if target.goal_id != goal_id:
                raise DomainError(ErrorCode.CAPABILITY_SCOPE_ESCALATION, "cross-goal task denied")

            existing = await s.scalar(
                select(AgentTaskModel).where(
                    AgentTaskModel.target_deployment_id == target_deployment_id,
                    AgentTaskModel.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return AgentTaskView(
                    id=existing.id,
                    status=existing.status,
                    attempt=existing.attempt,
                    lease_token=existing.lease_token,
                    result_ref=existing.result_ref,
                    error_code=existing.error_code,
                    replayed=True,
                )

            task = AgentTaskModel(
                id=uuid.uuid4(),
                goal_id=goal_id,
                work_id=work_id,
                organization_version_id=organization_version_id,
                source_deployment_id=source_deployment_id,
                target_deployment_id=target_deployment_id,
                parent_task_id=parent_task_id,
                task_type=task_type,
                capability_scope=list(capability_scope),
                permit_refs=list(permit_refs or []),
                payload_ref=payload_ref,
                payload_digest=payload_digest,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                causation_id=causation_id,
                status="OFFERED",
                attempt=0,
                max_attempts=max_attempts,
                deadline_at=deadline_at,
            )
            s.add(task)
            await s.flush()
            return AgentTaskView(
                id=task.id,
                status=task.status,
                attempt=task.attempt,
                lease_token=None,
                result_ref=None,
                error_code=None,
            )

        if session is not None:
            return await _run(session)
        async with self._sessions() as s, s.begin():
            return await _run(s)

    async def claim_task(
        self,
        task_id: uuid.UUID,
        *,
        worker_id: str,
        session: AsyncSession | None = None,
    ) -> AgentTaskView:
        async def _run(s: AsyncSession) -> AgentTaskView:
            task = await s.get(AgentTaskModel, task_id, with_for_update=True)
            if task is None:
                raise DomainError(ErrorCode.NOT_FOUND, "agent task not found")
            now = _utcnow()
            lease_valid = (
                task.lease_token is not None
                and task.lease_expires_at is not None
                and task.lease_expires_at > now
            )
            if task.status in CLAIMABLE or (
                task.status in {"ACCEPTED", "RUNNING"} and not lease_valid
            ):
                token = secrets.token_urlsafe(24)
                task.status = "ACCEPTED"
                task.lease_owner = worker_id
                task.lease_token = token
                task.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
                task.attempt += 1
                await s.flush()
                return AgentTaskView(
                    id=task.id,
                    status=task.status,
                    attempt=task.attempt,
                    lease_token=token,
                    result_ref=task.result_ref,
                    error_code=task.error_code,
                )
            if lease_valid and task.lease_owner != worker_id:
                raise DomainError(ErrorCode.STALE_LEASE, "task leased by another worker")
            raise DomainError(ErrorCode.INVALID_STATE, f"cannot claim from {task.status}")

        if session is not None:
            return await _run(session)
        async with self._sessions() as s, s.begin():
            return await _run(s)

    def _require_lease(self, task: AgentTaskModel, lease_token: str) -> None:
        now = _utcnow()
        if task.lease_token != lease_token:
            raise DomainError(ErrorCode.STALE_LEASE, "lease token mismatch")
        if task.lease_expires_at is None or task.lease_expires_at <= now:
            raise DomainError(ErrorCode.STALE_LEASE, "lease expired")

    async def heartbeat(
        self, task_id: uuid.UUID, *, lease_token: str, worker_id: str
    ) -> AgentTaskView:
        async with self._sessions() as s, s.begin():
            task = await s.get(AgentTaskModel, task_id, with_for_update=True)
            if task is None:
                raise DomainError(ErrorCode.NOT_FOUND, "agent task not found")
            self._require_lease(task, lease_token)
            if task.lease_owner != worker_id:
                raise DomainError(ErrorCode.STALE_LEASE, "owner mismatch")
            task.lease_expires_at = _utcnow() + timedelta(seconds=self._lease_seconds)
            return AgentTaskView(
                id=task.id,
                status=task.status,
                attempt=task.attempt,
                lease_token=task.lease_token,
                result_ref=task.result_ref,
                error_code=task.error_code,
            )

    async def start_task(self, task_id: uuid.UUID, *, lease_token: str) -> AgentTaskView:
        async with self._sessions() as s, s.begin():
            task = await s.get(AgentTaskModel, task_id, with_for_update=True)
            if task is None:
                raise DomainError(ErrorCode.NOT_FOUND, "agent task not found")
            self._require_lease(task, lease_token)
            if task.status not in {"ACCEPTED", "RUNNING"}:
                raise DomainError(ErrorCode.INVALID_STATE, f"cannot start from {task.status}")
            task.status = "RUNNING"
            return AgentTaskView(
                id=task.id,
                status=task.status,
                attempt=task.attempt,
                lease_token=task.lease_token,
                result_ref=task.result_ref,
                error_code=task.error_code,
            )

    async def complete_task(
        self, task_id: uuid.UUID, *, lease_token: str, result_ref: str
    ) -> AgentTaskView:
        async with self._sessions() as s, s.begin():
            task = await s.get(AgentTaskModel, task_id, with_for_update=True)
            if task is None:
                raise DomainError(ErrorCode.NOT_FOUND, "agent task not found")
            if task.status == "SUCCEEDED" and task.result_ref == result_ref:
                return AgentTaskView(
                    id=task.id,
                    status=task.status,
                    attempt=task.attempt,
                    lease_token=task.lease_token,
                    result_ref=task.result_ref,
                    error_code=None,
                    replayed=True,
                )
            self._require_lease(task, lease_token)
            if task.status not in {"RUNNING", "ACCEPTED", "UNKNOWN", "RECONCILING"}:
                raise DomainError(ErrorCode.INVALID_STATE, f"cannot complete from {task.status}")
            task.status = "SUCCEEDED"
            task.result_ref = result_ref
            task.error_code = None
            task.lease_token = None
            task.lease_expires_at = None
            return AgentTaskView(
                id=task.id,
                status=task.status,
                attempt=task.attempt,
                lease_token=None,
                result_ref=result_ref,
                error_code=None,
            )

    async def fail_task(
        self,
        task_id: uuid.UUID,
        *,
        lease_token: str,
        error_code: str,
        retryable: bool = True,
    ) -> AgentTaskView:
        async with self._sessions() as s, s.begin():
            task = await s.get(AgentTaskModel, task_id, with_for_update=True)
            if task is None:
                raise DomainError(ErrorCode.NOT_FOUND, "agent task not found")
            self._require_lease(task, lease_token)
            if retryable and task.attempt < task.max_attempts:
                task.status = "FAILED_RETRYABLE"
                task.error_code = error_code
                task.lease_token = None
                task.lease_expires_at = None
                task.not_before = _utcnow() + timedelta(seconds=2**min(task.attempt, 6))
            else:
                task.status = "FAILED_TERMINAL"
                task.error_code = error_code
                task.lease_token = None
                task.lease_expires_at = None
            return AgentTaskView(
                id=task.id,
                status=task.status,
                attempt=task.attempt,
                lease_token=None,
                result_ref=task.result_ref,
                error_code=error_code,
            )

    async def mark_unknown(self, task_id: uuid.UUID, *, lease_token: str) -> AgentTaskView:
        """After side-effect dispatch with unclear result — do not blind-retry."""
        async with self._sessions() as s, s.begin():
            task = await s.get(AgentTaskModel, task_id, with_for_update=True)
            if task is None:
                raise DomainError(ErrorCode.NOT_FOUND, "agent task not found")
            self._require_lease(task, lease_token)
            task.status = "UNKNOWN"
            task.error_code = "EXTERNAL_EFFECT_UNKNOWN"
            return AgentTaskView(
                id=task.id,
                status=task.status,
                attempt=task.attempt,
                lease_token=task.lease_token,
                result_ref=task.result_ref,
                error_code=task.error_code,
            )

    async def reconcile_task(
        self,
        task_id: uuid.UUID,
        *,
        resolved_status: str,
        result_ref: str | None = None,
        error_code: str | None = None,
        actor: str = "reconciler",
    ) -> AgentTaskView:
        if resolved_status not in {
            "SUCCEEDED",
            "FAILED_TERMINAL",
            "FAILED_RETRYABLE",
            "MANUAL_REVIEW",
            "RECONCILING",
        }:
            raise DomainError(ErrorCode.INVALID_STATE, "invalid reconcile target")
        async with self._sessions() as s, s.begin():
            task = await s.get(AgentTaskModel, task_id, with_for_update=True)
            if task is None:
                raise DomainError(ErrorCode.NOT_FOUND, "agent task not found")
            if task.status not in {"UNKNOWN", "RECONCILING", "RUNNING"}:
                raise DomainError(ErrorCode.INVALID_STATE, f"cannot reconcile from {task.status}")
            task.status = resolved_status
            if result_ref is not None:
                task.result_ref = result_ref
            if error_code is not None:
                task.error_code = error_code
            if resolved_status in TERMINAL or resolved_status == "FAILED_RETRYABLE":
                task.lease_token = None
                task.lease_expires_at = None
            return AgentTaskView(
                id=task.id,
                status=task.status,
                attempt=task.attempt,
                lease_token=task.lease_token,
                result_ref=task.result_ref,
                error_code=task.error_code,
            )

    async def cancel_task(self, task_id: uuid.UUID, *, reason: str) -> AgentTaskView:
        async with self._sessions() as s, s.begin():
            task = await s.get(AgentTaskModel, task_id, with_for_update=True)
            if task is None:
                raise DomainError(ErrorCode.NOT_FOUND, "agent task not found")
            if task.status in TERMINAL:
                return AgentTaskView(
                    id=task.id,
                    status=task.status,
                    attempt=task.attempt,
                    lease_token=task.lease_token,
                    result_ref=task.result_ref,
                    error_code=task.error_code,
                    replayed=True,
                )
            task.status = "CANCELLED"
            task.error_code = reason
            task.lease_token = None
            task.lease_expires_at = None
            return AgentTaskView(
                id=task.id,
                status=task.status,
                attempt=task.attempt,
                lease_token=None,
                result_ref=task.result_ref,
                error_code=reason,
            )

    async def sweep_unknown_to_reconciling(self, *, limit: int = 100) -> list[uuid.UUID]:
        """NFR-03: UNKNOWN within 15 minutes enters RECONCILING or MANUAL_REVIEW."""
        cutoff = _utcnow() - timedelta(minutes=UNKNOWN_RECONCILE_MINUTES)
        async with self._sessions() as s, s.begin():
            rows = list(
                await s.scalars(
                    select(AgentTaskModel)
                    .where(
                        AgentTaskModel.status == "UNKNOWN",
                        AgentTaskModel.updated_at <= cutoff,
                    )
                    .limit(limit)
                    .with_for_update()
                )
            )
            ids: list[uuid.UUID] = []
            for task in rows:
                task.status = "RECONCILING"
                ids.append(task.id)
            return ids

    async def get(self, task_id: uuid.UUID) -> AgentTaskView:
        async with self._sessions() as s:
            task = await s.get(AgentTaskModel, task_id)
            if task is None:
                raise DomainError(ErrorCode.NOT_FOUND, "agent task not found")
            return AgentTaskView(
                id=task.id,
                status=task.status,
                attempt=task.attempt,
                lease_token=task.lease_token,
                result_ref=task.result_ref,
                error_code=task.error_code,
            )

    async def list_claimable(
        self, *, target_deployment_id: uuid.UUID, limit: int = 20
    ) -> list[AgentTaskView]:
        now = _utcnow()
        async with self._sessions() as s:
            rows = await s.scalars(
                select(AgentTaskModel)
                .where(
                    AgentTaskModel.target_deployment_id == target_deployment_id,
                    or_(
                        and_(
                            AgentTaskModel.status.in_(tuple(CLAIMABLE)),
                            or_(
                                AgentTaskModel.not_before.is_(None),
                                AgentTaskModel.not_before <= now,
                            ),
                        ),
                        and_(
                            AgentTaskModel.status.in_(("ACCEPTED", "RUNNING")),
                            or_(
                                AgentTaskModel.lease_expires_at.is_(None),
                                AgentTaskModel.lease_expires_at <= now,
                            ),
                        ),
                    ),
                )
                .limit(limit)
            )
            return [
                AgentTaskView(
                    id=t.id,
                    status=t.status,
                    attempt=t.attempt,
                    lease_token=t.lease_token,
                    result_ref=t.result_ref,
                    error_code=t.error_code,
                )
                for t in rows
            ]


# Crash-window helpers for tests (six points: offer/claim/start/dispatch/complete + lease)
CRASH_WINDOWS = (
    "after_offer",
    "after_claim",
    "after_start",
    "after_dispatch",
    "before_complete",
    "after_complete",
)


def recover_after_crash(status_before: str, *, dispatched: bool) -> str:
    """Deterministic recovery decision after worker kill."""
    if status_before == "OFFERED":
        return "OFFERED"  # reclaimable
    if status_before in {"ACCEPTED", "RUNNING"} and not dispatched:
        return "FAILED_RETRYABLE"  # lease expiry → retry
    if status_before in {"ACCEPTED", "RUNNING"} and dispatched:
        return "UNKNOWN"  # must reconcile, no blind retry
    if status_before == "SUCCEEDED":
        return "SUCCEEDED"
    return status_before
