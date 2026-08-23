"""Tests for agent_invocation_guard — hub-and-spoke enforcement."""

from __future__ import annotations

import uuid

from regent.application.agent_invocation_guard import (
    InvocationGuardError,
    check_cross_deployment_invocation,
    check_subagent_delegate_allowed,
)


class TestSubagentDelegateGuard:
    def test_main_agent_can_delegate(self) -> None:
        decision = check_subagent_delegate_allowed(current_depth=0)
        assert decision.allowed is True

    def test_subagent_cannot_redelegate(self) -> None:
        decision = check_subagent_delegate_allowed(current_depth=1)
        assert decision.allowed is False
        assert "depth 1" in decision.reason

    def test_deep_subagent_cannot_redelegate(self) -> None:
        decision = check_subagent_delegate_allowed(current_depth=2)
        assert decision.allowed is False

    def test_custom_max_depth(self) -> None:
        decision = check_subagent_delegate_allowed(current_depth=1, max_depth=2)
        assert decision.allowed is True

    def test_zero_depth_always_denied_when_max_zero(self) -> None:
        decision = check_subagent_delegate_allowed(current_depth=0, max_depth=0)
        assert decision.allowed is False


class TestCrossDeploymentGuard:
    def setup_method(self) -> None:
        self.a = uuid.uuid4()
        self.b = uuid.uuid4()
        self.c = uuid.uuid4()

    def test_self_invocation_denied(self) -> None:
        decision = check_cross_deployment_invocation(
            source_deployment_id=self.a,
            target_deployment_id=self.a,
            source_role=None,
            target_role=None,
        )
        assert decision.allowed is False
        assert "Self-invocation" in decision.reason

    def test_normal_invocation_allowed(self) -> None:
        decision = check_cross_deployment_invocation(
            source_deployment_id=self.a,
            target_deployment_id=self.b,
            source_role="orchestrator",
            target_role="tech",
        )
        assert decision.allowed is True

    def test_cycle_detected(self) -> None:
        decision = check_cross_deployment_invocation(
            source_deployment_id=self.b,
            target_deployment_id=self.a,
            source_role="tech",
            target_role="product",
            active_chain=[self.a, self.b],
        )
        assert decision.allowed is False
        assert "Circular" in decision.reason

    def test_delivery_role_peer_to_peer_denied(self) -> None:
        decision = check_cross_deployment_invocation(
            source_deployment_id=self.a,
            target_deployment_id=self.b,
            source_role="product",
            target_role="tech",
        )
        assert decision.allowed is False
        assert "product" in decision.reason
        assert "tech" in decision.reason

    def test_orchestrator_to_role_allowed(self) -> None:
        decision = check_cross_deployment_invocation(
            source_deployment_id=self.a,
            target_deployment_id=self.b,
            source_role=None,
            target_role="product",
        )
        assert decision.allowed is True

    def test_role_to_orchestrator_allowed(self) -> None:
        """Roles reporting back to orchestrator is fine."""
        decision = check_cross_deployment_invocation(
            source_deployment_id=self.a,
            target_deployment_id=self.b,
            source_role="tech",
            target_role=None,
        )
        assert decision.allowed is True

    def test_invocation_guard_error(self) -> None:
        err = InvocationGuardError("product", "tech", "peer-to-peer denied")
        assert "product" in str(err)
        assert err.source == "product"
        assert err.target == "tech"
