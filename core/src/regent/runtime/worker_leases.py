import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import WorkerLeaseModel


@dataclass(frozen=True, slots=True)
class WorkerLease:
    worker_id: str
    token: uuid.UUID
    expires_at: datetime


class WorkerLeaseService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        lease_seconds: int = 30,
    ) -> None:
        self._sessions = sessions
        self._lease_seconds = lease_seconds

    async def acquire(
        self, worker_id: str, *, metadata: dict[str, Any] | None = None
    ) -> WorkerLease:
        async with self._sessions() as session, session.begin():
            db_now = await self._database_now(session)
            current = await session.get(WorkerLeaseModel, worker_id, with_for_update=True)
            if current is not None and current.expires_at > db_now:
                raise DomainError(ErrorCode.LEASE_CONFLICT, f"worker {worker_id} is active")

            token = uuid.uuid4()
            expires_at = db_now + timedelta(seconds=self._lease_seconds)
            if current is None:
                session.add(
                    WorkerLeaseModel(
                        worker_id=worker_id,
                        lease_token=token,
                        started_at=db_now,
                        heartbeat_at=db_now,
                        expires_at=expires_at,
                        metadata_json=metadata or {},
                    )
                )
            else:
                current.lease_token = token
                current.started_at = db_now
                current.heartbeat_at = db_now
                current.expires_at = expires_at
                current.metadata_json = metadata or {}
            return WorkerLease(worker_id=worker_id, token=token, expires_at=expires_at)

    async def heartbeat(self, lease: WorkerLease) -> WorkerLease:
        async with self._sessions() as session, session.begin():
            db_now = await self._database_now(session)
            expires_at = db_now + timedelta(seconds=self._lease_seconds)
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(WorkerLeaseModel)
                    .where(
                        WorkerLeaseModel.worker_id == lease.worker_id,
                        WorkerLeaseModel.lease_token == lease.token,
                        WorkerLeaseModel.expires_at > func.now(),
                    )
                    .values(heartbeat_at=db_now, expires_at=expires_at)
                ),
            )
            if result.rowcount != 1:
                raise DomainError(ErrorCode.LEASE_LOST, f"worker {lease.worker_id} lost lease")
            return WorkerLease(
                worker_id=lease.worker_id,
                token=lease.token,
                expires_at=expires_at,
            )

    async def release(self, lease: WorkerLease) -> None:
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    delete(WorkerLeaseModel).where(
                        WorkerLeaseModel.worker_id == lease.worker_id,
                        WorkerLeaseModel.lease_token == lease.token,
                    )
                ),
            )
            if result.rowcount != 1:
                raise DomainError(ErrorCode.LEASE_LOST, f"worker {lease.worker_id} lost lease")

    @staticmethod
    async def _database_now(session: AsyncSession) -> datetime:
        value = await session.scalar(select(func.now()))
        if value is None:
            raise RuntimeError("database did not return current time")
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
