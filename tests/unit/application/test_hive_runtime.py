"""Certified hive runtime unit tests (fixed template only)."""

from __future__ import annotations

import uuid

from regent.application.aar1_contract import CERTIFIED_HIVE_TEMPLATE_ID
from regent.application.agent_lifecycle_service import AgentLifecycleService
from regent.application.hive_runtime import (
    HIVE_TASK_TYPES,
    HiveRoleBinding,
    agent_spec_ref_for_role,
    role_assignment_name,
)


def test_hive_task_types_cover_pm_dev_qa() -> None:
    assert set(HIVE_TASK_TYPES) == {"pm", "dev", "qa"}
    assert HIVE_TASK_TYPES["qa"] == "hive.qa.review"


def test_agent_spec_refs_differ_for_producer_reviewer() -> None:
    producer = agent_spec_ref_for_role("dev")
    reviewer = agent_spec_ref_for_role("qa")
    assert producer != reviewer
    assert CERTIFIED_HIVE_TEMPLATE_ID in producer
    assert role_assignment_name("pm") == "goal-hive-pm"


def test_producer_reviewer_separation_enforced() -> None:
    same = uuid.uuid4()
    try:
        AgentLifecycleService.assert_producer_reviewer_separation(same, same)
        raise AssertionError("expected DomainError")
    except Exception as exc:
        from regent.domain.errors import DomainError, ErrorCode

        assert isinstance(exc, DomainError)
        assert exc.code is ErrorCode.POLICY_DENIED


def test_hive_role_binding_shape() -> None:
    binding = HiveRoleBinding(
        role="dev",
        agent_spec_id=uuid.uuid4(),
        deployment_id=uuid.uuid4(),
        capabilities=["product-surface-v1"],
    )
    assert binding.role == "dev"
    assert binding.capabilities == ["product-surface-v1"]
