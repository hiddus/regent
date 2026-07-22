"""Outbox dead-letter listing and controlled replay."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import OutboxEventModel


@dataclass(frozen=True, slots=True)
class DeadLetterRecord:
    id: uuid.UUID
    event_type: str
    attempt: int
    last_error: str | None
    correlation_id: uuid.UUID
    available_at: datetime
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ReplayReceipt:
    id: uuid.UUID
    status: str
    attempt: int
    replayed_by: str
    replayed_at: datetime


class OutboxDeadLetterService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_dead_letters(self, *, limit: int = 50) -> list[DeadLetterRecord]:
        async with self._sessions() as session:
            rows = list(
                await session.scalars(
                    select(OutboxEventModel)
                    .where(OutboxEventModel.status == "DEAD_LETTER")
                    .order_by(OutboxEventModel.occurred_at.desc())
                    .limit(limit)
                )
            )
            return [
                DeadLetterRecord(
                    id=row.id,
                    event_type=row.event_type,
                    attempt=row.attempt,
                    last_error=row.last_error,
                    correlation_id=row.correlation_id,
                    available_at=row.available_at,
                    occurred_at=row.occurred_at,
                )
                for row in rows
            ]

    async def replay(
        self, event_id: uuid.UUID, *, actor: str, reset_attempts: bool = True
    ) -> ReplayReceipt:
        """Requeue a dead-letter event as PENDING for controlled replay."""
        async with self._sessions() as session, session.begin():
            event = await session.get(OutboxEventModel, event_id)
            if event is None:
                raise DomainError(ErrorCode.NOT_FOUND, "outbox event not found")
            if event.status != "DEAD_LETTER":
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    "only DEAD_LETTER events can be replayed",
                )
            now = datetime.now(UTC)
            values: dict[str, object] = {
                "status": "PENDING",
                "available_at": now,
                "lease_owner": None,
                "lease_expires_at": None,
                "last_error": f"replayed by {actor} at {now.isoformat()}",
            }
            if reset_attempts:
                values["attempt"] = 0
            await session.execute(
                update(OutboxEventModel)
                .where(
                    OutboxEventModel.id == event_id,
                    OutboxEventModel.status == "DEAD_LETTER",
                )
                .values(**values)
            )
            refreshed = await session.get(OutboxEventModel, event_id)
            assert refreshed is not None
            return ReplayReceipt(
                id=refreshed.id,
                status=refreshed.status,
                attempt=refreshed.attempt,
                replayed_by=actor,
                replayed_at=now,
            )
