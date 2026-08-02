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


def test_classify_bare_http_not_evidence() -> None:
    """CD-7.1: bare 'http' / 'observed' substrings must not force evidence routing."""
    assert (
        classify_delivery_gap_kind(["stylesheet missing; see https://example.com"])
        != "evidence"
    )
    assert classify_delivery_gap_kind(["http fetch failed in build log"]) != "evidence"


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
        patch.object(DeliveryGapRecoveryService, "_admit_failure_memories", AsyncMock()),
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
async def test_recover_escalates_to_configure_on_second_attempt() -> None:
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
        patch.object(DeliveryGapRecoveryService, "_admit_failure_memories", AsyncMock()),
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
    assert result.method == "CONFIGURE"
    assert goal.metadata_json["capability_resolution"]["escalation_step"] == EscalationStep.CONFIGURE
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_recover_escalates_to_compose_on_third_attempt() -> None:
    goal_id = uuid.uuid4()
    composed_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="app",
        status="ACTIVE",
        version=1,
        created_by="test",
        correlation_id=uuid.uuid4(),
        metadata_json={"delivery_gap_recovery_attempts": 2},
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
        patch.object(DeliveryGapRecoveryService, "_admit_failure_memories", AsyncMock()),
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
    assert result.attempts == 3


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
        metadata_json={
            "delivery_gap_recovery_attempts": 10,
            # Auto-continue budget already spent → soft-pause (no TaskCard).
            "delivery_gap_auto_continue_cycles": 2,
        },
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
        patch.object(DeliveryGapRecoveryService, "_admit_failure_memories", AsyncMock()),
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
    assert result.method == "SOFT_PAUSE"
    assert "对话" in result.message or "补充方向" in result.message
    assert goal.metadata_json.get("execution_stage") == "DELIVERY_SOFT_PAUSE"
    assert goal.metadata_json.get("awaiting_human_intervention") is False
    assert goal.metadata_json.get("termination", {}).get("handoff") == "SOFT_PAUSE"
    assert not goal.metadata_json.get("pending_delivery_gap_human")


@pytest.mark.asyncio
async def test_resume_after_human_resets_attempts_and_recovers() -> None:
    goal_id = uuid.uuid4()
    project_id = uuid.uuid4()
    req_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="app",
        status="ACTIVE",
        version=2,
        created_by="test",
        correlation_id=uuid.uuid4(),
        app_project_id=project_id,
        metadata_json={
            "delivery_gap_recovery_attempts": 10,
            "awaiting_human_intervention": True,
            "delivery_gap_kind": "presentation",
            "requirement_revision_id": str(req_id),
            "capability_resolution_plan_id": str(plan_id),
            "termination": {
                "ladder_exhausted": True,
                "gap_reasons": ["stylesheet-present: missing"],
                "handoff": "WAITING_HUMAN",
            },
        },
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
        patch.object(DeliveryGapRecoveryService, "_append", AsyncMock()),
        patch.object(DeliveryGapRecoveryService, "_admit_failure_memories", AsyncMock()),
    ):
        svc = DeliveryGapRecoveryService(factory)
        svc._orgs = MagicMock(reorganize_for_gap=AsyncMock(return_value=reorg))
        result = await svc.resume_after_human(
            goal_id=goal_id,
            project_id=project_id,
            actor="user",
            human_message="批准",
        )

    assert result.recovered is True
    assert result.attempts == 1
    assert goal.metadata_json.get("awaiting_human_intervention") is False
    assert "termination" not in (goal.metadata_json or {})
    assert goal.metadata_json.get("delivery_gap_recovery_attempts") == 1
    assert "human-authorized-continue" in str(
        goal.metadata_json.get("delivery_gap_reasons")
        or goal.metadata_json.get("capability_resolution")
        or ""
    ) or result.method in {"REUSE", "CONFIGURE", "COMPOSE", "BUILD", "ACQUIRE"}


@pytest.mark.asyncio
async def test_resume_after_goal_intent_does_not_rehandoff() -> None:
    """Approve must enter the ladder — not immediately short-circuit to another card."""
    goal_id = uuid.uuid4()
    project_id = uuid.uuid4()
    req_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="app",
        status="ACTIVE",
        version=2,
        created_by="test",
        correlation_id=uuid.uuid4(),
        app_project_id=project_id,
        metadata_json={
            "delivery_gap_recovery_attempts": 0,
            "awaiting_human_intervention": True,
            "delivery_gap_kind": "goal_intent",
            "requirement_revision_id": str(req_id),
            "capability_resolution_plan_id": str(plan_id),
            "pending_delivery_gap_human": {
                "human_task_id": str(uuid.uuid4()),
                "gap_kind": "goal_intent",
                "gap_reasons": ["goal-first-deliverable: missing tokens"],
            },
            "termination": {
                "gap_reasons": ["goal-first-deliverable: missing tokens"],
                "handoff": "WAITING_HUMAN",
            },
        },
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
        patch.object(DeliveryGapRecoveryService, "_append", AsyncMock()),
        patch.object(DeliveryGapRecoveryService, "_admit_failure_memories", AsyncMock()),
    ):
        svc = DeliveryGapRecoveryService(factory)
        svc._orgs = MagicMock(reorganize_for_gap=AsyncMock(return_value=reorg))
        result = await svc.resume_after_human(
            goal_id=goal_id,
            project_id=project_id,
            actor="user",
            human_message="批准",
        )

    assert result.recovered is True
    assert result.terminal_exhaust is False
    assert result.method in {"REUSE", "CONFIGURE", "COMPOSE", "BUILD", "ACQUIRE"}
    assert goal.metadata_json.get("awaiting_human_intervention") is False


