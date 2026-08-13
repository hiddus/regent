"""P3-A: AgentEnvelope permission propagation tests."""

from __future__ import annotations

import uuid

import pytest

from regent.application.agent_envelope import AgentEnvelope, create_envelope
from regent.application.agent_mesh import AgentMesh, A2ATaskStatus


class TestAgentEnvelope:
    """P3-A: AgentEnvelope encapsulation and trust verification."""

    def test_create_envelope(self) -> None:
        """Create an envelope with capabilities and permits."""
        env = create_envelope(
            "agent-a", "agent-b",
            capabilities=["read", "write"],
            permits=["permit-1"],
            content={"task": "do something"},
        )
        assert env.source_agent == "agent-a"
        assert env.dest_agent == "agent-b"
        assert "read" in env.capability_scope
        assert "write" in env.capability_scope
        assert env.permit_refs == ["permit-1"]
        assert env.content_digest  # should be auto-computed

    def test_verify_trust_valid(self) -> None:
        """Content trust verification passes for unmodified content."""
        env = create_envelope(
            "a", "b",
            content={"key": "value"},
        )
        assert env.verify_trust() is True

    def test_verify_trust_empty_content(self) -> None:
        """Empty content passes trust verification."""
        env = create_envelope("a", "b")
        assert env.verify_trust() is True

    def test_derive_child_envelope_reduces_scope(self) -> None:
        """Child envelope has reduced scope (child ⊆ parent)."""
        parent = create_envelope(
            "coordinator", "worker-1",
            capabilities=["read", "write", "deploy"],
        )
        child = parent.derive_child_envelope(
            "worker-2",
            reduced_scope=frozenset(["read"]),
            content={"subtask": "analyze"},
        )
        assert child.capability_scope == frozenset(["read"])
        assert child.capability_scope.issubset(parent.capability_scope)
        assert child.source_agent == "worker-1"
        assert child.dest_agent == "worker-2"

    def test_derive_child_envelope_rejects_expanded_scope(self) -> None:
        """Child envelope cannot expand scope beyond parent."""
        parent = create_envelope(
            "coordinator", "worker-1",
            capabilities=["read"],
        )
        with pytest.raises(ValueError, match="not a subset"):
            parent.derive_child_envelope(
                "worker-2",
                reduced_scope=frozenset(["read", "write"]),
            )

    def test_derive_child_uses_only_delegated_permits(self) -> None:
        """Child receives a fresh delegated permit, never the parent permit."""
        parent = create_envelope(
            "coordinator", "worker-1",
            capabilities=["read", "write"],
            permits=["permit-parent"],
        )
        child = parent.derive_child_envelope(
            "worker-2",
            delegated_permits=["delegated-permit-child"],
        )
        assert child.permit_refs == ["delegated-permit-child"]

    def test_derive_child_rejects_raw_permit_addition(self) -> None:
        parent = create_envelope(
            "coordinator", "worker-1", permits=["permit-parent"]
        )
        with pytest.raises(ValueError, match="delegated_permits"):
            parent.derive_child_envelope(
                "worker-2", additional_permits=["permit-parent"]
            )

    def test_derive_child_inherits_goal_id(self) -> None:
        """Child envelope inherits goal_id from parent."""
        goal_id = uuid.uuid4()
        parent = create_envelope(
            "coordinator", "worker-1",
            capabilities=["read"],
            goal_id=goal_id,
        )
        child = parent.derive_child_envelope("worker-2")
        assert child.goal_id == goal_id


class TestAgentMeshRouteWithEnvelope:
    """P3-A: AgentMesh.route_with_envelope() integration."""

    def test_route_with_envelope_creates_task(self) -> None:
        """Routing an envelope creates an A2A task with metadata."""
        mesh = AgentMesh(use_memory=True)
        env = create_envelope(
            "agent-a", "agent-b",
            capabilities=["read", "write"],
            permits=["permit-1"],
            content={"task": "deploy"},
        )
        task = mesh.route_with_envelope(env, description="deploy task")
        assert task.from_agent == "agent-a"
        assert task.to_agent == "agent-b"
        assert task.status == A2ATaskStatus.PENDING
        assert task.metadata["envelope_id"] == str(env.envelope_id)
        assert "read" in task.metadata["capability_scope"]

    def test_route_rejects_non_envelope(self) -> None:
        """route_with_envelope rejects non-AgentEnvelope objects."""
        mesh = AgentMesh(use_memory=True)
        with pytest.raises(TypeError):
            mesh.route_with_envelope("not-an-envelope")

    def test_route_propagates_capability_scope(self) -> None:
        """Capability scope is propagated to task metadata."""
        mesh = AgentMesh(use_memory=True)
        env = create_envelope(
            "pm", "dev",
            capabilities=["code-review", "test"],
        )
        task = mesh.route_with_envelope(env)
        assert sorted(task.metadata["capability_scope"]) == ["code-review", "test"]

    def test_hmac_signed_envelope_routes(self) -> None:
        secret = b"test-hmac-secret-key"
        mesh = AgentMesh(use_memory=True)
        env = create_envelope(
            "a", "b",
            capabilities=["read"],
            content={"task": "x"},
            correlation_id="corr-1",
            hmac_secret=secret,
        )
        assert env.hmac_signature
        assert env.correlation_id == "corr-1"
        assert env.v1_envelope is not None
        task = mesh.route_with_envelope(env, hmac_secret=secret)
        assert task.status == A2ATaskStatus.PENDING
        assert task.metadata["hmac_signature"] == env.hmac_signature

    def test_hmac_required_when_secret_configured(self) -> None:
        secret = b"test-hmac-secret-key"
        mesh = AgentMesh(use_memory=True)
        # Digest-only envelope rejected when HMAC secret is provided to router
        env = create_envelope("a", "b", content={"task": "x"}, hmac_secret=None)
        # Force no auto-sign by passing explicit None and ensuring no settings key:
        assert not env.hmac_signature
        task = mesh.route_with_envelope(env, hmac_secret=secret)
        assert task.status == A2ATaskStatus.FAILED
        assert "HMAC" in task.task_description

    def test_hmac_tamper_rejected(self) -> None:
        secret = b"test-hmac-secret-key"
        mesh = AgentMesh(use_memory=True)
        env = create_envelope(
            "a", "b",
            content={"task": "x"},
            hmac_secret=secret,
        )
        # Tamper v1 envelope signature
        tampered = dict(env.v1_envelope or {})
        tampered["signature"] = "0" * 64
        object.__setattr__(env, "v1_envelope", tampered)
        object.__setattr__(env, "hmac_signature", tampered["signature"])
        task = mesh.route_with_envelope(env, hmac_secret=secret)
        assert task.status == A2ATaskStatus.FAILED
