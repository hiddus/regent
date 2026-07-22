import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import ExecutionPermitModel


@dataclass(frozen=True, slots=True)
class PermitBinding:
    goal_id: uuid.UUID
    work_id: uuid.UUID
    run_id: uuid.UUID
    actor_id: str
    action: str
    target: str
    parameters: dict[str, Any]
    data_scope: dict[str, Any]
    network_scope: dict[str, Any]
    resource_limit: dict[str, Any]
    risk_level: str
    valid_until: datetime
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ClaimedPermit:
    id: uuid.UUID
    nonce: uuid.UUID
    binding: PermitBinding


class PermitService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def request(self, binding: PermitBinding) -> uuid.UUID:
        if binding.valid_until.tzinfo is None:
            raise ValueError("valid_until must be timezone-aware")
        async with self._sessions() as session, session.begin():
            # Idempotency: return existing permit if key already used
            existing = await session.scalar(
                select(ExecutionPermitModel).where(
                    ExecutionPermitModel.idempotency_key == binding.idempotency_key
                )
            )
            if existing is not None:
                return existing.id
            permit_id = uuid.uuid4()
            status = "APPROVED" if binding.risk_level in {"NONE", "LOW"} else "REQUESTED"
            reason = "auto-approved by bounded low-risk policy" if status == "APPROVED" else None
            session.add(
                ExecutionPermitModel(
                    id=permit_id,
                    goal_id=binding.goal_id,
                    work_id=binding.work_id,
                    run_id=binding.run_id,
                    actor_id=binding.actor_id,
                    action=binding.action,
                    target=binding.target,
                    parameter_hash=self.parameter_hash(binding.parameters),
                    data_scope=binding.data_scope,
                    network_scope=binding.network_scope,
                    resource_limit=binding.resource_limit,
                    risk_level=binding.risk_level,
                    status=status,
                    nonce=uuid.uuid4(),
                    idempotency_key=binding.idempotency_key,
                    valid_until=binding.valid_until,
                    decision_reason=reason,
                )
            )
        return permit_id

    async def approve(self, permit_id: uuid.UUID, reason: str) -> None:
        await self._change(permit_id, from_status="REQUESTED", to_status="APPROVED", reason=reason)

    async def deny(self, permit_id: uuid.UUID, reason: str) -> None:
        await self._change(permit_id, from_status="REQUESTED", to_status="DENIED", reason=reason)

    async def claim(self, permit_id: uuid.UUID, *, actor_id: str) -> ClaimedPermit:
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(ExecutionPermitModel)
                    .where(
                        ExecutionPermitModel.id == permit_id,
                        ExecutionPermitModel.status == "APPROVED",
                        ExecutionPermitModel.actor_id == actor_id,
                        ExecutionPermitModel.valid_until > func.now(),
                    )
                    .values(status="CLAIMED", claimed_at=func.now())
                    .returning(ExecutionPermitModel)
                ),
            )
            permit = result.scalar_one_or_none()
            if permit is None:
                raise DomainError(ErrorCode.INVALID_STATE, "permit is unavailable or expired")
            return ClaimedPermit(
                id=permit.id,
                nonce=permit.nonce,
                binding=PermitBinding(
                    goal_id=permit.goal_id,
                    work_id=permit.work_id,
                    run_id=permit.run_id,
                    actor_id=permit.actor_id,
                    action=permit.action,
                    target=permit.target,
                    parameters={},
                    data_scope=permit.data_scope,
                    network_scope=permit.network_scope,
                    resource_limit=permit.resource_limit,
                    risk_level=permit.risk_level,
                    valid_until=permit.valid_until,
                    idempotency_key=permit.idempotency_key,
                ),
            )

    async def consume(self, permit_id: uuid.UUID, *, nonce: uuid.UUID) -> None:
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(ExecutionPermitModel)
                    .where(
                        ExecutionPermitModel.id == permit_id,
                        ExecutionPermitModel.status == "CLAIMED",
                        ExecutionPermitModel.nonce == nonce,
                    )
                    .values(status="CONSUMED", consumed_at=func.now())
                ),
            )
            if result.rowcount != 1:
                raise DomainError(ErrorCode.INVALID_STATE, "permit was already consumed or invalid")

    async def revoke(self, permit_id: uuid.UUID, reason: str) -> None:
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(ExecutionPermitModel)
                    .where(
                        ExecutionPermitModel.id == permit_id,
                        ExecutionPermitModel.status.in_(("REQUESTED", "APPROVED", "CLAIMED")),
                    )
                    .values(status="REVOKED", revoked_at=func.now(), decision_reason=reason)
                ),
            )
            if result.rowcount != 1:
                raise DomainError(ErrorCode.INVALID_STATE, "permit is not revocable")

    async def expire_due(self) -> int:
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(ExecutionPermitModel)
                    .where(
                        ExecutionPermitModel.status.in_(("REQUESTED", "APPROVED", "CLAIMED")),
                        ExecutionPermitModel.valid_until <= func.now(),
                    )
                    .values(status="EXPIRED", decision_reason="validity window elapsed")
                ),
            )
            return result.rowcount

    async def _change(
        self, permit_id: uuid.UUID, *, from_status: str, to_status: str, reason: str
    ) -> None:
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(ExecutionPermitModel)
                    .where(
                        ExecutionPermitModel.id == permit_id,
                        ExecutionPermitModel.status == from_status,
                        ExecutionPermitModel.valid_until > func.now(),
                    )
                    .values(status=to_status, decision_reason=reason)
                ),
            )
            if result.rowcount != 1:
                raise DomainError(ErrorCode.INVALID_STATE, "permit decision is no longer valid")

    @staticmethod
    def parameter_hash(parameters: dict[str, Any]) -> str:
        canonical = json.dumps(
            parameters, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)
