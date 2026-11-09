"""PRD §7 — privacy: consent (§7.1), PII classification (§7.2), retention (§7.3), export/delete (§7.4)."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from regent.config import get_settings
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AuditRecordModel,
    ConversationMessageModel,
    ConversationModel,
    EvidenceModel,
    GoalModel,
    ObservationModel,
    PrivacyConsentModel,
)

NOTICE_VERSION = "privacy-notice-v1"
DEFAULT_SCOPES = ("observation", "evidence", "conversation")

PRIVACY_NOTICE_TEXT = (
    "Regent 将为本 Goal 收集 Observation、Evidence 与对话内容，用于目标执行、"
    "验证、审计与改进。数据按 PII 分级最小化处理，保留期满后匿名化或删除。"
    "Goal Owner 可随时撤回同意；撤回后停止新采集，既有数据仍可按 §7.4 导出/删除。"
)


class PiiClass(StrEnum):
    """PRD §7.2 field classification tiers."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    RESTRICTED = "RESTRICTED"


# Default field-level policy: sensitive identifiers are RESTRICTED (not collected by default).
PII_FIELD_POLICY: dict[str, PiiClass] = {
    "email": PiiClass.RESTRICTED,
    "phone": PiiClass.RESTRICTED,
    "phone_cn": PiiClass.RESTRICTED,
    "id_card": PiiClass.RESTRICTED,
    "id_card_cn": PiiClass.RESTRICTED,
    "credit_card": PiiClass.RESTRICTED,
    "ipv4": PiiClass.INTERNAL,
    "display_name": PiiClass.INTERNAL,
    "metric_name": PiiClass.PUBLIC,
    "content_hash": PiiClass.PUBLIC,
    "producer_ref": PiiClass.INTERNAL,
}

_PII_DETECT: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone_cn": re.compile(r"\b(?:(?:\+86)|(?:86))?1[3-9]\d{9}\b"),
    "id_card_cn": re.compile(
        r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"
    ),
    "credit_card": re.compile(
        r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b"
    ),
}


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


@dataclass(frozen=True, slots=True)
class ConsentRecord:
    id: uuid.UUID
    goal_id: uuid.UUID
    subject: str
    status: str
    notice_version: str
    scopes: list[str]
    granted_at: str
    withdrawn_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "goal_id": str(self.goal_id),
            "subject": self.subject,
            "status": self.status,
            "notice_version": self.notice_version,
            "scopes": list(self.scopes),
            "granted_at": self.granted_at,
            "withdrawn_at": self.withdrawn_at,
        }


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    classification: PiiClass
    findings: list[str]
    minimized_text: str
    contains_restricted: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "findings": list(self.findings),
            "minimized_text": self.minimized_text,
            "contains_restricted": self.contains_restricted,
        }


