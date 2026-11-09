"""P3-A: AgentEnvelope — inter-agent message encapsulation.

Ensures:
- source/dest agent identification
- capability_scope propagation (child ⊆ parent)
- permit_refs for authorization delegation
- content_trust for message integrity
- optional Spec §17 HMAC (via envelope_v1) + correlation_id when key configured
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from regent.application.envelope_v1 import (
    build_unsigned_fields,
    sign_envelope,
    verify_envelope,
)


@dataclass(frozen=True, slots=True)
class AgentEnvelope:
    """Encapsulated message between agents with authorization context.

    The envelope ensures that:
    - Messages are addressed to specific agents (source/dest)
    - Capability scope only decreases (child ⊆ parent ∩ GoalSpec)
    - Permit references track authorization chain
    - Content trust is verified via digest and optional HMAC-SHA256
    """

    envelope_id: uuid.UUID
    source_agent: str
    dest_agent: str
    capability_scope: frozenset[str]
    permit_refs: list[str] = field(default_factory=list)
    content: dict[str, Any] = field(default_factory=dict)
    content_digest: str = ""
    goal_id: uuid.UUID | None = None
    correlation_id: str = ""
    hmac_signature: str = ""
    signing_key_id: str = ""
    v1_envelope: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.content_digest and self.content:
            object.__setattr__(
                self, "content_digest",
                hashlib.sha256(
                    str(sorted(self.content.items())).encode()
                ).hexdigest()[:32],
            )

    def verify_trust(self, *, hmac_secret: bytes | None = None) -> bool:
        """Verify content integrity via digest and optional HMAC."""
        if self.hmac_signature or self.v1_envelope is not None:
            if hmac_secret is None:
                return False
            try:
                self.verify_hmac(hmac_secret)
            except Exception:
                return False
            return True
        if not self.content:
            return True
        expected = hashlib.sha256(
            str(sorted(self.content.items())).encode()
        ).hexdigest()[:32]
        return self.content_digest == expected

    def verify_hmac(self, secret: bytes) -> None:
        """Verify Spec §17 envelope_v1 HMAC; raises DomainError on failure."""
        if self.v1_envelope is None:
            raise ValueError("envelope has no v1 HMAC payload")
        verify_envelope(self.v1_envelope, secret=secret)

    def derive_child_envelope(
        self,
        dest_agent: str,
        *,
        reduced_scope: frozenset[str] | None = None,
        additional_permits: list[str] | None = None,
        delegated_permits: list[str] | None = None,
        content: dict[str, Any] | None = None,
        hmac_secret: bytes | None = None,
    ) -> AgentEnvelope:
        """Create a child envelope with reduced capability scope.

        Child scope ⊆ parent scope (only-decrease principle).
        """
        parent_scope = self.capability_scope
        child_scope = reduced_scope if reduced_scope is not None else parent_scope

        # Enforce: child scope ⊆ parent scope
        if not child_scope.issubset(parent_scope):
            raise ValueError(
                f"child scope {child_scope} is not a subset of "
                f"parent scope {parent_scope}"
            )

        # Permit references are authorization, not ordinary context. Never
        # copy a parent's bearer reference into a child envelope.
        if additional_permits:
            raise ValueError(
                "raw additional_permits are forbidden; use purpose-bound "
                "delegated_permits"
            )
        permits = list(delegated_permits or [])

        return create_envelope(
            self.dest_agent,
            dest_agent,
            capabilities=sorted(child_scope),
            permits=permits,
            content=content or {},
            goal_id=self.goal_id,
            correlation_id=self.correlation_id or str(self.envelope_id),
            hmac_secret=hmac_secret,
            causation_envelope_id=self.envelope_id,
        )


def _resolve_hmac_secret(explicit: bytes | None) -> bytes | None:
    if explicit is not None:
        return explicit
    try:
        from regent.config import get_settings

        key = get_settings().aar1_envelope_hmac_key
        if key is None:
            return None
        value = key.get_secret_value()
        return value.encode() if value else None
    except Exception:
        return None


def create_envelope(
    source: str,
    dest: str,
    *,
    capabilities: list[str] | None = None,
    permits: list[str] | None = None,
    content: dict[str, Any] | None = None,
    goal_id: uuid.UUID | None = None,
    correlation_id: str = "",
    hmac_secret: bytes | None = None,
    causation_envelope_id: uuid.UUID | None = None,
    signing_key_id: str = "aar1-default",
) -> AgentEnvelope:
    """Factory function to create an AgentEnvelope.

    When an HMAC secret is provided (or ``REGENT_AAR1_ENVELOPE_HMAC_KEY`` is set),
    the envelope is signed via ``envelope_v1.sign_envelope`` (RFC8785 + HMAC-SHA256).
    """
    envelope_id = uuid.uuid4()
    body = content or {}
    caps = list(capabilities or [])
    permit_list = list(permits or [])
    corr = correlation_id or str(envelope_id)
    secret = _resolve_hmac_secret(hmac_secret)

    v1: dict[str, Any] | None = None
    signature = ""
    key_id = ""
    if secret is not None:
        fields = build_unsigned_fields(
            goal_id=goal_id or uuid.UUID(int=0),
            organization_version_id=uuid.UUID(int=0),
            source_deployment_id=uuid.UUID(int=0),
            target_deployment_id=uuid.UUID(int=0),
            capability_scope=sorted(caps),
            permit_refs=permit_list,
            payload=body,
            idempotency_key=str(envelope_id),
            correlation_id=corr,
            causation_id=str(causation_envelope_id) if causation_envelope_id else None,
        )
        signed = sign_envelope(fields, secret=secret, signing_key_id=signing_key_id)
        v1 = signed.to_dict()
        signature = signed.signature
        key_id = signed.signing_key_id
        corr = signed.correlation_id

    return AgentEnvelope(
        envelope_id=envelope_id,
        source_agent=source,
        dest_agent=dest,
        capability_scope=frozenset(caps),
        permit_refs=permit_list,
        content=body,
        goal_id=goal_id,
        correlation_id=corr,
        hmac_signature=signature,
        signing_key_id=key_id,
        v1_envelope=v1,
    )
