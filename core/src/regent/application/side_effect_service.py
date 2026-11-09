import uuid
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import ExecutionPermitModel, SideEffectAttemptModel


@dataclass(frozen=True, slots=True)
class AttemptReceipt:
    id: uuid.UUID
    permit_id: uuid.UUID
    run_id: uuid.UUID
    status: str
    idempotency_key: str
    external_request_id: str | None
    result: dict[str, Any] | None
    reconciliation_evidence: dict[str, Any] | None
    replayed: bool


class SideEffectService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def start(self, permit_id: uuid.UUID) -> AttemptReceipt:
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(SideEffectAttemptModel).where(SideEffectAttemptModel.permit_id == permit_id)
            )
            if existing is not None:
                return self._receipt(existing, replayed=True)
            permit = await session.get(ExecutionPermitModel, permit_id, with_for_update=True)
            if permit is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"permit {permit_id} not found")
            if permit.status != "CLAIMED":
                raise DomainError(
                    ErrorCode.PERMIT_INVALID, "permit must be claimed before execution"
                )
            attempt = SideEffectAttemptModel(
                id=uuid.uuid4(),
                permit_id=permit.id,
                run_id=permit.run_id,
                idempotency_key=permit.idempotency_key,
                status="STARTED",
            )
            session.add(attempt)
            await session.flush()
            return self._receipt(attempt, replayed=False)

    async def complete(
        self,
        attempt_id: uuid.UUID,
        *,
        outcome: str,
        external_request_id: str | None,
        result: dict[str, Any],
    ) -> AttemptReceipt:
        if outcome not in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
            raise ValueError("invalid side effect outcome")
        async with self._sessions() as session, session.begin():
            execution = cast(
                CursorResult[Any],
                await session.execute(
                    update(SideEffectAttemptModel)
                    .where(
                        SideEffectAttemptModel.id == attempt_id,
                        SideEffectAttemptModel.status == "STARTED",
                    )
                    .values(
                        status=outcome,
                        external_request_id=external_request_id,
                        result=result,
                        finished_at=func.now(),
                    )
                    .returning(SideEffectAttemptModel)
                ),
            )
            attempt = execution.scalar_one_or_none()
            if attempt is None:
                raise DomainError(ErrorCode.INVALID_STATE, "attempt already has an outcome")
            return self._receipt(attempt, replayed=False)

    async def reconcile(
        self,
        attempt_id: uuid.UUID,
        *,
        final_outcome: str,
        evidence: dict[str, Any],
    ) -> AttemptReceipt:
        if final_outcome not in {"SUCCEEDED", "FAILED"}:
            raise ValueError("reconciliation outcome must be final")
        async with self._sessions() as session, session.begin():
            execution = cast(
                CursorResult[Any],
                await session.execute(
                    update(SideEffectAttemptModel)
                    .where(
                        SideEffectAttemptModel.id == attempt_id,
                        SideEffectAttemptModel.status == "UNKNOWN",
                    )
                    .values(
                        status="RECONCILED",
                        result={"final_outcome": final_outcome},
                        reconciliation_evidence=evidence,
                        reconciled_at=func.now(),
                    )
                    .returning(SideEffectAttemptModel)
                ),
            )
            attempt = execution.scalar_one_or_none()
            if attempt is None:
                raise DomainError(
                    ErrorCode.RECONCILIATION_REQUIRED,
                    "only an unknown attempt can be reconciled exactly once",
                )
            return self._receipt(attempt, replayed=False)

    @staticmethod
    def _receipt(attempt: SideEffectAttemptModel, *, replayed: bool) -> AttemptReceipt:
        return AttemptReceipt(
            id=attempt.id,
            permit_id=attempt.permit_id,
            run_id=attempt.run_id,
            status=attempt.status,
            idempotency_key=attempt.idempotency_key,
            external_request_id=attempt.external_request_id,
            result=attempt.result,
            reconciliation_evidence=attempt.reconciliation_evidence,
            replayed=replayed,
        )
