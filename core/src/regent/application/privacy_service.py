"""PRD §7.4 — Goal Owner export package and delete request with audit receipt."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AuditRecordModel,
    ConversationMessageModel,
    ConversationModel,
    EvidenceModel,
    GoalModel,
)


@dataclass(frozen=True, slots=True)
class GoalExportPackage:
    goal_id: uuid.UUID
    exported_at: str
    owner: str
    goal: dict[str, Any]
    conversations: list[dict[str, Any]]
    evidence: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal_id": str(self.goal_id),
            "exported_at": self.exported_at,
            "owner": self.owner,
            "goal": self.goal,
            "conversations": self.conversations,
            "evidence": self.evidence,
            "pii_minimized": True,
        }


@dataclass(frozen=True, slots=True)
class GoalDeleteReceipt:
    goal_id: uuid.UUID
    status: str
    requested_by: str
    audit_id: uuid.UUID
    replayed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal_id": str(self.goal_id),
            "status": self.status,
            "requested_by": self.requested_by,
            "audit_id": str(self.audit_id),
            "replayed": self.replayed,
        }


def _minimize_text(value: str, *, max_len: int = 200) -> str:
    text = (value or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


class PrivacyService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def export_goal(self, goal_id: uuid.UUID, *, requester: str) -> GoalExportPackage:
        async with self._sessions() as session:
            goal = await session.scalar(
                select(GoalModel)
                .options(selectinload(GoalModel.specs))
                .where(GoalModel.id == goal_id)
            )
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"goal {goal_id} not found")
            if goal.created_by != requester:
                raise DomainError(ErrorCode.FORBIDDEN, "only Goal Owner may export")

            conversations = list(
                await session.scalars(
                    select(ConversationModel).where(ConversationModel.goal_id == goal_id)
                )
            )
            convo_payload: list[dict[str, Any]] = []
            for convo in conversations:
                messages = list(
                    await session.scalars(
                        select(ConversationMessageModel).where(
                            ConversationMessageModel.conversation_id == convo.id
                        )
                    )
                )
                convo_payload.append(
                    {
                        "id": str(convo.id),
                        "status": getattr(convo, "status", None),
                        "message_count": len(messages),
                        "messages": [
                            {
                                "id": str(msg.id),
                                "role": getattr(msg, "role", None),
                                # PII-minimized: truncate body; no raw emails/phones extracted.
                                "content_preview": _minimize_text(
                                    str(getattr(msg, "content", "") or ""),
                                    max_len=120,
                                ),
                                "created_at": (
                                    msg.created_at.isoformat()
                                    if getattr(msg, "created_at", None)
                                    else None
                                ),
                            }
                            for msg in messages
                        ],
                    }
                )

            evidence_rows = list(
                await session.scalars(select(EvidenceModel).where(EvidenceModel.goal_id == goal_id))
            )
            evidence_payload = [
                {
                    "id": str(row.id),
                    "evidence_type": row.evidence_type,
                    "quality_tier": row.quality_tier,
                    "content_hash": row.content_hash,
                    "producer_ref": row.producer_ref,
                    "uri": row.uri,
                    # Omit raw payload bodies (may contain untrusted external content).
                }
                for row in evidence_rows
            ]

            specs = sorted(goal.specs, key=lambda s: s.version)
            latest = specs[-1] if specs else None
            return GoalExportPackage(
                goal_id=goal.id,
                exported_at=datetime.now(UTC).isoformat(),
                owner=goal.created_by,
                goal={
                    "id": str(goal.id),
                    "status": goal.status,
                    "original_input_preview": _minimize_text(goal.original_input),
                    "created_by": goal.created_by,
                    "correlation_id": str(goal.correlation_id),
                    "spec_version": None if latest is None else latest.version,
                    "spec_status": None if latest is None else latest.status,
                    "success_criteria_keys": sorted((latest.success_criteria or {}).keys())
                    if latest
                    else [],
                },
                conversations=convo_payload,
                evidence=evidence_payload,
            )

    async def request_delete(self, goal_id: uuid.UUID, *, requester: str) -> GoalDeleteReceipt:
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id, with_for_update=True)
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"goal {goal_id} not found")
            if goal.created_by != requester:
                raise DomainError(ErrorCode.FORBIDDEN, "only Goal Owner may delete")

            meta = dict(goal.metadata_json or {})
            existing = meta.get("delete_request")
            if isinstance(existing, dict) and existing.get("status") == "DELETE_REQUESTED":
                return GoalDeleteReceipt(
                    goal_id=goal.id,
                    status="DELETE_REQUESTED",
                    requested_by=str(existing.get("requested_by") or requester),
                    audit_id=uuid.UUID(str(existing["audit_id"])),
                    replayed=True,
                )

            audit_id = uuid.uuid4()
            meta["delete_request"] = {
                "status": "DELETE_REQUESTED",
                "requested_by": requester,
                "requested_at": datetime.now(UTC).isoformat(),
                "audit_id": str(audit_id),
            }
            goal.metadata_json = meta
            if goal.status not in {"CANCELLED", "FAILED", "EXHAUSTED", "ACHIEVED"}:
                goal.status = "CANCELLED"
            session.add(
                AuditRecordModel(
                    id=audit_id,
                    aggregate_type="goal",
                    aggregate_id=goal.id,
                    aggregate_version=goal.version,
                    action="DELETE_REQUESTED",
                    actor=requester,
                    payload={
                        "status": "DELETE_REQUESTED",
                        "pii_minimized_export_required": True,
                    },
                    correlation_id=goal.correlation_id,
                )
            )
            return GoalDeleteReceipt(
                goal_id=goal.id,
                status="DELETE_REQUESTED",
                requested_by=requester,
                audit_id=audit_id,
                replayed=False,
            )
