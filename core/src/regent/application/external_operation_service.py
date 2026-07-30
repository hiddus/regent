"""G0 ExternalOperation service — durable dispatch rights before network I/O."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.provider_capability import (
    PROVIDER_CAPABILITY_MATRIX,
    ProviderCapability,
    ProviderCapabilityProfile,
    require_auto_irreversible,
)
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    DeploymentModel,
    ExecutionPermitModel,
    ExternalOperationModel,
)

_RECONCILE_TIMEOUT_MINUTES = 15


@dataclass(frozen=True, slots=True)
class PreparedExternalOperation:
    id: uuid.UUID
    operation_key: str
    local_fencing_token: uuid.UUID
    status: str


@dataclass(frozen=True, slots=True)
class DispatchingExternalOperation:
    id: uuid.UUID
    operation_key: str
    dispatch_generation: int
    local_fencing_token: uuid.UUID
    status: str


def request_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ExternalOperationService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_by_operation_key(self, operation_key: str) -> ExternalOperationModel | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(ExternalOperationModel).where(
                    ExternalOperationModel.operation_key == operation_key
                )
            )

    async def get(self, operation_id: uuid.UUID) -> ExternalOperationModel:
        async with self._sessions() as session:
            model = await session.get(ExternalOperationModel, operation_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "external operation not found")
            return model

    async def prepare(
        self,
        *,
        operation_key: str,
        provider: str,
        action: str,
        permit_id: uuid.UUID,
        local_fencing_token: uuid.UUID,
        payload: dict[str, Any],
        goal_id: uuid.UUID | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> PreparedExternalOperation:
        require_auto_irreversible(provider)
        digest = request_digest(payload)
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(ExternalOperationModel).where(
                    ExternalOperationModel.operation_key == operation_key
                )
            )
            if existing is not None:
                return PreparedExternalOperation(
                    existing.id,
                    existing.operation_key,
                    existing.local_fencing_token,
                    existing.status,
                )
            permit = await session.get(ExecutionPermitModel, permit_id)
            if permit is None or permit.status != "CLAIMED":
                raise DomainError(ErrorCode.INVALID_STATE, "permit must be CLAIMED to prepare EO")
            if permit.nonce != local_fencing_token:
                raise DomainError(ErrorCode.INVALID_STATE, "local fencing token mismatch")
            eo_id = uuid.uuid4()
            session.add(
                ExternalOperationModel(
                    id=eo_id,
                    operation_key=operation_key,
                    provider=provider,
                    action=action,
                    status="PREPARED",
                    request_digest=digest,
                    permit_id=permit_id,
                    local_fencing_token=local_fencing_token,
                    dispatch_generation=0,
                    goal_id=goal_id,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                    result_summary={"payload_keys": sorted(payload.keys())},
                )
            )
        return PreparedExternalOperation(eo_id, operation_key, local_fencing_token, "PREPARED")

    async def begin_dispatch(
        self,
        operation_id: uuid.UUID,
        *,
        worker_lease_token: str,
        expected_fencing_token: uuid.UUID,
    ) -> DispatchingExternalOperation:
        """Atomically mark DISPATCHING and CONSUME permit — no network I/O in this method."""
        async with self._sessions() as session, session.begin():
            eo = await session.get(ExternalOperationModel, operation_id, with_for_update=True)
            if eo is None:
                raise DomainError(ErrorCode.NOT_FOUND, "external operation not found")
            if eo.local_fencing_token != expected_fencing_token:
                raise DomainError(ErrorCode.INVALID_STATE, "stale local fencing token")
            if eo.status == "DISPATCHING" and eo.dispatch_generation > 0:
                return DispatchingExternalOperation(
                    eo.id,
                    eo.operation_key,
                    eo.dispatch_generation,
                    eo.local_fencing_token,
                    eo.status,
                )
            if eo.status != "PREPARED":
                raise DomainError(
                    ErrorCode.INVALID_STATE, f"cannot begin dispatch from {eo.status}"
                )
            permit = await session.get(ExecutionPermitModel, eo.permit_id, with_for_update=True)
            if permit is None or permit.status != "CLAIMED":
                raise DomainError(ErrorCode.INVALID_STATE, "permit not CLAIMED for dispatch")
            if permit.nonce != eo.local_fencing_token:
                raise DomainError(ErrorCode.INVALID_STATE, "permit fencing mismatch")
            eo.status = "DISPATCHING"
            eo.dispatch_generation = 1
            eo.worker_lease_token = worker_lease_token
            permit.status = "CONSUMED"
            permit.consumed_at = datetime.now(UTC)
            await session.flush()
            return DispatchingExternalOperation(
                eo.id, eo.operation_key, eo.dispatch_generation, eo.local_fencing_token, eo.status
            )

    async def mark_succeeded(
        self, operation_id: uuid.UUID, *, external_id: str, summary: dict[str, Any] | None = None
    ) -> None:
        await self._terminal(operation_id, "SUCCEEDED", external_id=external_id, summary=summary)

    async def mark_failed_terminal(
        self, operation_id: uuid.UUID, *, failure_code: str, summary: dict[str, Any] | None = None
    ) -> None:
        await self._terminal(
            operation_id, "FAILED_TERMINAL", failure_code=failure_code, summary=summary
        )

    async def mark_unknown(self, operation_id: uuid.UUID, *, reason: str) -> None:
        await self._terminal(operation_id, "UNKNOWN", failure_code=reason)

    async def begin_reconcile(self, operation_id: uuid.UUID) -> None:
        async with self._sessions() as session, session.begin():
            eo = await session.get(ExternalOperationModel, operation_id, with_for_update=True)
            if eo is None:
                raise DomainError(ErrorCode.NOT_FOUND, "external operation not found")
            if eo.status != "UNKNOWN":
                raise DomainError(ErrorCode.INVALID_STATE, "reconcile requires UNKNOWN")
            eo.status = "RECONCILING"

    async def resolve_reconcile(
        self,
        operation_id: uuid.UUID,
        *,
        status: str,
        external_id: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        if status not in {"SUCCEEDED", "FAILED_TERMINAL", "MANUAL_REVIEW"}:
            raise ValueError("invalid reconcile status")
        async with self._sessions() as session, session.begin():
            eo = await session.get(ExternalOperationModel, operation_id, with_for_update=True)
            if eo is None:
                raise DomainError(ErrorCode.NOT_FOUND, "external operation not found")
            if eo.status != "RECONCILING":
                raise DomainError(ErrorCode.INVALID_STATE, "resolve requires RECONCILING")
            eo.status = status
            eo.reconciled_at = datetime.now(UTC)
            if external_id:
                eo.external_id = external_id
            if summary:
                eo.result_summary = {**dict(eo.result_summary or {}), **summary}

    async def reconcile_stale_unknowns(
        self,
        *,
        now: datetime | None = None,
        timeout_minutes: int = _RECONCILE_TIMEOUT_MINUTES,
    ) -> list[uuid.UUID]:
        """Scan DISPATCHING/UNKNOWN EOs that exceeded timeout and begin reconciliation.

        Returns list of EO ids that were transitioned to RECONCILING.
        """
        clock = now or datetime.now(UTC)
        cutoff = clock - timedelta(minutes=timeout_minutes)
        reconciled: list[uuid.UUID] = []

        async with self._sessions() as session, session.begin():
            stale = await session.scalars(
                select(ExternalOperationModel).where(
                    ExternalOperationModel.status.in_(
                        ("DISPATCHING", "UNKNOWN")
                    ),
                    ExternalOperationModel.updated_at < cutoff,
                )
            )
            for eo in stale.all():
                eo.status = "RECONCILING"
                attempts = dict(eo.reconcile_attempts or {})
                attempt_key = clock.isoformat()
                attempts[attempt_key] = "auto_stale_scan"
                eo.reconcile_attempts = attempts
                if eo.reconcile_deadline is None:
                    eo.reconcile_deadline = clock + timedelta(minutes=timeout_minutes)
                reconciled.append(eo.id)

        return reconciled

    def query_provider(self, provider: str) -> ProviderCapabilityProfile | None:
        """Return the capability profile for *provider*, or None if unregistered."""
        return PROVIDER_CAPABILITY_MATRIX.get(provider)

    async def resolve_reconciling_via_query(
        self,
        *,
        now: datetime | None = None,
        limit: int = 50,
    ) -> list[uuid.UUID]:
        """G0: for RECONCILING EOs, probe durable provider state and resolve.

        Uses capability matrix QUERY_* flags. Static preview probes Deployment rows
        (durable surrogate for in-process provider memory). Past deadline without
        a conclusive probe → MANUAL_REVIEW.
        """
        clock = now or datetime.now(UTC)
        resolved: list[uuid.UUID] = []
        async with self._sessions() as session:
            rows = list(
                await session.scalars(
                    select(ExternalOperationModel)
                    .where(ExternalOperationModel.status == "RECONCILING")
                    .order_by(ExternalOperationModel.updated_at.asc())
                    .limit(limit)
                )
            )
        for eo in rows:
            profile = self.query_provider(eo.provider)
            can_query = profile is not None and (
                ProviderCapability.QUERY_BY_OPERATION_KEY in profile.capabilities
                or ProviderCapability.QUERY_BY_EXTERNAL_ID in profile.capabilities
            )
            outcome = await self._probe_durable_outcome(eo) if can_query else None
            if outcome in {"SUCCEEDED", "FAILED_TERMINAL"}:
                await self.resolve_reconcile(
                    eo.id,
                    status=outcome,
                    external_id=eo.external_id,
                    summary={"resolve_path": "provider_query", "probed_at": clock.isoformat()},
                )
                resolved.append(eo.id)
                continue
            deadline = eo.reconcile_deadline
            if deadline is not None and clock >= deadline:
                await self.resolve_reconcile(
                    eo.id,
                    status="MANUAL_REVIEW",
                    summary={
                        "resolve_path": "deadline_exceeded",
                        "can_query": bool(can_query),
                        "probed_at": clock.isoformat(),
                    },
                )
                resolved.append(eo.id)
        return resolved

    async def _probe_durable_outcome(self, eo: ExternalOperationModel) -> str | None:
        """Return SUCCEEDED / FAILED_TERMINAL / None from durable local state."""
        async with self._sessions() as session:
            deployment: DeploymentModel | None = None
            if eo.external_id:
                deployment = await session.scalar(
                    select(DeploymentModel).where(
                        DeploymentModel.external_deployment_id == eo.external_id
                    )
                )
            if deployment is None and eo.operation_key.startswith("preview-deploy:"):
                idem = eo.operation_key.removeprefix("preview-deploy:")
                deployment = await session.scalar(
                    select(DeploymentModel).where(DeploymentModel.idempotency_key == idem)
                )
            if deployment is None and eo.operation_key.startswith("preview-rollback:"):
                # rollback keys: preview-rollback:{idempotency}:{permit}
                parts = eo.operation_key.split(":", 2)
                if len(parts) >= 2:
                    deployment = await session.scalar(
                        select(DeploymentModel).where(
                            DeploymentModel.idempotency_key == parts[1]
                        )
                    )
            if deployment is None:
                return None
            if deployment.status == "SUCCEEDED":
                return "SUCCEEDED"
            if deployment.status in {"FAILED", "ROLLED_BACK"}:
                return "FAILED_TERMINAL"
            return None

    async def _terminal(
        self,
        operation_id: uuid.UUID,
        status: str,
        *,
        external_id: str | None = None,
        failure_code: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(ExternalOperationModel)
                .where(
                    ExternalOperationModel.id == operation_id,
                    ExternalOperationModel.status.in_(("DISPATCHING", "UNKNOWN", "RECONCILING")),
                )
                .values(
                    status=status,
                    external_id=external_id,
                    failure_code=failure_code,
                    result_summary=summary or {},
                )
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise DomainError(ErrorCode.INVALID_STATE, f"cannot mark {status}")
