"""Persistent FailureEnvelope + RepairAttempt (Tech-Spec §13.5 / GQ-0–GQ-2)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import FailureEnvelopeModel, RepairAttemptModel

FailureStage = Literal["build", "test", "smoke", "verification", "generation"]
RepairStatus = Literal[
    "REQUESTED", "RUNNING", "SUCCEEDED", "FAILED", "EXHAUSTED", "HANDED_OFF"
]

# Frozen per-stage repair policy (GQ-0 contract).
STAGE_REPAIR_POLICY: dict[str, dict[str, Any]] = {
    "build": {
        "max_attempts": 2,
        "timeout_seconds": 900,
        "non_retryable_codes": ("POLICY_DENIED", "GENERATOR_METADATA_MISMATCH"),
        "human_handoff_on_exhaust": True,
    },
    "test": {
        "max_attempts": 2,
        "timeout_seconds": 600,
        "non_retryable_codes": ("TEST_COMMAND_MISSING",),
        "human_handoff_on_exhaust": True,
    },
    "smoke": {
        "max_attempts": 2,
        "timeout_seconds": 600,
        "non_retryable_codes": ("SMOKE_SECURITY_BLOCK",),
        "human_handoff_on_exhaust": True,
    },
    "verification": {
        "max_attempts": 1,
        "timeout_seconds": 900,
        "non_retryable_codes": ("GENERATOR_METADATA_MISMATCH",),
        "human_handoff_on_exhaust": True,
    },
    "generation": {
        "max_attempts": 2,
        "timeout_seconds": 1200,
        "non_retryable_codes": ("GENERATOR_METADATA_MISMATCH", "POLICY_DENIED"),
        "human_handoff_on_exhaust": True,
    },
}

MAX_ERROR_SUMMARY_CHARS = 4_000


@dataclass(frozen=True, slots=True)
class RecordFailureCommand:
    goal_id: uuid.UUID
    stage: FailureStage
    error_summary: str
    error_code: str | None = None
    run_id: uuid.UUID | None = None
    generation_plan_id: uuid.UUID | None = None
    generation_run_id: uuid.UUID | None = None
    workspace_snapshot_id: uuid.UUID | None = None
    evidence_artifact_uri: str | None = None
    evidence_payload: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class StartRepairCommand:
    failure_envelope_id: uuid.UUID
    idempotency_key: str
    strategy: str
    input_snapshot: dict[str, Any]
    actor: str = "regent-core"


def clip_error_summary(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= MAX_ERROR_SUMMARY_CHARS:
        return text
    return text[: MAX_ERROR_SUMMARY_CHARS - 20] + "\n…[truncated]"


def is_non_retryable(stage: str, error_code: str | None) -> bool:
    policy = STAGE_REPAIR_POLICY.get(stage) or {}
    codes = set(policy.get("non_retryable_codes") or ())
    return bool(error_code) and error_code in codes


class FailureEnvelopeService:
    """Durable failure capture + repair attempt ledger (restart-safe, idempotent)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record_failure(self, command: RecordFailureCommand) -> FailureEnvelopeModel:
        if command.stage not in STAGE_REPAIR_POLICY:
            raise DomainError(ErrorCode.INVALID_STATE, f"unknown failure stage: {command.stage}")
        async with self._sessions() as session, session.begin():
            model = FailureEnvelopeModel(
                id=uuid.uuid4(),
                goal_id=command.goal_id,
                run_id=command.run_id,
                generation_plan_id=command.generation_plan_id,
                generation_run_id=command.generation_run_id,
                workspace_snapshot_id=command.workspace_snapshot_id,
                stage=command.stage,
                error_code=command.error_code,
                error_summary=clip_error_summary(command.error_summary),
                evidence_artifact_uri=command.evidence_artifact_uri,
                evidence_payload=dict(command.evidence_payload or {}),
                policy_json=dict(STAGE_REPAIR_POLICY[command.stage]),
                status="OPEN",
            )
            session.add(model)
            await session.flush()
            return model

    async def start_repair(self, command: StartRepairCommand) -> RepairAttemptModel:
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(RepairAttemptModel).where(
                    RepairAttemptModel.idempotency_key == command.idempotency_key
                )
            )
            if existing is not None:
                return existing

            envelope = await session.get(FailureEnvelopeModel, command.failure_envelope_id)
            if envelope is None:
                raise DomainError(ErrorCode.NOT_FOUND, "failure envelope not found")
            if envelope.status in {"CLOSED", "HANDED_OFF"}:
                raise DomainError(ErrorCode.INVALID_STATE, "failure envelope is terminal")

            policy = dict(envelope.policy_json or STAGE_REPAIR_POLICY.get(envelope.stage, {}))
            max_attempts = int(policy.get("max_attempts") or 1)
            if is_non_retryable(envelope.stage, envelope.error_code):
                envelope.status = "HANDED_OFF"
                attempt = RepairAttemptModel(
                    id=uuid.uuid4(),
                    failure_envelope_id=envelope.id,
                    attempt_no=1,
                    idempotency_key=command.idempotency_key,
                    strategy=command.strategy,
                    status="HANDED_OFF",
                    input_snapshot=dict(command.input_snapshot),
                    output_snapshot={},
                    termination_reason=f"non_retryable:{envelope.error_code}",
                    actor=command.actor,
                )
                session.add(attempt)
                await session.flush()
                return attempt

            rows = (
                await session.scalars(
                    select(RepairAttemptModel).where(
                        RepairAttemptModel.failure_envelope_id == envelope.id
                    )
                )
            ).all()
            attempt_no = len(rows) + 1
            if attempt_no > max_attempts:
                envelope.status = (
                    "HANDED_OFF" if policy.get("human_handoff_on_exhaust") else "CLOSED"
                )
                attempt = RepairAttemptModel(
                    id=uuid.uuid4(),
                    failure_envelope_id=envelope.id,
                    attempt_no=attempt_no,
                    idempotency_key=command.idempotency_key,
                    strategy=command.strategy,
                    status="EXHAUSTED",
                    input_snapshot=dict(command.input_snapshot),
                    output_snapshot={},
                    termination_reason="max_attempts_exceeded",
                    actor=command.actor,
                )
                session.add(attempt)
                await session.flush()
                return attempt

            envelope.status = "REPAIRING"
            attempt = RepairAttemptModel(
                id=uuid.uuid4(),
                failure_envelope_id=envelope.id,
                attempt_no=attempt_no,
                idempotency_key=command.idempotency_key,
                strategy=command.strategy,
                status="REQUESTED",
                input_snapshot=dict(command.input_snapshot),
                output_snapshot={},
                termination_reason=None,
                actor=command.actor,
            )
            session.add(attempt)
            await session.flush()
            return attempt

    async def complete_repair(
        self,
        attempt_id: uuid.UUID,
        *,
        status: RepairStatus,
        output_snapshot: dict[str, Any] | None = None,
        termination_reason: str | None = None,
    ) -> RepairAttemptModel:
        async with self._sessions() as session, session.begin():
            attempt = await session.get(RepairAttemptModel, attempt_id)
            if attempt is None:
                raise DomainError(ErrorCode.NOT_FOUND, "repair attempt not found")
            if attempt.status in {"SUCCEEDED", "FAILED", "EXHAUSTED", "HANDED_OFF"}:
                return attempt
            attempt.status = status
            attempt.output_snapshot = dict(output_snapshot or {})
            attempt.termination_reason = termination_reason
            attempt.completed_at = datetime.now(UTC)
            envelope = await session.get(FailureEnvelopeModel, attempt.failure_envelope_id)
            if envelope is not None:
                if status == "SUCCEEDED":
                    envelope.status = "CLOSED"
                elif status in {"EXHAUSTED", "HANDED_OFF"}:
                    envelope.status = "HANDED_OFF"
                elif status == "FAILED":
                    envelope.status = "OPEN"
            await session.flush()
            return attempt

    async def structured_feedback_for_goal(
        self, goal_id: uuid.UUID, *, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Return recent failure summaries suitable for regenerator retry context."""
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(FailureEnvelopeModel)
                    .where(FailureEnvelopeModel.goal_id == goal_id)
                    .order_by(FailureEnvelopeModel.created_at.desc())
                    .limit(limit)
                )
            ).all()
            return [
                {
                    "stage": row.stage,
                    "error_code": row.error_code,
                    "error_summary": row.error_summary,
                    "evidence_artifact_uri": row.evidence_artifact_uri,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]
