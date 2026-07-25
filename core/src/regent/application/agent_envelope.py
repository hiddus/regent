"""P3-A: AgentEnvelope — inter-agent message encapsulation.

Ensures:
- source/dest agent identification
- capability_scope propagation (child ⊆ parent)
- permit_refs for authorization delegation
- content_trust for message integrity
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentEnvelope:
    """Encapsulated message between agents with authorization context.

    The envelope ensures that:
    - Messages are addressed to specific agents (source/dest)
    - Capability scope only decreases (child ⊆ parent ∩ GoalSpec)
    - Permit references track authorization chain
    - Content trust is verified via digest
    """

    envelope_id: uuid.UUID
    source_agent: str
    dest_agent: str
    capability_scope: frozenset[str]
    permit_refs: list[str] = field(default_factory=list)
    content: dict[str, Any] = field(default_factory=dict)
    content_digest: str = ""
    goal_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if not self.content_digest and self.content:
            object.__setattr__(
                self, "content_digest",
                hashlib.sha256(
                    str(sorted(self.content.items())).encode()
                ).hexdigest()[:32],
            )

    def verify_trust(self) -> bool:
        """Verify content integrity via digest."""
        if not self.content:
            return True
        expected = hashlib.sha256(
            str(sorted(self.content.items())).encode()
        ).hexdigest()[:32]
        return self.content_digest == expected

    def derive_child_envelope(
        self,
        dest_agent: str,
        *,
        reduced_scope: frozenset[str] | None = None,
        additional_permits: list[str] | None = None,
        content: dict[str, Any] | None = None,
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

        permits = list(self.permit_refs)
        if additional_permits:
            permits.extend(additional_permits)

        return AgentEnvelope(
            envelope_id=uuid.uuid4(),
            source_agent=self.dest_agent,
            dest_agent=dest_agent,
            capability_scope=child_scope,
            permit_refs=permits,
            content=content or {},
            goal_id=self.goal_id,
        )


def create_envelope(
    source: str,
    dest: str,
    *,
    capabilities: list[str] | None = None,
    permits: list[str] | None = None,
    content: dict[str, Any] | None = None,
    goal_id: uuid.UUID | None = None,
) -> AgentEnvelope:
    """Factory function to create an AgentEnvelope."""
    return AgentEnvelope(
        envelope_id=uuid.uuid4(),
        source_agent=source,
        dest_agent=dest,
        capability_scope=frozenset(capabilities or []),
        permit_refs=list(permits or []),
        content=content or {},
        goal_id=goal_id,
    )
