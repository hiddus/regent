"""Durable ExecutionPlanItem for long-running agent work (PRD §10.4 / Spec §18.6).

Persists status/owner/dependencies/evidence_refs/next_action/version and
survives Worker restart + context compaction via Goal/Run checkpoint restore.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import ExecutionPlanItemModel

PLAN_SCHEMA_VERSION = "execution-plan-item/v1"
_TERMINAL = frozenset({"completed", "cancelled", "failed"})


@dataclass(frozen=True, slots=True)
class UpsertPlanItem:
    goal_id: uuid.UUID
    item_key: str
    content: str
    status: str = "pending"
    owner_agent_id: str | None = None
    run_id: uuid.UUID | None = None
    dependencies: Sequence[str] = ()
    evidence_refs: Sequence[str] = ()
    next_action: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PlanItemView:
    id: uuid.UUID
    goal_id: uuid.UUID
    run_id: uuid.UUID | None
    item_key: str
    content: str
    status: str
    owner_agent_id: str | None
    dependencies: list[str]
    evidence_refs: list[str]
    next_action: str | None
    version: int
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "goal_id": str(self.goal_id),
            "run_id": str(self.run_id) if self.run_id else None,
            "item_key": self.item_key,
            "content": self.content,
            "status": self.status,
            "owner_agent_id": self.owner_agent_id,
            "dependencies": list(self.dependencies),
            "evidence_refs": list(self.evidence_refs),
            "next_action": self.next_action,
            "version": self.version,
            "metadata": dict(self.metadata),
        }


class ExecutionPlanService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def upsert_items(self, items: Sequence[UpsertPlanItem]) -> list[PlanItemView]:
        if not items:
            return []
        views: list[PlanItemView] = []
        async with self._sessions() as session, session.begin():
            for item in items:
                views.append(await self._upsert_one(session, item))
        return views

    async def _upsert_one(
        self, session: AsyncSession, item: UpsertPlanItem
    ) -> PlanItemView:
        existing = await session.scalar(
            select(ExecutionPlanItemModel).where(
                ExecutionPlanItemModel.goal_id == item.goal_id,
                ExecutionPlanItemModel.item_key == item.item_key,
            )
        )
        now = datetime.now(UTC)
        if existing is None:
            model = ExecutionPlanItemModel(
                id=uuid.uuid4(),
                goal_id=item.goal_id,
                run_id=item.run_id,
                item_key=item.item_key,
                content=item.content,
                status=item.status,
                owner_agent_id=item.owner_agent_id,
                dependencies=list(item.dependencies),
                evidence_refs=list(item.evidence_refs),
                next_action=item.next_action,
                version=1,
                metadata_json={
                    **dict(item.metadata or {}),
                    "schema_version": PLAN_SCHEMA_VERSION,
                },
                updated_at=now,
            )
            session.add(model)
            await session.flush()
            return _to_view(model)

        # Terminal plan evidence is immutable under ordinary upsert. Corrections require
        # a new item/versioned administrative workflow rather than rewriting history.
        if existing.status in _TERMINAL:
            if item.status != existing.status:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    f"cannot rewrite terminal plan item {item.item_key} from "
                    f"{existing.status} to {item.status}",
                )
            return _to_view(existing)
        existing.content = item.content
        existing.status = item.status
        existing.owner_agent_id = item.owner_agent_id
        existing.run_id = item.run_id or existing.run_id
        existing.dependencies = list(item.dependencies)
        existing.evidence_refs = list(item.evidence_refs)
        existing.next_action = item.next_action
        existing.version = int(existing.version) + 1
        meta = dict(existing.metadata_json or {})
        meta.update(dict(item.metadata or {}))
        meta["schema_version"] = PLAN_SCHEMA_VERSION
        existing.metadata_json = meta
        existing.updated_at = now
        await session.flush()
        return _to_view(existing)

    async def checkpoint(self, goal_id: uuid.UUID) -> dict[str, Any]:
        items = await self.list_items(goal_id)
        return {
            "schema_version": PLAN_SCHEMA_VERSION,
            "goal_id": str(goal_id),
            "items": [i.as_dict() for i in items],
            "open_item_keys": [
                i.item_key for i in items if i.status not in _TERMINAL
            ],
            "completed_item_keys": [
                i.item_key for i in items if i.status == "completed"
            ],
        }

    async def restore_from_checkpoint(
        self, checkpoint: dict[str, Any]
    ) -> list[PlanItemView]:
        goal_id = uuid.UUID(str(checkpoint["goal_id"]))
        upserts = []
        for raw in checkpoint.get("items") or []:
            upserts.append(
                UpsertPlanItem(
                    goal_id=goal_id,
                    item_key=str(raw["item_key"]),
                    content=str(raw.get("content") or ""),
                    status=str(raw.get("status") or "pending"),
                    owner_agent_id=raw.get("owner_agent_id"),
                    run_id=uuid.UUID(raw["run_id"]) if raw.get("run_id") else None,
                    dependencies=list(raw.get("dependencies") or []),
                    evidence_refs=list(raw.get("evidence_refs") or []),
                    next_action=raw.get("next_action"),
                    metadata=dict(raw.get("metadata") or {}),
                )
            )
        # Restore uses direct write that preserves terminal statuses.
        views: list[PlanItemView] = []
        async with self._sessions() as session, session.begin():
            for item in upserts:
                existing = await session.scalar(
                    select(ExecutionPlanItemModel).where(
                        ExecutionPlanItemModel.goal_id == item.goal_id,
                        ExecutionPlanItemModel.item_key == item.item_key,
                    )
                )
                if existing is None:
                    views.append(await self._upsert_one(session, item))
                    continue
                # Preserve completed side-effect markers: do not downgrade completed.
                if existing.status == "completed":
                    views.append(_to_view(existing))
                    continue
                views.append(await self._upsert_one(session, item))
        return views

    async def list_items(self, goal_id: uuid.UUID) -> list[PlanItemView]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(ExecutionPlanItemModel)
                .where(ExecutionPlanItemModel.goal_id == goal_id)
                .order_by(ExecutionPlanItemModel.item_key.asc())
            )
            return [_to_view(m) for m in rows]

    async def next_runnable(self, goal_id: uuid.UUID) -> list[PlanItemView]:
        items = await self.list_items(goal_id)
        completed = {i.item_key for i in items if i.status == "completed"}
        runnable = []
        for item in items:
            if item.status not in {"pending", "in_progress"}:
                continue
            deps = set(item.dependencies)
            if deps <= completed:
                runnable.append(item)
        return runnable


def _to_view(model: ExecutionPlanItemModel) -> PlanItemView:
    return PlanItemView(
        id=model.id,
        goal_id=model.goal_id,
        run_id=model.run_id,
        item_key=model.item_key,
        content=model.content,
        status=model.status,
        owner_agent_id=model.owner_agent_id,
        dependencies=list(model.dependencies or []),
        evidence_refs=list(model.evidence_refs or []),
        next_action=model.next_action,
        version=int(model.version),
        metadata=dict(model.metadata_json or {}),
    )
