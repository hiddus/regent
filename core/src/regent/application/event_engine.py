"""EventEngine — unified event routing layer driving the arg max loop.

Wraps the OutboxDispatcher with a typed handler registration API,
event lifecycle tracking, and structured dispatch metrics.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.execution_events import P1_MAIN_CHAIN_EVENTS
from regent.infrastructure.models import OutboxEventModel

logger = logging.getLogger(__name__)

# Handler type: async callable(event: OutboxEventModel, payload: dict) -> None
EventHandler = Callable[[OutboxEventModel, dict[str, Any]], Coroutine[Any, Any, None]]


@dataclass(frozen=True, slots=True)
class DispatchRecord:
    """Immutable record of a single event dispatch."""

    event_id: uuid.UUID
    event_type: str
    handler_ok: bool
    duration_ms: float
    timestamp: str


class EventEngine:
    """Unified event routing — drives the arg max loop.

    Usage::

        engine = EventEngine(sessions)
        engine.register_handler("GoalExecutionRequested", handle_goal)
        engine.register_handler("DiscoveryRoundRequested", handle_discovery)
        await engine.start()
        # ... engine.dispatch_pending() consumes outbox events ...
        await engine.stop()
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = sessions
        self._handlers: dict[str, list[EventHandler]] = {}
        self._dispatch_log: list[DispatchRecord] = []
        self._running = False

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def register_handler(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler for an event type. Multiple handlers per type allowed."""
        self._handlers.setdefault(event_type, []).append(handler)
        logger.debug(
            "event handler registered",
            extra={
                "event_type": event_type,
                "handler": getattr(handler, "__qualname__", repr(handler)),
            },
        )

    def register_handlers(self, mapping: dict[str, EventHandler]) -> None:
        """Bulk-register handlers from a {event_type: handler} mapping."""
        for event_type, handler in mapping.items():
            self.register_handler(event_type, handler)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Mark the engine as running."""
        self._running = True
        logger.info(
            "EventEngine started",
            extra={"registered_types": len(self._handlers)},
        )

    async def stop(self) -> None:
        """Mark the engine as stopped."""
        self._running = False
        logger.info(
            "EventEngine stopped",
            extra={"dispatched": len(self._dispatch_log)},
        )

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, event: OutboxEventModel) -> None:
        """Dispatch a single outbox event to all registered handlers."""
        event_type = event.event_type
        handlers = self._handlers.get(event_type, [])
        if not handlers:
            logger.debug("no handler for event", extra={"event_type": event_type})
            return
        payload = dict(event.payload or {})
        for handler in handlers:
            t0 = datetime.now(UTC)
            ok = True
            try:
                await handler(event, payload)
            except Exception:
                ok = False
                logger.exception(
                    "event handler failed",
                    extra={
                        "event_type": event_type,
                        "event_id": str(event.id),
                        "handler": getattr(handler, "__qualname__", repr(handler)),
                    },
                )
            elapsed = (datetime.now(UTC) - t0).total_seconds() * 1000
            self._dispatch_log.append(
                DispatchRecord(
                    event_id=event.id,
                    event_type=event_type,
                    handler_ok=ok,
                    duration_ms=elapsed,
                    timestamp=t0.isoformat(),
                )
            )

    async def dispatch_pending(self, worker_id: str, *, limit: int = 50) -> int:
        """Consume and dispatch up to `limit` pending outbox events.

        Returns the number of events dispatched.
        """
        async with self._sessions() as session, session.begin():
            rows = await session.scalars(
                select(OutboxEventModel)
                .where(OutboxEventModel.status == "PENDING")
                .order_by(OutboxEventModel.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            events = list(rows.all())
            for event in events:
                event.status = "DISPATCHED"
                event.dispatched_at = datetime.now(UTC)
                await self.dispatch(event)
        return len(events)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def dispatch_log(self) -> list[DispatchRecord]:
        return list(self._dispatch_log)

    @property
    def registered_event_types(self) -> set[str]:
        return set(self._handlers.keys())

    def p1_coverage(self) -> float:
        """Fraction of P1 main chain events that have a registered handler."""
        if not P1_MAIN_CHAIN_EVENTS:
            return 1.0
        covered = sum(1 for e in P1_MAIN_CHAIN_EVENTS if e in self._handlers)
        return covered / len(P1_MAIN_CHAIN_EVENTS)


def build_p1_event_engine(
    sessions: async_sessionmaker[AsyncSession],
    orchestrator: object,
) -> EventEngine:
    """Factory: create an EventEngine pre-wired with P1 main chain handlers.

    Imports the orchestrator's ``get_p1_event_handlers`` and registers them.
    """
    from regent.application.execution_orchestrator import get_p1_event_handlers

    engine = EventEngine(sessions)
    handlers = get_p1_event_handlers(orchestrator)  # type: ignore[arg-type]
    engine.register_handlers(handlers)
    return engine