@pytest.mark.asyncio
async def test_resume_missing_lineage_clears_always_allow_and_restarts_discovery() -> None:
    """Approve without requirement/plan must not stack intervene + always-allow loops."""
    goal_id = uuid.uuid4()
    project_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="app",
        status="ACTIVE",
        version=2,
        created_by="test",
        correlation_id=uuid.uuid4(),
        app_project_id=project_id,
        metadata_json={
            "decision_allow_actions": ["delivery_gap_intervene", "quality_approval"],
            "awaiting_human_intervention": True,
            "delivery_gap_kind": "product_surface",
        },
    )
    factory = _goal_session(goal, None)

    with patch.object(DeliveryGapRecoveryService, "_append", AsyncMock()):
        svc = DeliveryGapRecoveryService(factory)
        with patch.object(
            DeliveryGapRecoveryService,
            "_resolve_generation_ids",
            AsyncMock(return_value=(None, None)),
        ):
            result = await svc.resume_after_human(
                goal_id=goal_id,
                project_id=project_id,
                actor="user",
                human_message="总是允许",
            )

    assert result.recovered is False
    assert result.method == "RESTART_DISCOVERY"
    assert "missing generation lineage" in result.message
    assert goal.metadata_json.get("decision_allow_actions") == ["quality_approval"]
    assert goal.metadata_json.get("execution_stage") == "DISCOVERING"
    assert goal.metadata_json.get("awaiting_human_intervention") is False


@pytest.mark.asyncio
async def test_goal_intent_runs_ladder_without_human_ask() -> None:
    """goal_intent is normal repair work — auto ladder, no authorization card."""
    goal_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="app",
        status="ACTIVE",
        version=1,
        created_by="test",
        correlation_id=uuid.uuid4(),
        metadata_json={"delivery_gap_recovery_attempts": 0},
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
        patch.object(DeliveryGapRecoveryService, "_append", AsyncMock()),
        patch.object(DeliveryGapRecoveryService, "_admit_failure_memories", AsyncMock()),
    ):
        svc = DeliveryGapRecoveryService(factory)
        svc._orgs = MagicMock(reorganize_for_gap=AsyncMock(return_value=reorg))
        result = await svc.recover(
            goal_id=goal_id,
            project_id=uuid.uuid4(),
            requirement_revision_id=uuid.uuid4(),
            capability_resolution_plan_id=uuid.uuid4(),
            actor="test",
            gap_reasons=["goal-first-deliverable: missing tokens"],
        )

    assert result.recovered is True
    assert result.terminal_exhaust is False
    assert result.method in {"REUSE", "CONFIGURE", "COMPOSE", "BUILD", "ACQUIRE"}
    assert result.gap_kind == "goal_intent"

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
        patch.object(DeliveryGapRecoveryService, "_admit_failure_memories", AsyncMock()),
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


def test_build_failure_lesson_and_constraints_absorb_deploy_gap() -> None:
    from regent.application.delivery_gap_recovery import (
        build_failure_lesson,
        build_learned_constraints,
    )

    reasons = ["deployment-failed: RuntimeError", "stylesheet-present: missing"]
    constraints = build_learned_constraints("presentation", reasons)
    assert any("CSS" in c or "stylesheet" in c.lower() or "Substantial" in c for c in constraints)
    assert any("deploy" in c.lower() for c in constraints)

    lesson = build_failure_lesson(
        gap_reasons=reasons,
        gap_kind="presentation",
        method="REUSE",
        attempt=1,
        halt_context={"stage": "DEPLOY_FAILED", "last_error": "boom"},
        goal_text="build a news digest",
    )
    assert lesson["replan_required"] is True
    assert lesson["lesson_digest"]
    assert lesson["last_error"] == "boom"
    assert "stylesheet-present: missing" in lesson["gap_reasons"]
    assert lesson.get("summary")
    assert "stylesheet-present" in lesson["summary"] or "deployment-failed" in lesson["summary"]
    assert lesson.get("avoid")
    assert lesson.get("code", "").startswith("DELIVERY_GAP_")


