"""Unit tests for GAC-B1/D delivery gap classification and recovery routing."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from regent.application.capability_ladder import EscalationStep
from regent.application.delivery_gap_recovery import (
    DeliveryGapRecoveryService,
    classify_delivery_gap_kind,
    guidance_for_gap_kind,
)
from regent.application.organization_service import OrganizationReceipt, ReorganizationResult
from regent.infrastructure.evidence_capability import CAPABILITY_NAME as HTTP_SOURCE_NAME
from regent.infrastructure.models import GoalModel, GoalSpecModel
from regent.infrastructure.product_surface_capability import (
    CAPABILITY_NAME as PRODUCT_SURFACE_NAME,
)


def test_classify_presentation_before_evidence() -> None:
    assert (
        classify_delivery_gap_kind(
            ["stylesheet-present: missing", "goal-outbound-links: https links=0"]
        )
        == "presentation"
    )


def test_classify_evidence() -> None:
    assert classify_delivery_gap_kind(["goal-outbound-links: https links=0 < 3"]) == "evidence"


def test_classify_goal_intent() -> None:
    assert (
        classify_delivery_gap_kind(["goal-first-deliverable: missing tokens"]) == "goal_intent"
    )


def test_classify_default() -> None:
    assert classify_delivery_gap_kind([]) == "product_surface"


def test_guidance_differs_by_kind() -> None:
    present = guidance_for_gap_kind("presentation")
    evidence = guidance_for_gap_kind("evidence")
    assert present[0] != evidence[0]


def _goal_session(goal: GoalModel, spec: GoalSpecModel | None = None) -> MagicMock:
    session = AsyncMock()
    session.get = AsyncMock(return_value=goal)
    cap_row = MagicMock(status="VERIFIED")
    session.scalar = AsyncMock(side_effect=[spec, cap_row, cap_row, cap_row])
    session.add = MagicMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    tx = AsyncMock()
    tx.__aenter__.return_value = None
    tx.__aexit__.return_value = None
    session.begin = MagicMock(return_value=tx)
    return MagicMock(return_value=session_context)


def _fake_reorg(goal_id: uuid.UUID) -> ReorganizationResult:
    return ReorganizationResult(
        receipt=OrganizationReceipt(
            organization_id=uuid.uuid4(),
            goal_id=goal_id,
            strategy="MULTI_SPECIALIST",
            agent_spec_ids=[uuid.uuid4()],
            required_capabilities=["product-surface-v1"],
            reused_capabilities=["product-surface-v1"],
            capability_gaps=[],
            assignment_count=1,
            replayed=False,
        ),
        recovery_work_id=uuid.uuid4(),
        gap_kind="evidence",
        method="REUSE",
        attempt=1,
    )


@pytest.mark.asyncio
async def test_recover_routes_evidence_to_http_capability() -> None:
    goal_id = uuid.uuid4()
    surface_id = uuid.uuid4()
    review_id = uuid.uuid4()
    http_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="news digest",
        status="ACTIVE",
        version=1,
        created_by="test",
        correlation_id=uuid.uuid4(),
        metadata_json={"execution_stage": "GENERATING"},
    )
    spec = GoalSpecModel(
        id=uuid.uuid4(),
        goal_id=goal_id,
        version=1,
        status="FROZEN",
        content_hash="abc",
        explicit_constraints={},
        success_criteria={"usable": True},
    )
    factory = _goal_session(goal, spec)
    reorg = _fake_reorg(goal_id)

    with (
        patch(
            "regent.application.delivery_gap_recovery.ensure_product_surface_capability",
            AsyncMock(return_value=surface_id),
        ),
        patch(
            "regent.application.delivery_gap_recovery.ensure_delivery_review_capability",
            AsyncMock(return_value=review_id),
        ),
        patch(
            "regent.application.delivery_gap_recovery.ensure_allowlisted_http_capability",
            AsyncMock(return_value=http_id),
        ),
        patch.object(DeliveryGapRecoveryService, "_append", AsyncMock()),
    ):
        svc = DeliveryGapRecoveryService(factory)
        svc._orgs = MagicMock(reorganize_for_gap=AsyncMock(return_value=reorg))
        result = await svc.recover(
            goal_id=goal_id,
            project_id=uuid.uuid4(),
            requirement_revision_id=uuid.uuid4(),
            capability_resolution_plan_id=uuid.uuid4(),
            actor="test",
            gap_reasons=["goal-outbound-links: https links=0 < 3"],
        )

    assert result.recovered is True
    assert result.gap_kind == "evidence"
    assert result.method == "REUSE"
    assert goal.metadata_json["capability_resolution"]["primary_capability"] == HTTP_SOURCE_NAME
    assert goal.metadata_json["capability_resolution"]["escalation_step"] == "REUSE"
    assert goal.metadata_json["organization_id"] == str(reorg.receipt.organization_id)


@pytest.mark.asyncio
async def test_recover_escalates_to_compose_on_second_attempt() -> None:
    goal_id = uuid.uuid4()
    composed_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="app",
        status="ACTIVE",
        version=1,
        created_by="test",
        correlation_id=uuid.uuid4(),
        metadata_json={"delivery_gap_recovery_attempts": 1},
    )
    factory = _goal_session(goal, None)
    reorg = _fake_reorg(goal_id)

    with (
        patch(
            "regent.application.delivery_gap_recovery.ensure_product_surface_capability",
            AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "regent.application.delivery_gap_recovery.ensure_delivery_review_capability",
            AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "regent.application.delivery_gap_recovery.ensure_allowlisted_http_capability",
            AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "regent.application.delivery_gap_recovery.build_attainment_capability",
            AsyncMock(return_value=composed_id),
        ),
        patch.object(DeliveryGapRecoveryService, "_append", AsyncMock()),
    ):
        svc = DeliveryGapRecoveryService(factory)
        svc._orgs = MagicMock(reorganize_for_gap=AsyncMock(return_value=reorg))
        result = await svc.recover(
            goal_id=goal_id,
            project_id=uuid.uuid4(),
            requirement_revision_id=uuid.uuid4(),
            capability_resolution_plan_id=uuid.uuid4(),
            actor="test",
            gap_reasons=["stylesheet-present: missing"],
        )

    assert result.recovered is True
    assert result.method == "COMPOSE"
    assert goal.metadata_json["capability_resolution"]["escalation_step"] == EscalationStep.COMPOSE
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_recover_stops_after_ladder_exhausted() -> None:
    goal_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="app",
        status="ACTIVE",
        version=1,
        created_by="test",
        correlation_id=uuid.uuid4(),
        metadata_json={"delivery_gap_recovery_attempts": 4},
    )
    factory = _goal_session(goal, None)

    with (
        patch(
            "regent.application.delivery_gap_recovery.ensure_product_surface_capability",
            AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "regent.application.delivery_gap_recovery.ensure_delivery_review_capability",
            AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "regent.application.delivery_gap_recovery.ensure_allowlisted_http_capability",
            AsyncMock(return_value=uuid.uuid4()),
        ),
        patch.object(DeliveryGapRecoveryService, "_append", AsyncMock()),
    ):
        result = await DeliveryGapRecoveryService(factory).recover(
            goal_id=goal_id,
            project_id=uuid.uuid4(),
            requirement_revision_id=uuid.uuid4(),
            capability_resolution_plan_id=uuid.uuid4(),
            actor="test",
            gap_reasons=["stylesheet-present: missing"],
        )

    assert result.recovered is False
    assert result.terminal_exhaust is True
    assert result.method == "STOP"


@pytest.mark.asyncio
async def test_recover_routes_presentation_to_product_surface() -> None:
    goal_id = uuid.uuid4()
    surface_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="app",
        status="ACTIVE",
        version=1,
        created_by="test",
        correlation_id=uuid.uuid4(),
        metadata_json={},
    )
    factory = _goal_session(goal, None)
    reorg = _fake_reorg(goal_id)

    with (
        patch(
            "regent.application.delivery_gap_recovery.ensure_product_surface_capability",
            AsyncMock(return_value=surface_id),
        ),
        patch(
            "regent.application.delivery_gap_recovery.ensure_delivery_review_capability",
            AsyncMock(return_value=uuid.uuid4()),
        ),
        patch(
            "regent.application.delivery_gap_recovery.ensure_allowlisted_http_capability",
            AsyncMock(return_value=uuid.uuid4()),
        ),
        patch.object(DeliveryGapRecoveryService, "_append", AsyncMock()),
    ):
        svc = DeliveryGapRecoveryService(factory)
        svc._orgs = MagicMock(reorganize_for_gap=AsyncMock(return_value=reorg))
        result = await svc.recover(
            goal_id=goal_id,
            project_id=uuid.uuid4(),
            requirement_revision_id=uuid.uuid4(),
            capability_resolution_plan_id=uuid.uuid4(),
            actor="test",
            gap_reasons=["stylesheet-present: missing <style>"],
        )

    assert result.gap_kind == "presentation"
    assert (
        goal.metadata_json["capability_resolution"]["primary_capability"]
        == PRODUCT_SURFACE_NAME
    )
