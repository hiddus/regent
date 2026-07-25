"""P2-3 long-term memory (activated by Stage DecisionRecord).

V3 Memory Hierarchy:
- **Episodic**: events, historical runs, success/failure patterns
- **Semantic**: industry knowledge, rules, experience, capability templates
- **Working**: current task context (TTL-scoped, auto-expiring)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.p1_contracts import canonical_hash
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import MemoryRecordModel

# ---------------------------------------------------------------------------
# V3 Memory Kind taxonomy
# ---------------------------------------------------------------------------


class MemoryKind(StrEnum):
    """V3 memory hierarchy kinds."""

    # Episodic: events, historical runs, patterns
    EPISODIC_GOAL_ACHIEVED = "episodic.goal_achieved"
    EPISODIC_RUN_FAILURE = "episodic.run_failure"
    EPISODIC_CAPABILITY_ACQUIRED = "episodic.capability_acquired"
    EPISODIC_REORGANIZATION = "episodic.reorganization"
    EPISODIC_HUMAN_FEEDBACK = "episodic.human_feedback"

    # Semantic: knowledge, rules, experience, templates
    SEMANTIC_RULE = "semantic.rule"
    SEMANTIC_PATTERN = "semantic.pattern"
    SEMANTIC_KNOWLEDGE = "semantic.knowledge"
    SEMANTIC_TEMPLATE = "semantic.template"

    # Working: current task context (TTL-scoped)
    WORKING_CONTEXT = "working.context"
    WORKING_SNAPSHOT = "working.snapshot"

    # Legacy compatibility
    CANDIDATE = "candidate"
    VERIFIED = "verified"


# TTL defaults for working memory
_WORKING_MEMORY_TTL = timedelta(hours=1)
_SEMANTIC_MEMORY_TTL = timedelta(days=365)  # effectively permanent
_EPISODIC_MEMORY_TTL = timedelta(days=90)


def _ttl_for_kind(kind: str) -> timedelta | None:
    """Return the TTL for a given memory kind, or None for no expiry."""
    if kind.startswith("working."):
        return _WORKING_MEMORY_TTL
    if kind.startswith("episodic."):
        return _EPISODIC_MEMORY_TTL
    if kind.startswith("semantic."):
        return _SEMANTIC_MEMORY_TTL
    return None  # legacy kinds have no TTL


@dataclass(frozen=True, slots=True)
class AdmitMemory:
    org_key: str
    kind: str
    content: dict[str, Any]
    actor: str
    goal_id: uuid.UUID | None = None
    source_refs: list[Any] | None = None
    ttl_seconds: int | None = None  # V3: explicit TTL override


class MemoryService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def admit(self, command: AdmitMemory) -> MemoryRecordModel:
        digest = canonical_hash(command.content)
        async with self._sessions() as session, session.begin():
            model = MemoryRecordModel(
                id=uuid.uuid4(),
                org_key=command.org_key,
                goal_id=command.goal_id,
                status="CANDIDATE",
                kind=command.kind,
                content_json=command.content,
                content_hash=digest,
                source_refs=list(command.source_refs or []),
                created_by=command.actor,
            )
            # Store TTL metadata if working memory or explicit TTL
            ttl = command.ttl_seconds
            if ttl is None:
                ttl_delta = _ttl_for_kind(command.kind)
                if ttl_delta is not None:
                    ttl = int(ttl_delta.total_seconds())
            if ttl is not None:
                model.content_json = {
                    **model.content_json,
                    "_ttl_seconds": ttl,
                    "_expires_at": (
                        datetime.now(UTC) + timedelta(seconds=ttl)
                    ).isoformat(),
                }
            session.add(model)
            await session.flush()
            return model

    async def verify(self, memory_id: uuid.UUID, *, actor: str) -> MemoryRecordModel:
        async with self._sessions() as session, session.begin():
            model = await session.get(MemoryRecordModel, memory_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "memory not found")
            if model.status not in {"CANDIDATE", "VERIFIED"}:
                raise DomainError(ErrorCode.INVALID_STATE, f"cannot verify from {model.status}")
            model.status = "VERIFIED"
            model.content_json = {
                **dict(model.content_json or {}),
                "_verified_by": actor,
            }
            await session.flush()
            return model

    async def revoke(self, memory_id: uuid.UUID, *, actor: str, reason: str) -> MemoryRecordModel:
        async with self._sessions() as session, session.begin():
            model = await session.get(MemoryRecordModel, memory_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "memory not found")
            model.status = "REVOKED"
            model.content_json = {
                **dict(model.content_json or {}),
                "_revoked_by": actor,
                "_revoke_reason": reason,
                "_revalidation_required": True,
            }
            await session.flush()
            return model

    async def list_org(self, org_key: str) -> list[MemoryRecordModel]:
        async with self._sessions() as session:
            return list(
                await session.scalars(
                    select(MemoryRecordModel)
                    .where(MemoryRecordModel.org_key == org_key)
                    .order_by(MemoryRecordModel.created_at.desc())
                )
            )

    # ------------------------------------------------------------------
    # V3 Memory Hierarchy queries
    # ------------------------------------------------------------------

    async def query_by_kind(
        self,
        org_key: str,
        kind_prefix: str,
        *,
        limit: int = 50,
    ) -> list[MemoryRecordModel]:
        """Query memories by kind prefix (e.g. 'episodic.' or 'semantic.rule')."""
        async with self._sessions() as session:
            stmt = (
                select(MemoryRecordModel)
                .where(
                    MemoryRecordModel.org_key == org_key,
                    MemoryRecordModel.kind.startswith(kind_prefix),
                    MemoryRecordModel.status.in_({"CANDIDATE", "VERIFIED"}),
                )
                .order_by(MemoryRecordModel.created_at.desc())
                .limit(limit)
            )
            return list(await session.scalars(stmt))

    async def query_by_goal(
        self,
        goal_id: uuid.UUID,
        *,
        kind_prefix: str | None = None,
        limit: int = 50,
    ) -> list[MemoryRecordModel]:
        """Query memories scoped to a specific goal."""
        async with self._sessions() as session:
            conditions = [
                MemoryRecordModel.goal_id == goal_id,
                MemoryRecordModel.status.in_({"CANDIDATE", "VERIFIED"}),
            ]
            if kind_prefix:
                conditions.append(
                    MemoryRecordModel.kind.startswith(kind_prefix)
                )
            stmt = (
                select(MemoryRecordModel)
                .where(*conditions)
                .order_by(MemoryRecordModel.created_at.desc())
                .limit(limit)
            )
            return list(await session.scalars(stmt))

    async def query_working(
        self,
        org_key: str,
        goal_id: uuid.UUID | None = None,
    ) -> list[MemoryRecordModel]:
        """Query working memories, filtering out expired entries."""
        results = await self.query_by_kind(org_key, "working.")
        now = datetime.now(UTC)
        active = []
        for mem in results:
            expires_at_str = (mem.content_json or {}).get("_expires_at")
            if expires_at_str:
                try:
                    expires_at = datetime.fromisoformat(expires_at_str)
                    if expires_at < now:
                        continue  # expired
                except ValueError:
                    pass
            if goal_id is not None and mem.goal_id != goal_id:
                continue
            active.append(mem)
        return active

    async def expire_stale_working(
        self,
        org_key: str,
        *,
        actor: str = "regent-core",
    ) -> int:
        """Mark expired working memories as EXPIRED. Returns count expired."""
        async with self._sessions() as session, session.begin():
            now = datetime.now(UTC)
            stmt = select(MemoryRecordModel).where(
                MemoryRecordModel.org_key == org_key,
                MemoryRecordModel.kind.startswith("working."),
                MemoryRecordModel.status.in_({"CANDIDATE", "VERIFIED"}),
            )
            memories = list(await session.scalars(stmt))
            expired_count = 0
            for mem in memories:
                expires_at_str = (mem.content_json or {}).get("_expires_at")
                if expires_at_str:
                    try:
                        expires_at = datetime.fromisoformat(expires_at_str)
                        if expires_at < now:
                            mem.status = "EXPIRED"
                            mem.content_json = {
                                **dict(mem.content_json or {}),
                                "_expired_by": actor,
                                "_expired_at": now.isoformat(),
                            }
                            expired_count += 1
                    except ValueError:
                        pass
            await session.flush()
            return expired_count

    # ------------------------------------------------------------------
    # P2-B: DecisionRecord-driven memory stage enablement
    # ------------------------------------------------------------------

    async def enable_memory_stage(
        self,
        org_key: str,
        *,
        decision_record_id: uuid.UUID | None = None,
        stage: str = "VERIFIED",
        actor: str = "regent-core",
    ) -> dict[str, Any]:
        """Enable memory stage based on a DecisionRecord.

        Promotes CANDIDATE memories to VERIFIED (or the specified stage)
        when a positive DecisionRecord exists from the eval harness.

        Returns count of promoted memories and the decision reference.
        """
        async with self._sessions() as session, session.begin():
            stmt = (
                select(MemoryRecordModel)
                .where(
                    MemoryRecordModel.org_key == org_key,
                    MemoryRecordModel.status == "CANDIDATE",
                )
            )
            candidates = list(await session.scalars(stmt))
            promoted = 0
            for mem in candidates:
                mem.status = stage
                mem.content_json = {
                    **dict(mem.content_json or {}),
                    "_promoted_by": actor,
                    "_promoted_stage": stage,
                    "_decision_record_id": str(decision_record_id) if decision_record_id else None,
                }
                promoted += 1
            await session.flush()
            return {
                "promoted_count": promoted,
                "stage": stage,
                "decision_record_id": str(decision_record_id) if decision_record_id else None,
                "org_key": org_key,
            }
