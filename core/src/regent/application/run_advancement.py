"""GAC-C1: advance orphan CREATED runs into a runnable permit path."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.transition_service import TransitionContext, TransitionService
from regent.domain.errors import DomainError, ErrorCode
from regent.domain.transitions import RunCommand
from regent.infrastructure.models import RunModel

logger = logging.getLogger(__name__)


async def advance_created_run(
    sessions: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    actor: str,
) -> str:
    """CREATED → PERMIT_PENDING → QUEUED → RUNNING. Returns final status."""
    transitions = TransitionService(sessions)
    async with sessions() as session:
        run = await session.get(RunModel, run_id)
        if run is None:
            raise DomainError(ErrorCode.NOT_FOUND, f"run {run_id} not found")
        if run.status != "CREATED":
            return run.status
        version = run.version
        correlation_id = run.correlation_id
    for command in (
        RunCommand.REQUEST_PERMIT,
        RunCommand.QUEUE,
        RunCommand.CLAIM,
    ):
        receipt = await transitions.transition_run(
            TransitionContext(run_id, version, actor, correlation_id),
            command,
        )
        version = receipt.version
    return "RUNNING"


async def reclaim_stale_created_runs(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: str = "worker:gac-c1",
    limit: int = 20,
) -> int:
    """Advance up to `limit` CREATED runs left by bypass inserts (GAC-C1)."""
    async with sessions() as session:
        rows = list(
            await session.scalars(
                select(RunModel)
                .where(RunModel.status == "CREATED")
                .order_by(RunModel.created_at.asc())
                .limit(limit)
            )
        )
    advanced = 0
    for run in rows:
        try:
            await advance_created_run(sessions, run.id, actor=actor)
            advanced += 1
        except Exception:
            logger.exception(
                "failed to advance CREATED run",
                extra={"run_id": str(run.id)},
            )
    return advanced
