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

from regent.application.impact_graph_service import ImpactGraphService
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
    verified: bool = False


class MemoryService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._impact = ImpactGraphService(sessions)

    async def admit(self, command: AdmitMemory) -> MemoryRecordModel:
        digest = canonical_hash(command.content)
        async with self._sessions() as session, session.begin():
            model = MemoryRecordModel(
                id=uuid.uuid4(),
                org_key=command.org_key,
                goal_id=command.goal_id,
                status="VERIFIED" if command.verified else "CANDIDATE",
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
            memory_id = model.id
            source_refs = list(command.source_refs or [])
            org_key = command.org_key
        # Impact Graph edges outside the admit transaction (cycle checks need committed parent).
        if source_refs:
            await self._impact.link_from_source_refs(
                org_key=org_key,
                memory_id=memory_id,
                source_refs=source_refs,
            )
        async with self._sessions() as session:
            refreshed = await session.get(MemoryRecordModel, memory_id)
            assert refreshed is not None
            return refreshed

    async def reinforce_semantic(
        self,
        command: AdmitMemory,
        *,
        memory_key: str,
        verification_threshold: int = 2,
    ) -> MemoryRecordModel:
        """Accumulate independent evidence and promote a repeatable pattern.

        A single successful run creates a candidate. The same stable pattern
        must be observed on at least ``verification_threshold`` distinct Goals
        before it can influence planning as VERIFIED memory.
        """
        if not command.kind.startswith("semantic."):
            raise ValueError("reinforce_semantic requires a semantic memory kind")
        key = str(memory_key).strip()
        if not key:
            raise ValueError("memory_key is required")
        async with self._sessions() as session, session.begin():
            rows = list(
                await session.scalars(
                    select(MemoryRecordModel).where(
                        MemoryRecordModel.org_key == command.org_key,
                        MemoryRecordModel.kind == command.kind,
                        MemoryRecordModel.status.in_({"CANDIDATE", "VERIFIED"}),
                    )
                )
            )
            model = next(
                (
                    row
                    for row in rows
                    if str((row.content_json or {}).get("_memory_key") or "") == key
                ),
                None,
            )
            goal_ref = str(command.goal_id) if command.goal_id else ""
            if model is None:
                evidence = [goal_ref] if goal_ref else []
                content = {
                    **command.content,
                    "_memory_key": key,
                    "_evidence_goal_ids": evidence,
                    "_observation_count": len(evidence) or 1,
                }
                model = MemoryRecordModel(
                    id=uuid.uuid4(),
                    org_key=command.org_key,
                    goal_id=command.goal_id,
                    status=(
                        "VERIFIED" if verification_threshold <= 1 else "CANDIDATE"
                    ),
                    kind=command.kind,
                    content_json=content,
                    content_hash=canonical_hash(content),
                    source_refs=list(command.source_refs or []),
                    created_by=command.actor,
                )
                session.add(model)
            else:
                content = dict(model.content_json or {})
                evidence = list(content.get("_evidence_goal_ids") or [])
                if goal_ref and goal_ref not in evidence:
                    evidence.append(goal_ref)
                count = max(int(content.get("_observation_count") or 0), len(evidence))
                if not goal_ref:
                    count += 1
                content.update(command.content)
                content.update(
                    {
                        "_memory_key": key,
                        "_evidence_goal_ids": evidence[-20:],
                        "_observation_count": count,
                    }
                )
                model.content_json = content
                model.content_hash = canonical_hash(content)
                if count >= max(1, int(verification_threshold)):
                    model.status = "VERIFIED"
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
        await self._impact.revoke_cascade(memory_id, actor=actor, reason=reason)
        async with self._sessions() as session:
            model = await session.get(MemoryRecordModel, memory_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "memory not found")
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
        verified_only: bool = False,
    ) -> list[MemoryRecordModel]:
        """Query memories by kind prefix (e.g. 'episodic.' or 'semantic.rule')."""
        async with self._sessions() as session:
            statuses = {"VERIFIED"} if verified_only else {"CANDIDATE", "VERIFIED"}
            stmt = (
                select(MemoryRecordModel)
                .where(
                    MemoryRecordModel.org_key == org_key,
                    MemoryRecordModel.kind.startswith(kind_prefix),
                    MemoryRecordModel.status.in_(statuses),
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
