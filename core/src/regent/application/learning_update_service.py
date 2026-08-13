"""Evidence-backed learning updates that become APPLIED only on later use."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    LearningUpdateApplicationModel,
    LearningUpdateModel,
)


@dataclass(frozen=True)
class ProposeLearningUpdate:
    org_key: str
    target_type: str
    target_key: str
    base_version: str
    candidate_version: str
    before: dict[str, Any]
    after: dict[str, Any]
    actor: str
    evidence_refs: list[Any] = field(default_factory=list)
    applicability: dict[str, Any] = field(default_factory=dict)
    invalidation: dict[str, Any] = field(default_factory=dict)
    ttl_seconds: int | None = None
    goal_id: uuid.UUID | None = None
    rollback_update_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ApplyLearningUpdate:
    update_id: uuid.UUID
    consumer_type: str
    consumer_ref: str
    applied_version: str
    read_context: dict[str, Any] = field(default_factory=dict)


class LearningUpdateService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def propose(self, command: ProposeLearningUpdate) -> LearningUpdateModel:
        if not all(
            value.strip()
            for value in (
                command.org_key,
                command.target_type,
                command.target_key,
                command.base_version,
                command.candidate_version,
                command.actor,
            )
        ):
            raise ValueError("learning update identity and versions must be non-empty")
        if command.base_version == command.candidate_version:
            raise ValueError("candidate_version must differ from base_version")
        if command.before == command.after:
            raise ValueError("learning update must change the target state")
        if command.ttl_seconds is not None and command.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

        now = datetime.now(UTC)
        expires_at = (
            now + timedelta(seconds=command.ttl_seconds)
            if command.ttl_seconds is not None
            else None
        )
        async with self._sessions() as session, session.begin():
            if command.rollback_update_id is not None:
                rollback = await session.get(LearningUpdateModel, command.rollback_update_id)
                if rollback is None:
                    raise DomainError(ErrorCode.NOT_FOUND, "rollback learning update not found")
                if (
                    rollback.org_key != command.org_key
                    or rollback.target_type != command.target_type
                    or rollback.target_key != command.target_key
                ):
                    raise DomainError(
                        ErrorCode.INVALID_STATE,
                        "rollback update must address the same target",
                    )
            model = LearningUpdateModel(
                id=uuid.uuid4(),
                org_key=command.org_key,
                goal_id=command.goal_id,
                target_type=command.target_type,
                target_key=command.target_key,
                base_version=command.base_version,
                candidate_version=command.candidate_version,
                before_json=dict(command.before),
                after_json=dict(command.after),
                evidence_refs=list(command.evidence_refs),
                applicability_json=dict(command.applicability),
                invalidation_json=dict(command.invalidation),
                ttl_seconds=command.ttl_seconds,
                expires_at=expires_at,
                rollback_update_id=command.rollback_update_id,
                status="PROPOSED",
                created_by=command.actor,
            )
            session.add(model)
            await session.flush()
            return model

    async def record_application(
        self, command: ApplyLearningUpdate
    ) -> LearningUpdateApplicationModel:
        if not command.consumer_type.strip() or not command.consumer_ref.strip():
            raise ValueError("consumer identity must be non-empty")
        expired = False
        application: LearningUpdateApplicationModel | None = None
        async with self._sessions() as session, session.begin():
            update = await session.get(LearningUpdateModel, command.update_id)
            if update is None:
                raise DomainError(ErrorCode.NOT_FOUND, "learning update not found")
            now = datetime.now(UTC)
            if update.expires_at is not None:
                expires_at = update.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at <= now:
                    update.status = "EXPIRED"
                    expired = True
            if not expired and update.status in {"REVOKED", "EXPIRED"}:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    f"cannot apply learning update from {update.status}",
                )
            if not expired and command.applied_version != update.candidate_version:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT,
                    "consumer did not apply the candidate version",
                )
            if not expired:
                existing = await session.scalar(
                    select(LearningUpdateApplicationModel).where(
                        LearningUpdateApplicationModel.learning_update_id == update.id,
                        LearningUpdateApplicationModel.consumer_type == command.consumer_type,
                        LearningUpdateApplicationModel.consumer_ref == command.consumer_ref,
                    )
                )
                if existing is not None:
                    return existing
                application = LearningUpdateApplicationModel(
                    id=uuid.uuid4(),
                    learning_update_id=update.id,
                    consumer_type=command.consumer_type,
                    consumer_ref=command.consumer_ref,
                    applied_version=command.applied_version,
                    read_context_json=dict(command.read_context),
                    applied_at=now,
                )
                session.add(application)
                update.status = "APPLIED"
                if update.first_applied_at is None:
                    update.first_applied_at = now
                await session.flush()
        if expired:
            raise DomainError(ErrorCode.INVALID_STATE, "learning update has expired")
        assert application is not None
        return application

    async def revoke(self, update_id: uuid.UUID) -> LearningUpdateModel:
        async with self._sessions() as session, session.begin():
            update = await session.get(LearningUpdateModel, update_id)
            if update is None:
                raise DomainError(ErrorCode.NOT_FOUND, "learning update not found")
            if update.status == "EXPIRED":
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    "expired learning update cannot be revoked",
                )
            update.status = "REVOKED"
            await session.flush()
            return update
