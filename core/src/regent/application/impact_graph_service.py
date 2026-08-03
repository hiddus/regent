"""P2-3 Impact Graph — cycle detection, cascade revoke, revalidation, decay.

Spec §16 / Security-Tenancy appendix §3:
Admission → Retrieval → Usage Trace → Impact Graph → Revocation → Revalidation
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import MemoryImpactEdgeModel, MemoryRecordModel

REVALIDATION_REQUIRED = "REVALIDATION_REQUIRED"
_HALF_LIFE_DAYS = 30.0


@dataclass(frozen=True, slots=True)
class ImpactEdge:
    from_memory_id: uuid.UUID
    to_memory_id: uuid.UUID
    edge_kind: str = "DERIVED_FROM"


class ImpactGraphService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def add_edge(
        self,
        *,
        org_key: str,
        from_memory_id: uuid.UUID,
        to_memory_id: uuid.UUID,
        edge_kind: str = "DERIVED_FROM",
    ) -> MemoryImpactEdgeModel:
        """Add an impact edge after cycle detection.

        Semantics: ``to_memory`` depends on / derives from ``from_memory``.
        """
        if from_memory_id == to_memory_id:
            raise DomainError(ErrorCode.POLICY_DENIED, "self-referential impact edge forbidden")
        if edge_kind not in {"DERIVED_FROM", "CITES", "SUPPORTS"}:
            raise DomainError(ErrorCode.INVALID_STATE, f"unknown edge kind {edge_kind}")

        async with self._sessions() as session, session.begin():
            for mid in (from_memory_id, to_memory_id):
                row = await session.get(MemoryRecordModel, mid)
                if row is None:
                    raise DomainError(ErrorCode.NOT_FOUND, f"memory {mid} not found")
                if row.org_key != org_key:
                    raise DomainError(ErrorCode.FORBIDDEN, "impact edge org mismatch")

            if await self._would_create_cycle(session, from_memory_id, to_memory_id):
                raise DomainError(
                    ErrorCode.POLICY_DENIED,
                    "impact edge would create a cycle",
                )

            existing = await session.scalar(
                select(MemoryImpactEdgeModel).where(
                    MemoryImpactEdgeModel.from_memory_id == from_memory_id,
                    MemoryImpactEdgeModel.to_memory_id == to_memory_id,
                    MemoryImpactEdgeModel.edge_kind == edge_kind,
                )
            )
            if existing is not None:
                return existing

            edge = MemoryImpactEdgeModel(
                id=uuid.uuid4(),
                org_key=org_key,
                from_memory_id=from_memory_id,
                to_memory_id=to_memory_id,
                edge_kind=edge_kind,
            )
            session.add(edge)
            await session.flush()
            return edge

    async def _would_create_cycle(
        self,
        session: AsyncSession,
        from_id: uuid.UUID,
        to_id: uuid.UUID,
    ) -> bool:
        """True if adding from→to creates a path to→…→from."""
        stack = [from_id]
        seen: set[uuid.UUID] = set()
        while stack:
            current = stack.pop()
            if current == to_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            parents = await session.scalars(
                select(MemoryImpactEdgeModel.from_memory_id).where(
                    MemoryImpactEdgeModel.to_memory_id == current
                )
            )
            stack.extend(list(parents))
        return False

    async def dependents(self, memory_id: uuid.UUID) -> list[uuid.UUID]:
        """Return transitive dependents (memories that derive from ``memory_id``)."""
        async with self._sessions() as session:
            return await self._collect_dependents(session, memory_id)

    async def _collect_dependents(
        self, session: AsyncSession, root: uuid.UUID
    ) -> list[uuid.UUID]:
        result: list[uuid.UUID] = []
        queue = [root]
        seen: set[uuid.UUID] = {root}
        while queue:
            current = queue.pop(0)
            children = list(
                await session.scalars(
                    select(MemoryImpactEdgeModel.to_memory_id).where(
                        MemoryImpactEdgeModel.from_memory_id == current
                    )
                )
            )
            for child in children:
                if child in seen:
                    continue
                seen.add(child)
                result.append(child)
                queue.append(child)
        return result

    async def revoke_cascade(
        self,
        memory_id: uuid.UUID,
        *,
        actor: str,
        reason: str,
    ) -> list[uuid.UUID]:
        """Revoke root memory and mark all dependents REVALIDATION_REQUIRED."""
        async with self._sessions() as session, session.begin():
            root = await session.get(MemoryRecordModel, memory_id)
            if root is None:
                raise DomainError(ErrorCode.NOT_FOUND, "memory not found")
            dependent_ids = await self._collect_dependents(session, memory_id)
            now = datetime.now(UTC).isoformat()
            root.status = "REVOKED"
            root.content_json = {
                **dict(root.content_json or {}),
                "_revoked_by": actor,
                "_revoke_reason": reason,
                "_revalidation_required": True,
                "_revalidation_status": REVALIDATION_REQUIRED,
                "_revoked_at": now,
            }
            touched = [memory_id]
            for dep_id in dependent_ids:
                dep = await session.get(MemoryRecordModel, dep_id)
                if dep is None:
                    continue
                dep.content_json = {
                    **dict(dep.content_json or {}),
                    "_revalidation_required": True,
                    "_revalidation_status": REVALIDATION_REQUIRED,
                    "_revalidation_source": str(memory_id),
                    "_revalidation_reason": reason,
                    "_revalidation_at": now,
                    "_revalidation_by": actor,
                }
                touched.append(dep_id)
            await session.flush()
            return touched

    async def batch_revoke(
        self,
        org_key: str,
        *,
        actor: str,
        reason: str,
        source_ref: str | None = None,
        parser_version: str | None = None,
        kind_prefix: str | None = None,
    ) -> list[uuid.UUID]:
        """Batch revoke by source_ref / parser_version / kind prefix, then cascade."""
        async with self._sessions() as session:
            stmt = select(MemoryRecordModel).where(
                MemoryRecordModel.org_key == org_key,
                MemoryRecordModel.status.in_({"CANDIDATE", "VERIFIED"}),
            )
            if kind_prefix:
                stmt = stmt.where(MemoryRecordModel.kind.startswith(kind_prefix))
            candidates = list(await session.scalars(stmt))

        matched: list[uuid.UUID] = []
        for mem in candidates:
            refs = list(mem.source_refs or [])
            content = dict(mem.content_json or {})
            if source_ref is not None:
                ref_hit = source_ref in {str(r) for r in refs} or any(
                    isinstance(r, dict) and str(r.get("ref") or r.get("id") or "") == source_ref
                    for r in refs
                )
                if not ref_hit:
                    continue
            if parser_version is not None and content.get("parser_version") != parser_version:
                continue
            matched.append(mem.id)

        touched: list[uuid.UUID] = []
        for mid in matched:
            touched.extend(
                await self.revoke_cascade(mid, actor=actor, reason=reason)
            )
        # stable unique
        seen: set[uuid.UUID] = set()
        unique: list[uuid.UUID] = []
        for mid in touched:
            if mid not in seen:
                seen.add(mid)
                unique.append(mid)
        return unique

    @staticmethod
    def confidence_decay(
        base_confidence: float,
        *,
        created_at: datetime,
        now: datetime | None = None,
        half_life_days: float = _HALF_LIFE_DAYS,
    ) -> float:
        """Exponential confidence decay with configurable half-life.

        Known limitation (TS §25): unit-tested only. Production MemoryService
        retrieve paths still order by ``created_at`` and do not apply decay to
        ranking or filtering. Do not treat this helper as live scoring.
        """
        clock = now or datetime.now(UTC)
        created = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
        age_days = max(0.0, (clock - created).total_seconds() / 86400.0)
        if half_life_days <= 0:
            return 0.0
        return max(0.0, min(1.0, base_confidence * math.pow(0.5, age_days / half_life_days)))

    @staticmethod
    def can_support_gate(memory: MemoryRecordModel) -> bool:
        """Downstream marked REVALIDATION_REQUIRED must not support PASSED Gate."""
        if memory.status in {"REVOKED", "EXPIRED", "SUPERSEDED"}:
            return False
        content = dict(memory.content_json or {})
        if content.get("_revalidation_required") is True:
            return False
        if content.get("_revalidation_status") == REVALIDATION_REQUIRED:
            return False
        return True

    async def repair_orphan_edges(self, org_key: str) -> int:
        """Remove edges whose endpoints no longer exist. Returns deleted count."""
        async with self._sessions() as session, session.begin():
            edges = list(
                await session.scalars(
                    select(MemoryImpactEdgeModel).where(
                        MemoryImpactEdgeModel.org_key == org_key
                    )
                )
            )
            deleted = 0
            for edge in edges:
                src = await session.get(MemoryRecordModel, edge.from_memory_id)
                dst = await session.get(MemoryRecordModel, edge.to_memory_id)
                if src is None or dst is None:
                    await session.delete(edge)
                    deleted += 1
            await session.flush()
            return deleted

    async def link_from_source_refs(
        self,
        *,
        org_key: str,
        memory_id: uuid.UUID,
        source_refs: list[Any],
    ) -> int:
        """Create DERIVED_FROM edges from UUID-like source_refs. Returns edge count."""
        linked = 0
        for ref in source_refs:
            raw = ref
            if isinstance(ref, dict):
                raw = ref.get("memory_id") or ref.get("id") or ref.get("ref")
            try:
                parent_id = uuid.UUID(str(raw))
            except (TypeError, ValueError):
                continue
            if parent_id == memory_id:
                continue
            try:
                await self.add_edge(
                    org_key=org_key,
                    from_memory_id=parent_id,
                    to_memory_id=memory_id,
                    edge_kind="DERIVED_FROM",
                )
                linked += 1
            except DomainError:
                # Missing parent or cycle — skip; admit already persisted.
                continue
        return linked
