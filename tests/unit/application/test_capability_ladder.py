"""Unit tests for GAC-D capability escalation ladder and BUILD packages."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.application.capability_build_service import (
    build_attainment_capability,
    build_implementation_package,
)
from regent.application.capability_ladder import (
    ATTAINMENT_LADDER_CYCLES,
    MAX_ATTAINMENT_ESCALATION_ATTEMPTS,
    EscalationStep,
    plan_escalation,
)


def test_ladder_runs_two_cycles_before_stop() -> None:
    """Unmet goals enumerate a full REUSE→…→ACQUIRE cycle twice before human handoff."""
    assert ATTAINMENT_LADDER_CYCLES == 2
    assert MAX_ATTAINMENT_ESCALATION_ATTEMPTS == 10
    assert plan_escalation(0).step is EscalationStep.REUSE
    assert plan_escalation(1).step is EscalationStep.CONFIGURE
    assert plan_escalation(2).step is EscalationStep.COMPOSE
    assert plan_escalation(3).step is EscalationStep.BUILD
    assert plan_escalation(4).step is EscalationStep.ACQUIRE
    # Second cycle
    assert plan_escalation(5).step is EscalationStep.REUSE
    assert plan_escalation(6).step is EscalationStep.CONFIGURE
    assert plan_escalation(7).step is EscalationStep.COMPOSE
    assert plan_escalation(8).step is EscalationStep.BUILD
    assert plan_escalation(9).step is EscalationStep.ACQUIRE
    assert plan_escalation(10).exhausted is True
    assert plan_escalation(10).step is EscalationStep.STOP


def test_build_implementation_package_is_verifiable() -> None:
    pkg = build_implementation_package(
        gap_kind="presentation",
        requirement_key="delivery.build.presentation",
        guidance=("Add CSS", "Use main"),
        acceptance_checks=["stylesheet-present"],
        composable_from=("product-surface-v1",),
    )
    assert pkg["protocol"] == "gac-build-v1"
    assert pkg["implementation"]["kind"] == "generation_guidance_package"
    assert pkg["verified_checks"]["passed_tests"] == 1
    assert "Add CSS" in pkg["generation_guidance"]


@pytest.mark.asyncio
async def test_build_attainment_capability_writes_package() -> None:
    goal_id = uuid.uuid4()
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.flush = AsyncMock()

    cap_id = await build_attainment_capability(
        session,
        goal_id=goal_id,
        capability_name="goal-gap-evidence-v1",
        requirement_key="delivery.build.evidence",
        gap_kind="evidence",
        guidance=("Render outbound links",),
        acceptance_checks=["goal-outbound-links"],
        composable_from=("allowlisted-http-source-v1",),
    )
    assert isinstance(cap_id, uuid.UUID)
    session.add.assert_called_once()
    added = session.add.call_args[0][0]
    assert added.name == "goal-gap-evidence-v1"
    assert added.status == "GOAL_CERTIFIED"
    assert added.verification["implementation"]["kind"] == "generation_guidance_package"
