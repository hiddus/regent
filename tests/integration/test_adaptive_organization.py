"""P3-A: Adaptive organization end-to-end integration tests.

Verifies:
- AgentEnvelope encapsulation and trust verification
- Capability scope propagation (child ⊆ parent)
- route_with_envelope on AgentMesh
- propose_adaptive_organization utility-driven proposal
- handle_adaptive_organization orchestrator handler
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from regent.application.agent_envelope import AgentEnvelope, create_envelope
from regent.application.agent_mesh import AgentMesh, A2ATaskStatus


# ---------------------------------------------------------------------------
# AgentEnvelope
# ---------------------------------------------------------------------------


class TestAgentEnvelopeIntegration:
    """End-to-end AgentEnvelope + AgentMesh integration."""

    def test_create_and_verify_envelope(self) -> None:
        """Envelope creation with content trust digest."""
        env = create_envelope(
            source="orchestrator",
            dest="worker-1",
            capabilities=["deploy", "test"],
            permits=["permit-abc"],
            content={"task": "build"},
            goal_id=uuid.uuid4(),
        )
        assert env.verify_trust()
        assert env.capability_scope == frozenset({"deploy", "test"})
        assert env.source_agent == "orchestrator"
        assert env.dest_agent == "worker-1"

    def test_child_envelope_scope_reduced(self) -> None:
        """Child envelope has reduced scope (child ⊆ parent)."""
        parent = create_envelope(
            source="orchestrator",
            dest="team-lead",
            capabilities=["deploy", "test", "review"],
        )
        child = parent.derive_child_envelope(
            "worker-1",
            reduced_scope=frozenset({"deploy"}),
        )
        assert child.capability_scope == frozenset({"deploy"})
        assert child.capability_scope.issubset(parent.capability_scope)
        assert child.source_agent == "team-lead"
        assert child.dest_agent == "worker-1"

    def test_child_envelope_rejects_superset_scope(self) -> None:
        """Child envelope cannot exceed parent scope."""
        parent = create_envelope(
            source="orchestrator",
            dest="team-lead",
            capabilities=["deploy"],
        )
        with pytest.raises(ValueError, match="not a subset"):
            parent.derive_child_envelope(
                "worker-1",
                reduced_scope=frozenset({"deploy", "admin"}),
            )

    def test_tampered_content_fails_trust(self) -> None:
        """Tampered content fails digest verification."""
        env = create_envelope(
            source="a", dest="b",
            content={"key": "value"},
        )
        assert env.verify_trust()
        # Tamper: create a new envelope with same digest but different content
        tampered = AgentEnvelope(
            envelope_id=env.envelope_id,
            source_agent="a",
            dest_agent="b",
            capability_scope=env.capability_scope,
            content={"key": "tampered"},
            content_digest=env.content_digest,  # old digest
        )
        assert not tampered.verify_trust()


# ---------------------------------------------------------------------------
# AgentMesh.route_with_envelope
# ---------------------------------------------------------------------------


class TestRouteWithEnvelope:
    """AgentMesh routing with AgentEnvelope."""

    def test_route_with_valid_envelope(self) -> None:
        """Valid envelope routes to A2A delegation."""
        mesh = AgentMesh(use_memory=True)
        env = create_envelope(
            source="orchestrator",
            dest="worker-1",
            capabilities=["deploy"],
            content={"task": "build_app"},
        )
        task = mesh.route_with_envelope(env, description="build the app")
        assert task.status == A2ATaskStatus.PENDING
        assert task.from_agent == "orchestrator"
        assert task.to_agent == "worker-1"
        assert "deploy" in str(task.metadata.get("capability_scope", ""))

    def test_route_with_tampered_envelope_rejected(self) -> None:
        """Tampered envelope is rejected by trust verification."""
        mesh = AgentMesh(use_memory=True)
        env = AgentEnvelope(
            envelope_id=uuid.uuid4(),
            source_agent="attacker",
            dest_agent="worker-1",
            capability_scope=frozenset({"deploy"}),
            content={"task": "malicious"},
            content_digest="invalid-digest",
        )
        task = mesh.route_with_envelope(env)
        assert task.status == A2ATaskStatus.FAILED
        assert "trust" in task.task_description.lower()

    def test_route_rejects_non_envelope(self) -> None:
        """route_with_envelope rejects non-AgentEnvelope objects."""
        mesh = AgentMesh(use_memory=True)
        with pytest.raises(TypeError, match="AgentEnvelope"):
            mesh.route_with_envelope({"not": "an envelope"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# propose_adaptive_organization
# ---------------------------------------------------------------------------


class TestProposeAdaptiveOrganization:
    """OrganizationService.propose_adaptive_organization integration."""

    @pytest.mark.asyncio
    async def test_propose_returns_utility_score(self) -> None:
        """Proposal includes utility score and rationale."""
        from regent.application.organization_service import OrganizationService

        sessions = MagicMock()
        service = OrganizationService(sessions)

        proposal = await service.propose_adaptive_organization(
            uuid.uuid4(),
            actor="test-agent",
        )
        assert "proposed_template" in proposal
        assert "utility" in proposal
        assert "utility_components" in proposal
        assert "proposed_roles" in proposal
        assert proposal["proposed_by"] == "test-agent"
        assert isinstance(proposal["utility"], float)
        assert 0 <= proposal["utility"] <= 1
