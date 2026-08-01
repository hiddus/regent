import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import OutboxEventModel

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

# Transient / concurrency errors: keep exponential outbox retry.
_RETRYABLE_DOMAIN_CODES = frozenset(
    {
        ErrorCode.LEASE_CONFLICT,
        ErrorCode.LEASE_LOST,
        ErrorCode.VERSION_CONFLICT,
        ErrorCode.ACTIVE_RUN_EXISTS,
        ErrorCode.STALE_LEASE,
        ErrorCode.EXTERNAL_EFFECT_UNKNOWN,
    }
)

# Business / permanent invalid state: do not burn attempts on the same bad payload.
# GenerationRunRequested handlers should prefer learn→replan→new event; this is the
# safety net when INVALID_STATE still bubbles to the dispatcher.
_NON_RETRYABLE_DOMAIN_CODES = frozenset(
    {
        ErrorCode.INVALID_STATE,
        ErrorCode.NOT_FOUND,
        ErrorCode.POLICY_DENIED,
        ErrorCode.DELIVERY_REJECTED,
        ErrorCode.GOAL_TERMINAL,
        ErrorCode.PERMIT_REQUIRED,
        ErrorCode.PERMIT_INVALID,
        ErrorCode.RECONCILIATION_REQUIRED,
        ErrorCode.NO_ACTIVE_CONSTITUTION,
        ErrorCode.POLICY_EVALUATION_FAILED,
        ErrorCode.NO_FEASIBLE_ORGANIZATION,
        ErrorCode.STALE_ORGANIZATION_VERSION,
        ErrorCode.INVALID_AGENT_LIFECYCLE_TRANSITION,
        ErrorCode.CAPABILITY_SCOPE_ESCALATION,
        ErrorCode.ENVELOPE_TAMPERED,
        ErrorCode.ENVELOPE_EXPIRED,
        ErrorCode.ENVELOPE_REPLAYED,
        ErrorCode.MCP_SERVER_NOT_CERTIFIED,
    }
)


@dataclass(frozen=True, slots=True)
class ClaimedEvent:
    id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    attempt: int
    correlation_id: uuid.UUID


def is_retryable_handler_error(exc: BaseException) -> bool:
    """Infrastructure/transient → retry; business INVALID_STATE → dead-letter."""
    if isinstance(exc, DomainError):
        if exc.code in _RETRYABLE_DOMAIN_CODES:
            return True
        if exc.code in _NON_RETRYABLE_DOMAIN_CODES:
            return False
        return False
    return True


def claim_statement(limit: int) -> Select[tuple[OutboxEventModel]]:
    """Claim PENDING/FAILED due events, and reclaim expired DISPATCHING leases."""
    return (
        select(OutboxEventModel)
        .where(
            or_(
                and_(
                    OutboxEventModel.status.in_(("PENDING", "FAILED")),
                    OutboxEventModel.available_at <= func.now(),
                    or_(
                        OutboxEventModel.lease_expires_at.is_(None),
                        OutboxEventModel.lease_expires_at < func.now(),
                    ),
                ),
                and_(
                    OutboxEventModel.status == "DISPATCHING",
                    OutboxEventModel.lease_expires_at.is_not(None),
                    OutboxEventModel.lease_expires_at < func.now(),
                ),
            )
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
        dispatch_concurrency: int = 1,
    ) -> None:
        self._sessions = sessions
        self._handlers = handlers
        self._lease_seconds = lease_seconds
        self._retry_seconds = retry_seconds
        self._max_attempts = max_attempts
        self._dispatch_concurrency = max(1, int(dispatch_concurrency))

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

    async def _dispatch_one(self, worker_id: str, event: ClaimedEvent) -> None:
        handler = self._handlers.get(event.event_type)
        if handler is None:
            # Missing handler is a permanent wiring bug — do not burn retries
            # and poison the outbox (e.g. historical DeliveryStateChanged).
            await self.fail(
                event.id,
                worker_id,
                f"no handler registered for {event.event_type}",
                retryable=False,
            )
            return
        try:
            await handler(event.payload)
        except Exception as exc:
            await self.fail(
                event.id,
                worker_id,
                f"{type(exc).__name__}: {exc}",
                retryable=is_retryable_handler_error(exc),
            )
        else:
            await self.ack(event.id, worker_id)

    async def dispatch_once(self, worker_id: str, *, limit: int | None = None) -> int:
        """Claim due events and run handlers (optionally in parallel).

        Claim size defaults to ``dispatch_concurrency`` so a worker does not
        pin many long LLM events while only running a few at a time. Multi-worker
        fleets rely on ``SKIP LOCKED`` for horizontal parallelism.
        """
        claim_limit = self._dispatch_concurrency if limit is None else max(1, int(limit))
        claimed = await self.claim(worker_id, limit=claim_limit)
        if not claimed:
            return 0
        if self._dispatch_concurrency <= 1 or len(claimed) == 1:
            for event in claimed:
                await self._dispatch_one(worker_id, event)
            return len(claimed)

        sem = asyncio.Semaphore(self._dispatch_concurrency)

        async def _guarded(event: ClaimedEvent) -> None:
            async with sem:
                await self._dispatch_one(worker_id, event)

        await asyncio.gather(*[_guarded(event) for event in claimed])
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
            if result.rowcount == 1:
                return
            # Handler succeeded after lease expiry: still finalize to avoid re-dispatch.
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(OutboxEventModel)
                    .where(
                        OutboxEventModel.id == event_id,
                        OutboxEventModel.status == "DISPATCHING",
                    )
                    .values(
                        status="DISPATCHED",
                        dispatched_at=func.now(),
                        lease_owner=None,
                        lease_expires_at=None,
                    )
                ),
            )
            if result.rowcount != 1:
                # Already DISPATCHED/FAILED by reclaim — treat as success.
                return

    async def fail(
        self,
        event_id: uuid.UUID,
        worker_id: str,
        error: str,
        *,
        retryable: bool = True,
    ) -> None:
        async with self._sessions() as session, session.begin():
            db_now = await self._database_now(session)
            event = await session.get(OutboxEventModel, event_id)
            if event is None:
                raise DomainError(ErrorCode.NOT_FOUND, "outbox event not found")
            # Non-retryable business errors (e.g. INVALID_STATE on the same bad
            # GenerationRunRequested payload) skip attempt burn-down to dead letter.
            dead_letter = (not retryable) or event.attempt >= self._max_attempts
            delay = min(
                self._retry_seconds * 2 ** max(event.attempt - 1, 0),
                300,
            )
            # Gateway / concurrency pressure: back off harder so we don't storm.
            err_l = error.lower()
            exp = 2 ** max(event.attempt - 1, 0)
            if "lease_conflict" in err_l or "concurrency cap" in err_l:
                delay = max(delay, min(45 * exp, 300))
            elif "504" in err_l or "gateway time" in err_l or "timeout" in err_l:
                delay = max(delay, min(60 * exp, 300))
            tagged_error = error if retryable else f"[non-retryable] {error}"
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
                        last_error=tagged_error[:4000],
                    )
                ),
            )
            # Lease may expire during long handlers; do not crash the worker loop.
            if result.rowcount != 1:
                return

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