def _minimize_text(value: str, *, max_len: int = 200) -> str:
    text = (value or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def privacy_notice() -> dict[str, Any]:
    return {
        "notice_version": NOTICE_VERSION,
        "notice_text": PRIVACY_NOTICE_TEXT,
        "scopes": list(DEFAULT_SCOPES),
        "pii_field_policy": {k: v.value for k, v in PII_FIELD_POLICY.items()},
        "retention_days_default": get_settings().privacy_retention_days,
    }


def classify_and_minimize(text: str, *, max_len: int = 200) -> ClassificationResult:
    """PRD §7.2 — detect PII, classify, and redact RESTRICTED spans."""
    raw = text or ""
    findings: list[str] = []
    redacted = raw
    worst = PiiClass.PUBLIC
    for kind, pattern in _PII_DETECT.items():
        matches = pattern.findall(raw)
        if not matches:
            continue
        findings.append(kind)
        tier = PII_FIELD_POLICY.get(kind, PiiClass.RESTRICTED)
        if tier == PiiClass.RESTRICTED:
            worst = PiiClass.RESTRICTED
            redacted = pattern.sub(f"[REDACTED:{kind}]", redacted)
        elif tier == PiiClass.INTERNAL and worst == PiiClass.PUBLIC:
            worst = PiiClass.INTERNAL
    return ClassificationResult(
        classification=worst,
        findings=findings,
        minimized_text=_minimize_text(redacted, max_len=max_len),
        contains_restricted=worst == PiiClass.RESTRICTED,
    )


class PrivacyService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    # ------------------------------------------------------------------ §7.1
    async def grant_consent(
        self,
        goal_id: uuid.UUID,
        *,
        subject: str,
        scopes: list[str] | None = None,
    ) -> ConsentRecord:
        scope_list = list(scopes or DEFAULT_SCOPES)
        for scope in scope_list:
            if scope not in DEFAULT_SCOPES:
                raise DomainError(ErrorCode.INVALID_STATE, f"unknown privacy scope: {scope}")
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"goal {goal_id} not found")
            if goal.created_by != subject:
                raise DomainError(ErrorCode.FORBIDDEN, "only Goal Owner may grant consent")

            existing = await session.scalar(
                select(PrivacyConsentModel).where(
                    PrivacyConsentModel.goal_id == goal_id,
                    PrivacyConsentModel.subject == subject,
                )
            )
            now = datetime.now(UTC)
            if existing is None:
                consent_id = uuid.uuid4()
                row = PrivacyConsentModel(
                    id=consent_id,
                    goal_id=goal_id,
                    subject=subject,
                    notice_version=NOTICE_VERSION,
                    notice_text=PRIVACY_NOTICE_TEXT,
                    scopes={"allowed": scope_list},
                    status="GRANTED",
                    granted_at=now,
                    withdrawn_at=None,
                )
                session.add(row)
            else:
                existing.status = "GRANTED"
                existing.notice_version = NOTICE_VERSION
                existing.notice_text = PRIVACY_NOTICE_TEXT
                existing.scopes = {"allowed": scope_list}
                existing.granted_at = now
                existing.withdrawn_at = None
                consent_id = existing.id

            session.add(
                AuditRecordModel(
                    id=uuid.uuid4(),
                    aggregate_type="goal",
                    aggregate_id=goal_id,
                    aggregate_version=goal.version,
                    action="PRIVACY_CONSENT_GRANTED",
                    actor=subject,
                    payload={
                        "notice_version": NOTICE_VERSION,
                        "scopes": scope_list,
                    },
                    correlation_id=goal.correlation_id,
                )
            )
            return ConsentRecord(
                id=consent_id,
                goal_id=goal_id,
                subject=subject,
                status="GRANTED",
                notice_version=NOTICE_VERSION,
                scopes=scope_list,
                granted_at=now.isoformat(),
                withdrawn_at=None,
            )

    async def withdraw_consent(
        self, goal_id: uuid.UUID, *, subject: str
    ) -> ConsentRecord:
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"goal {goal_id} not found")
            if goal.created_by != subject:
                raise DomainError(ErrorCode.FORBIDDEN, "only Goal Owner may withdraw consent")
            row = await session.scalar(
                select(PrivacyConsentModel).where(
                    PrivacyConsentModel.goal_id == goal_id,
                    PrivacyConsentModel.subject == subject,
                )
            )
            if row is None:
                raise DomainError(ErrorCode.NOT_FOUND, "no consent record to withdraw")
            now = datetime.now(UTC)
            row.status = "WITHDRAWN"
            row.withdrawn_at = now
            scopes = list((row.scopes or {}).get("allowed") or DEFAULT_SCOPES)
            session.add(
                AuditRecordModel(
                    id=uuid.uuid4(),
                    aggregate_type="goal",
                    aggregate_id=goal_id,
                    aggregate_version=goal.version,
                    action="PRIVACY_CONSENT_WITHDRAWN",
                    actor=subject,
                    payload={"notice_version": row.notice_version, "scopes": scopes},
                    correlation_id=goal.correlation_id,
                )
            )
            return ConsentRecord(
                id=row.id,
                goal_id=goal_id,
                subject=subject,
                status="WITHDRAWN",
                notice_version=row.notice_version,
                scopes=scopes,
                granted_at=row.granted_at.isoformat() if row.granted_at else now.isoformat(),
                withdrawn_at=now.isoformat(),
            )

    async def get_consent(
        self, goal_id: uuid.UUID, *, subject: str
    ) -> ConsentRecord | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(PrivacyConsentModel).where(
                    PrivacyConsentModel.goal_id == goal_id,
                    PrivacyConsentModel.subject == subject,
                )
            )
            if row is None:
                return None
            scopes = list((row.scopes or {}).get("allowed") or DEFAULT_SCOPES)
            return ConsentRecord(
                id=row.id,
                goal_id=row.goal_id,
                subject=row.subject,
                status=row.status,
                notice_version=row.notice_version,
                scopes=scopes,
                granted_at=row.granted_at.isoformat() if row.granted_at else "",
                withdrawn_at=row.withdrawn_at.isoformat() if row.withdrawn_at else None,
            )

    async def require_consent_for_scope(
        self, goal_id: uuid.UUID, *, scope: str
    ) -> None:
        """Fail closed when enforcement is on and consent is missing/withdrawn/out-of-scope."""
        if not get_settings().privacy_consent_enforced:
            return
        if scope not in DEFAULT_SCOPES:
            raise DomainError(ErrorCode.INVALID_STATE, f"unknown privacy scope: {scope}")
        async with self._sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"goal {goal_id} not found")
            row = await session.scalar(
                select(PrivacyConsentModel).where(
                    PrivacyConsentModel.goal_id == goal_id,
                    PrivacyConsentModel.subject == goal.created_by,
                )
            )
            if row is None or row.status != "GRANTED":
                raise DomainError(
                    ErrorCode.POLICY_DENIED,
                    f"privacy consent required for scope={scope} (PRD §7.1)",
                )
            allowed = list((row.scopes or {}).get("allowed") or [])
            if scope not in allowed:
                raise DomainError(
                    ErrorCode.POLICY_DENIED,
                    f"privacy consent does not include scope={scope}",
                )

    async def ensure_owner_consent_on_create(
        self, goal_id: uuid.UUID, *, subject: str
    ) -> ConsentRecord:
        """Record notice+grant when Goal Owner creates a Goal (collection prerequisite)."""
        return await self.grant_consent(goal_id, subject=subject)

    # ------------------------------------------------------------------ §7.2
    def classify_text(self, text: str) -> ClassificationResult:
        return classify_and_minimize(text)

    def reject_restricted_payload(self, text: str, *, context: str) -> str:
        """Default: do not collect RESTRICTED PII; redact and keep INTERNAL/PUBLIC."""
        result = classify_and_minimize(text, max_len=10_000)
        if result.contains_restricted:
            # Minimization policy: store redacted form, never raw RESTRICTED spans.
            return result.minimized_text
        return text

    # ------------------------------------------------------------------ §7.3
    async def anonymize_expired(
        self,
        *,
        now: datetime | None = None,
        retention_days: int | None = None,
        limit: int = 500,
    ) -> dict[str, int]:
        """Anonymize Observations past retention; returns counts."""
        clock = now or datetime.now(UTC)
        days = retention_days if retention_days is not None else get_settings().privacy_retention_days
        cutoff = clock - timedelta(days=days)
        anonymized = 0
        async with self._sessions() as session, session.begin():
            rows = list(
                await session.scalars(
                    select(ObservationModel)
                    .where(
                        ObservationModel.anonymized_at.is_(None),
                        ObservationModel.observed_at < cutoff,
                    )
                    .limit(limit)
                )
            )
            for row in rows:
                digest = hashlib.sha256(
                    f"{row.id}:{row.event_id}:{row.source}".encode()
                ).hexdigest()[:16]
                row.metric_value = {
                    "anonymized": True,
                    "retention_days": days,
                    "value_fingerprint": digest,
                }
                row.source = f"anonymized:{digest}"
                row.anonymized_at = clock
                anonymized += 1
                if row.goal_id is not None:
                    session.add(
                        AuditRecordModel(
                            id=uuid.uuid4(),
                            aggregate_type="observation",
                            aggregate_id=row.id,
                            aggregate_version=1,
                            action="PRIVACY_OBSERVATION_ANONYMIZED",
                            actor="privacy-retention-worker",
                            payload={
                                "retention_days": days,
                                "goal_id": str(row.goal_id),
                            },
                            correlation_id=row.goal_id,
                        )
                    )
        return {"observations_anonymized": anonymized, "retention_days": days}

    # ------------------------------------------------------------------ §7.4
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
                                "content_preview": classify_and_minimize(
                                    str(getattr(msg, "content", "") or ""),
                                    max_len=120,
                                ).minimized_text,
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
                    "pii_class": PII_FIELD_POLICY.get("content_hash", PiiClass.PUBLIC).value,
                }
                for row in evidence_rows
            ]

            specs = sorted(goal.specs, key=lambda s: s.version)
            latest = specs[-1] if specs else None
            input_class = classify_and_minimize(goal.original_input)
            return GoalExportPackage(
                goal_id=goal.id,
                exported_at=datetime.now(UTC).isoformat(),
                owner=goal.created_by,
                goal={
                    "id": str(goal.id),
                    "status": goal.status,
                    "original_input_preview": input_class.minimized_text,
                    "original_input_pii_class": input_class.classification.value,
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
