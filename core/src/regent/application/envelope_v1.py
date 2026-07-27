"""AAR-1 Envelope v1 — RFC8785-style canonical JSON + HMAC-SHA256."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.aar1_models import EnvelopeNonceModel

SCHEMA_VERSION = "envelope/v1"


def canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class EnvelopeV1:
    schema_version: str
    message_id: str
    issued_at: str
    expires_at: str
    nonce: str
    goal_id: str
    organization_version_id: str
    source_deployment_id: str
    target_deployment_id: str
    capability_scope: list[str]
    permit_refs: list[str]
    payload_ref: str | None
    payload_digest: str
    idempotency_key: str
    correlation_id: str
    causation_id: str | None
    signing_key_id: str
    signature: str

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "message_id": self.message_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
            "goal_id": self.goal_id,
            "organization_version_id": self.organization_version_id,
            "source_deployment_id": self.source_deployment_id,
            "target_deployment_id": self.target_deployment_id,
            "capability_scope": list(self.capability_scope),
            "permit_refs": list(self.permit_refs),
            "payload_ref": self.payload_ref,
            "payload_digest": self.payload_digest,
            "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "signing_key_id": self.signing_key_id,
        }

    def to_dict(self) -> dict[str, Any]:
        d = self.unsigned_dict()
        d["signature"] = self.signature
        return d


def sign_envelope(
    fields: dict[str, Any],
    *,
    secret: bytes,
    signing_key_id: str,
) -> EnvelopeV1:
    unsigned = {**fields, "signing_key_id": signing_key_id}
    unsigned.pop("signature", None)
    digest = hmac.new(secret, canonical_json_bytes(unsigned), hashlib.sha256).hexdigest()
    return EnvelopeV1(**{**unsigned, "signature": digest})  # type: ignore[arg-type]


def verify_envelope(
    envelope: EnvelopeV1 | dict[str, Any],
    *,
    secret: bytes,
    known_key_ids: set[str] | None = None,
    now: datetime | None = None,
    parent_scope: set[str] | None = None,
) -> EnvelopeV1:
    data = envelope.to_dict() if isinstance(envelope, EnvelopeV1) else dict(envelope)
    signature = str(data.get("signature") or "")
    unsigned = {k: v for k, v in data.items() if k != "signature"}
    key_id = str(unsigned.get("signing_key_id") or "")
    if known_key_ids is not None and key_id not in known_key_ids:
        raise DomainError(ErrorCode.ENVELOPE_TAMPERED, f"unknown signing key id {key_id}")

    expected = hmac.new(secret, canonical_json_bytes(unsigned), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise DomainError(ErrorCode.ENVELOPE_TAMPERED, "signature mismatch")

    current = now or datetime.now(UTC)
    expires_at = datetime.fromisoformat(str(unsigned["expires_at"]))
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if current > expires_at:
        raise DomainError(ErrorCode.ENVELOPE_EXPIRED, "envelope expired")

    scope = set(unsigned.get("capability_scope") or [])
    if parent_scope is not None and not scope.issubset(parent_scope):
        raise DomainError(ErrorCode.CAPABILITY_SCOPE_ESCALATION, "scope not subset of parent")

    return EnvelopeV1(**{**unsigned, "signature": signature})  # type: ignore[arg-type]


def build_unsigned_fields(
    *,
    goal_id: uuid.UUID,
    organization_version_id: uuid.UUID,
    source_deployment_id: uuid.UUID,
    target_deployment_id: uuid.UUID,
    capability_scope: list[str],
    permit_refs: list[str] | None = None,
    payload: dict[str, Any] | None = None,
    payload_ref: str | None = None,
    idempotency_key: str,
    correlation_id: str,
    causation_id: str | None = None,
    ttl_seconds: int = 300,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    body = payload or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "message_id": str(uuid.uuid4()),
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        "nonce": secrets.token_hex(16),
        "goal_id": str(goal_id),
        "organization_version_id": str(organization_version_id),
        "source_deployment_id": str(source_deployment_id),
        "target_deployment_id": str(target_deployment_id),
        "capability_scope": sorted(capability_scope),
        "permit_refs": list(permit_refs or []),
        "payload_ref": payload_ref,
        "payload_digest": payload_digest(body),
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "causation_id": causation_id,
    }


async def remember_nonce(
    session: AsyncSession,
    *,
    nonce: str,
    signing_key_id: str,
    message_id: str,
    expires_at: datetime,
) -> None:
    existing = await session.scalar(
        select(EnvelopeNonceModel).where(
            EnvelopeNonceModel.nonce == nonce,
            EnvelopeNonceModel.signing_key_id == signing_key_id,
        )
    )
    if existing is not None:
        raise DomainError(ErrorCode.ENVELOPE_REPLAYED, "nonce already used")
    session.add(
        EnvelopeNonceModel(
            id=uuid.uuid4(),
            nonce=nonce,
            signing_key_id=signing_key_id,
            message_id=message_id,
            expires_at=expires_at,
        )
    )
