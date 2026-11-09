"""Periodic reconciliation worker for stale ExternalOperations.

Scans DISPATCHING/UNKNOWN EOs that exceeded the timeout threshold
(default 15 min) and transitions them to RECONCILING, then attempts
provider query → resolve (G0 durable path).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.external_operation_service import ExternalOperationService

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_MINUTES = 15


class ReconciliationWorker:
    """Periodic worker that reconciles stale external operations.

    Usage::

        worker = ReconciliationWorker(sessions)
        reconciled = await worker.tick()
        for eo_id in reconciled:
            logger.info("Reconciled stale EO %s", eo_id)
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        timeout_minutes: int = _DEFAULT_TIMEOUT_MINUTES,
    ) -> None:
        self._sessions = sessions
        self._timeout_minutes = timeout_minutes
        self._service = ExternalOperationService(sessions)

    async def tick(
        self,
        *,
        now: datetime | None = None,
    ) -> list[uuid.UUID]:
        """Run one reconciliation sweep.

        1) Stale DISPATCHING/UNKNOWN → RECONCILING
        2) RECONCILING → provider query resolve or MANUAL_REVIEW on deadline
        Returns EO ids touched in either step.
        """
        clock = now or datetime.now(UTC)
        touched: list[uuid.UUID] = []
        try:
            reconciled = await self._service.reconcile_stale_unknowns(
                now=clock,
                timeout_minutes=self._timeout_minutes,
            )
            if reconciled:
                logger.info(
                    "Reconciliation sweep: %d stale EO(s) → RECONCILING",
                    len(reconciled),
                )
                touched.extend(reconciled)
            resolved = await self._service.resolve_reconciling_via_query(now=clock)
            if resolved:
                logger.info(
                    "Reconciliation resolve: %d EO(s) via provider query/deadline",
                    len(resolved),
                )
                touched.extend(resolved)
            return touched
        except Exception:
            logger.exception("Reconciliation sweep failed")
            return touched

    async def reconcile_specific(
        self,
        operation_id: uuid.UUID,
        *,
        resolved_status: str,
        external_id: str | None = None,
        summary: dict | None = None,
    ) -> None:
        """Manually reconcile a specific EO by id.

        Transitions UNKNOWN → RECONCILING → resolved_status.
        """
        svc = self._service
        eo = await svc.get(operation_id)
        if eo.status == "UNKNOWN":
            await svc.begin_reconcile(operation_id)
        await svc.resolve_reconcile(
            operation_id,
            status=resolved_status,
            external_id=external_id,
            summary=summary,
        )
