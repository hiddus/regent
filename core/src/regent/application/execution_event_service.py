"""Append-only execution event ledger with deterministic per-goal replay."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import ExecutionEventModel, GoalModel


@dataclass(frozen=True)
class AppendExecutionEvent:
    event_key: str
    event_type: str
    goal_id: uuid.UUID
    input_hash: str
    output_hash: str
    permission_snapshot: dict[str, Any]
    parent_event_id: uuid.UUID | None = None
    causation_event_id: uuid.UUID | None = None
    work_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    organization_version_id: uuid.UUID | None = None
    budget_reservation_ref: str | None = None
    model_version: str | None = None
    tool_versions: dict[str, Any] = field(default_factory=dict)


def _payload_hash(command: AppendExecutionEvent) -> str:
    payload = asdict(command)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ExecutionEventService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def append(self, command: AppendExecutionEvent) -> ExecutionEventModel:
        if not command.event_key.strip() or not command.event_type.strip():
            raise ValueError("event_key and event_type are required")
        for name, value in (
            ("input_hash", command.input_hash),
            ("output_hash", command.output_hash),
        ):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
                raise ValueError(f"{name} must be a SHA-256 hex digest")
        digest = _payload_hash(command)
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(ExecutionEventModel).where(
                    ExecutionEventModel.event_key == command.event_key
                )
            )
            if existing is not None:
                if existing.payload_hash != digest:
                    raise DomainError(
                        ErrorCode.VERSION_CONFLICT,
                        "event_key already exists with different immutable payload",
                    )
                return existing

            goal = await session.get(GoalModel, command.goal_id, with_for_update=True)
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, "goal not found")
            await self._validate_lineage(session, command)
            latest = await session.scalar(
                select(func.max(ExecutionEventModel.goal_sequence)).where(
                    ExecutionEventModel.goal_id == command.goal_id
                )
            )
            event = ExecutionEventModel(
                event_id=uuid.uuid4(),
                event_key=command.event_key,
                event_type=command.event_type,
                payload_hash=digest,
                parent_event_id=command.parent_event_id,
                causation_event_id=command.causation_event_id,
                goal_id=command.goal_id,
                goal_sequence=int(latest or 0) + 1,
                work_id=command.work_id,
                run_id=command.run_id,
                agent_id=command.agent_id,
                organization_version_id=command.organization_version_id,
                input_hash=command.input_hash.lower(),
                output_hash=command.output_hash.lower(),
                permission_snapshot_json=dict(command.permission_snapshot),
                budget_reservation_ref=command.budget_reservation_ref,
                model_version=command.model_version,
                tool_versions_json=dict(command.tool_versions),
            )
            session.add(event)
            await session.flush()
            return event

    async def replay_goal(self, goal_id: uuid.UUID) -> list[ExecutionEventModel]:
        async with self._sessions() as session:
            return list(
                await session.scalars(
                    select(ExecutionEventModel)
                    .where(ExecutionEventModel.goal_id == goal_id)
                    .order_by(ExecutionEventModel.goal_sequence.asc())
                )
            )

    @staticmethod
    async def _validate_lineage(
        session: AsyncSession, command: AppendExecutionEvent
    ) -> None:
        for label, event_id in (
            ("parent", command.parent_event_id),
            ("causation", command.causation_event_id),
        ):
            if event_id is None:
                continue
            related = await session.get(ExecutionEventModel, event_id)
            if related is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"{label} event not found")
            if related.goal_id != command.goal_id:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    f"{label} event belongs to another goal",
                )
