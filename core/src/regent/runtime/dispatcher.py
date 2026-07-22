import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import OutboxEventModel

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ClaimedEvent:
    id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    attempt: int
    correlation_id: uuid.UUID


def claim_statement(limit: int) -> Select[tuple[OutboxEventModel]]:
    return (
        select(OutboxEventModel)
        .where(
            OutboxEventModel.status.in_(("PENDING", "FAILED")),
            OutboxEventModel.available_at <= func.now(),
            or_(
                OutboxEventModel.lease_expires_at.is_(None),
                OutboxEventModel.lease_expires_at < func.now(),
            ),
        )
        .order_by(OutboxEventModel.available_at, OutboxEventModel.occurred_at)
        .with_for_update(skip_locked=True)
        .limit(limit)
    )


class OutboxDispatcher:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        handlers: Mapping[str, EventHandler],
        *,
        lease_seconds: int = 30,
        retry_seconds: int = 5,
        max_attempts: int = 8,
    ) -> None:
        self._sessions = sessions
        self._handlers = handlers
        self._lease_seconds = lease_seconds
        self._retry_seconds = retry_seconds
        self._max_attempts = max_attempts

    async def claim(self, worker_id: str, *, limit: int = 10) -> list[ClaimedEvent]:
        async with self._sessions() as session, session.begin():
            db_now = await self._database_now(session)
            events = list((await session.scalars(claim_statement(limit))).all())
            lease_expires_at = db_now + timedelta(seconds=self._lease_seconds)
            claimed: list[ClaimedEvent] = []
            for event in events:
                event.status = "DISPATCHING"
                event.lease_owner = worker_id
                event.lease_expires_at = lease_expires_at
                event.attempt += 1
                event.last_error = None
                claimed.append(
                    ClaimedEvent(
                        id=event.id,
                        event_type=event.event_type,
                        payload=event.payload,
                        attempt=event.attempt,
                        correlation_id=event.correlation_id,
                    )
                )
            return claimed

    async def dispatch_once(self, worker_id: str, *, limit: int = 10) -> int:
        claimed = await self.claim(worker_id, limit=limit)
        for event in claimed:
            handler = self._handlers.get(event.event_type)
            if handler is None:
                await self.fail(
                    event.id,
                    worker_id,
                    f"no handler registered for {event.event_type}",
                )
                continue
            try:
                await handler(event.payload)
            except Exception as exc:
                await self.fail(event.id, worker_id, f"{type(exc).__name__}: {exc}")
            else:
                await self.ack(event.id, worker_id)
        return len(claimed)

    async def ack(self, event_id: uuid.UUID, worker_id: str) -> None:
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(OutboxEventModel)
                    .where(
                        OutboxEventModel.id == event_id,
                        OutboxEventModel.status == "DISPATCHING",
                        OutboxEventModel.lease_owner == worker_id,
                        OutboxEventModel.lease_expires_at > func.now(),
                    )
                    .values(
                        status="DISPATCHED",
                        dispatched_at=func.now(),
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                ),
            )
            self._require_owned_lease(result.rowcount, event_id)

    async def fail(self, event_id: uuid.UUID, worker_id: str, error: str) -> None:
        async with self._sessions() as session, session.begin():
            db_now = await self._database_now(session)
            event = await session.get(OutboxEventModel, event_id)
            if event is None:
                raise DomainError(ErrorCode.NOT_FOUND, "outbox event not found")
            dead_letter = event.attempt >= self._max_attempts
            delay = min(
                self._retry_seconds * 2 ** max(event.attempt - 1, 0),
                300,
            )
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(OutboxEventModel)
                    .where(
                        OutboxEventModel.id == event_id,
                        OutboxEventModel.status == "DISPATCHING",
                        OutboxEventModel.lease_owner == worker_id,
                    )
                    .values(
                        status="DEAD_LETTER" if dead_letter else "FAILED",
                        available_at=db_now + timedelta(seconds=delay),
                        lease_owner=None,
                        lease_expires_at=None,
                        last_error=error[:4000],
                    )
                ),
            )

            self._require_owned_lease(result.rowcount, event_id)
    @staticmethod
    async def _database_now(session: AsyncSession) -> datetime:
        value = await session.scalar(select(func.now()))
        if value is None:
            raise RuntimeError("database did not return current time")
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @staticmethod
    def _require_owned_lease(rowcount: int, event_id: uuid.UUID) -> None:
        if rowcount != 1:
            raise DomainError(
                ErrorCode.LEASE_LOST,
                f"dispatch lease for event {event_id} is not owned or expired",
            )