@pytest.mark.asyncio
async def test_recover_writes_failure_lessons_and_replan_nonce() -> None:
    goal_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="news digest site",
        status="ACTIVE",
        version=1,
        created_by="test",
        correlation_id=uuid.uuid4(),
        metadata_json={
            "halt": {"stage": "DEPLOY_FAILED", "message": "preview down", "error": "boom"}
        },
    )
    factory = _goal_session(goal, None)
    reorg = _fake_reorg(goal_id)
    admit = AsyncMock()

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
        patch.object(DeliveryGapRecoveryService, "_admit_failure_memories", admit),
    ):
        svc = DeliveryGapRecoveryService(factory)
        svc._orgs = MagicMock(reorganize_for_gap=AsyncMock(return_value=reorg))
        result = await svc.recover(
            goal_id=goal_id,
            project_id=uuid.uuid4(),
            requirement_revision_id=uuid.uuid4(),
            capability_resolution_plan_id=uuid.uuid4(),
            actor="test",
            gap_reasons=["deployment-failed: RuntimeError"],
            halt_context={"stage": "DEPLOY_FAILED", "last_error": "RuntimeError"},
        )

    assert result.recovered is True
    meta = goal.metadata_json
    assert meta["failure_lessons"]
    assert meta["learned_constraints"]
    assert meta["replan_nonce"]
    assert meta["capability_resolution"]["failure_lesson_digest"]
    assert meta["replan_nonce"].startswith("1:")
    assert "absorb" in " ".join(meta["learned_constraints"]).lower() or any(
        "deploy" in c.lower() for c in meta["learned_constraints"]
    )
    # Outbox payload must carry replan markers so next GenerationRunRequested differs.
    added = factory.return_value.__aenter__.return_value.add.call_args_list
    assert added, "expected outbox event to be added"
    outbox_event = added[0].args[0]
    payload = outbox_event.payload
    assert payload["replan_nonce"] == meta["replan_nonce"]
    assert payload["failure_lesson_digest"] == meta["capability_resolution"][
        "failure_lesson_digest"
    ]
    admit.assert_awaited_once()
    assert "已吸收失败经验并重规划" in result.message


@pytest.mark.asyncio
async def test_recover_replan_nonce_changes_across_attempts() -> None:
    goal_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="app",
        status="ACTIVE",
        version=1,
        created_by="test",
        correlation_id=uuid.uuid4(),
        metadata_json={},
    )
    reorg = _fake_reorg(goal_id)
    nonces: list[str] = []
    digests: list[str] = []

    async def _run_once(prior_attempts: int) -> None:
        goal.metadata_json = {
            **dict(goal.metadata_json or {}),
            "delivery_gap_recovery_attempts": prior_attempts,
        }
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
            patch(
                "regent.application.delivery_gap_recovery.build_attainment_capability",
                AsyncMock(return_value=uuid.uuid4()),
            ),
            patch.object(DeliveryGapRecoveryService, "_append", AsyncMock()),
            patch.object(DeliveryGapRecoveryService, "_admit_failure_memories", AsyncMock()),
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
        nonces.append(goal.metadata_json["replan_nonce"])
        digests.append(goal.metadata_json["capability_resolution"]["failure_lesson_digest"])

    await _run_once(0)
    await _run_once(1)

    assert nonces[0] != nonces[1]
    assert digests[0] != digests[1]
    assert nonces[0].startswith("1:")
    assert nonces[1].startswith("2:")
    assert len(goal.metadata_json["failure_lessons"]) >= 1


def test_acceptance_contract_replan_fields_change_plan_digest() -> None:
    """Observable replan: failure lesson fields must change GenerationPlanContract digest."""
    import uuid as uuid_mod

    from regent.application.p1_contracts import GenerationPlanContract, canonical_hash

    base = {
        "goal_spec_hash": "a" * 64,
        "hypothesis_decision_id": uuid_mod.uuid4(),
        "requirement_revision_hash": "b" * 64,
        "capability_resolution_hash": "c" * 64,
        "runtime_profile_hash": "d" * 64,
        "evidence_bundle_digest": "e" * 64,
        "generator_ref": "agentic-generation-v1",
        "model_ref": "p1-model",
        "prompt_version": "agentic-generation-v1",
        "planned_paths": ["src/index.html"],
        "verification_commands": ["python -m compileall src"],
        "acceptance_contract": {
            "delivery_policy": "goal_attainment_escalation",
            "delivery_gap_reasons": ["stylesheet-present: missing"],
            "delivery_gap_recovery_attempt": 1,
        },
    }
    first = GenerationPlanContract(**base)
    second_contract = {
        **base["acceptance_contract"],
        "delivery_gap_recovery_attempt": 2,
        "replan_nonce": "2:presentation:CONFIGURE:abc123",
        "failure_lesson_digest": "abc123",
        "learned_constraints": ["Must ship substantial CSS"],
        "failure_lessons": [{"lesson_digest": "abc123", "attempt": 2}],
    }
    second = GenerationPlanContract(
        **{**base, "acceptance_contract": second_contract}
    )
    assert canonical_hash(first) != canonical_hash(second)
