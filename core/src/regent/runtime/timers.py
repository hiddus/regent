import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import DurableTimerModel, OutboxEventModel


@dataclass(frozen=True, slots=True)
class ClaimedTimer:
    id: uuid.UUID
    aggregate_type: str
    aggregate_id: uuid.UUID
    command: str
    payload: dict[str, Any]


def due_timer_statement(limit: int) -> Select[tuple[DurableTimerModel]]:
    return (
        select(DurableTimerModel)
        .where(
            DurableTimerModel.status.in_(("PENDING", "FAILED")),
            DurableTimerModel.due_at <= func.now(),
            or_(
                DurableTimerModel.lease_expires_at.is_(None),
                DurableTimerModel.lease_expires_at < func.now(),
            ),
        )
        .order_by(DurableTimerModel.due_at)
        .with_for_update(skip_locked=True)
        .limit(limit)
    )


class DurableTimerService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        lease_seconds: int = 30,
        retry_seconds: int = 5,
    ) -> None:
        self._sessions = sessions
        self._lease_seconds = lease_seconds
        self._retry_seconds = retry_seconds

    async def schedule(
        self,
        *,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        command: str,
        payload: dict[str, Any],
        due_at: datetime,
    ) -> uuid.UUID:
        if due_at.tzinfo is None:
            raise ValueError("due_at must be timezone-aware")
        timer_id = uuid.uuid4()
        async with self._sessions() as session, session.begin():
            session.add(
                DurableTimerModel(
                    id=timer_id,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    command=command,
                    payload=payload,
                    due_at=due_at,
                    status="PENDING",
                )
            )
        return timer_id

    async def cancel(self, timer_id: uuid.UUID) -> None:
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(DurableTimerModel)
                    .where(
                        DurableTimerModel.id == timer_id,
                        DurableTimerModel.status.in_(("PENDING", "FAILED")),
                    )
                    .values(status="CANCELLED", lease_owner=None, lease_expires_at=None)
                ),
            )
            if result.rowcount != 1:
                raise DomainError(ErrorCode.INVALID_STATE, "timer is not cancellable")

    async def claim(self, worker_id: str, *, limit: int = 10) -> list[ClaimedTimer]:
        async with self._sessions() as session, session.begin():
            now = await self._database_now(session)
            timers = list((await session.scalars(due_timer_statement(limit))).all())
            claimed = []
            for timer in timers:
                timer.status = "CLAIMED"
                timer.lease_owner = worker_id
                timer.lease_expires_at = now + timedelta(seconds=self._lease_seconds)
                timer.attempt += 1
                timer.last_error = None
                claimed.append(
                    ClaimedTimer(
                        id=timer.id,
                        aggregate_type=timer.aggregate_type,
                        aggregate_id=timer.aggregate_id,
                        command=timer.command,
                        payload=timer.payload,
                    )
                )
            return claimed

    async def fire(self, timer: ClaimedTimer, worker_id: str) -> uuid.UUID:
        event_id = uuid.uuid4()
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(DurableTimerModel)
                    .where(
                        DurableTimerModel.id == timer.id,
                        DurableTimerModel.status == "CLAIMED",
                        DurableTimerModel.lease_owner == worker_id,
                        DurableTimerModel.lease_expires_at > func.now(),
                    )
                    .values(status="FIRED", lease_owner=None, lease_expires_at=None)
                ),
            )
            if result.rowcount != 1:
                raise DomainError(ErrorCode.LEASE_LOST, f"timer lease {timer.id} was lost")
            session.add(
                OutboxEventModel(
                    id=event_id,
                    event_type="TimerFired",
                    aggregate_type=timer.aggregate_type,
                    aggregate_id=timer.aggregate_id,
                    aggregate_version=0,
                    payload={"command": timer.command, **timer.payload},
                    status="PENDING",
                    attempt=0,
                    correlation_id=uuid.uuid4(),
                )
            )
        return event_id

    async def fail(self, timer_id: uuid.UUID, worker_id: str, error: str) -> None:
        async with self._sessions() as session, session.begin():
            now = await self._database_now(session)
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(DurableTimerModel)
                    .where(
                        DurableTimerModel.id == timer_id,
                        DurableTimerModel.status == "CLAIMED",
                        DurableTimerModel.lease_owner == worker_id,
                    )
                    .values(
                        status="FAILED",
                        due_at=now + timedelta(seconds=self._retry_seconds),
                        lease_owner=None,
                        lease_expires_at=None,
                        last_error=error[:4000],
                    )
                ),
            )
            if result.rowcount != 1:
                raise DomainError(ErrorCode.LEASE_LOST, f"timer lease {timer_id} was lost")

    async def dispatch_due(self, worker_id: str, *, limit: int = 10) -> int:
        claimed = await self.claim(worker_id, limit=limit)
        for timer in claimed:
            await self.fire(timer, worker_id)
        return len(claimed)

    @staticmethod
    async def _database_now(session: AsyncSession) -> datetime:
        value = await session.scalar(select(func.now()))
        if value is None:
            raise RuntimeError("database did not return current time")
        return value if value.tzinfo else value.replace(tzinfo=UTC)
