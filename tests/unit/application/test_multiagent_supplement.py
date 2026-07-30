"""Multi-agent supplementation contracts (MA-0…MA-6) unit tests."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from regent.application.a2a_projection import (
    assert_not_replacing_kernel,
    project_agent_card,
    project_envelope_to_a2a,
    project_run_state,
)
from regent.application.aar1_contract import CERTIFIED_HIVE_TEMPLATE_ID
from regent.application.context_artifact import (
    ContextArtifactService,
    build_structured_compact_summary,
    rehydrate_context,
    should_offload_tool_result,
)
from regent.application.dispatch_decision import DispatchDecisionInput, DispatchDecisionService
from regent.application.execution_plan import ExecutionPlanService, UpsertPlanItem
from regent.application.mast_failure import MAST_CODES, classify_mast_failure, is_mast_code
from regent.application.member_contract import (
    compute_template_certification,
    certified_hive_member_contracts,
    enrich_topology_with_member_contracts,
    run_template_regression_suite,
    validate_certification_inheritance,
)
from regent.application.multiagent_metrics import (
    FaultInjectionTrace,
    TokenBucket,
    accumulate_token_bucket,
    compute_all_metrics,
    compute_coordination_token_share,
    compute_dispatch_entropy,
    compute_error_amplification_factor,
)
from regent.application.p24_frozen_experiment import (
    FrozenExperimentConfig,
    P24FrozenExperiment,
    VariantRunResult,
)
from regent.application.p25_adaptive_gate import (
    ROLLOUT_NOT_ALLOWED,
    enrich_adaptive_proposal_skeleton,
    evaluate_adaptive_rollout_gate,
)
from regent.application.task_features import TaskFeatures, prune_organization_space
from regent.domain.states import GoalState
from regent.infrastructure.models import GoalModel


def test_metrics_insufficient_without_fields() -> None:
    result = compute_coordination_token_share(TokenBucket())
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.value is None

    amp = compute_error_amplification_factor(
        FaultInjectionTrace(
            injection_task_version="fi-v1",
            injection_points=["upstream"],
            expected_impact_boundary=["mid"],
            actual_affected_nodes=["mid", "down"],
            injected_error_count=None,
        )
    )
    assert amp.status == "INSUFFICIENT_EVIDENCE"

    ent = compute_dispatch_entropy([])
    assert ent.status == "INSUFFICIENT_EVIDENCE"


def test_metrics_formulas_and_recompute() -> None:
    bucket = accumulate_token_bucket(
        [
            {"tokens": 20, "kind": "coordination"},
            {"tokens": 60, "kind": "agent_execution"},
            {"tokens": 10, "kind": "orchestrator"},
            {"tokens": 10, "kind": "evaluator"},
            {"tokens": 100, "kind": "cache"},
        ]
    )
    share = compute_coordination_token_share(bucket)
    assert share.status == "OK"
    assert share.value == pytest.approx(0.2)
    assert share.extras["engineering_alert"] is True
    assert share.extras["cache_tokens"] == 100

    amp = compute_error_amplification_factor(
        FaultInjectionTrace(
            injection_task_version="fi-v1",
            injection_points=["n1"],
            expected_impact_boundary=["n2"],
            actual_affected_nodes=["n2", "n3", "n4", "n5"],
            injected_error_count=1,
            independent_eval_evidence_refs=["ev-1"],
        )
    )
    assert amp.status == "OK"
    assert amp.value == pytest.approx(4.0)

    ent = compute_dispatch_entropy(
        [
            {"step_id": "s1", "candidate_weights": {"a": 1.0}},
            {"step_id": "s2", "candidate_weights": {"a": 0.5, "b": 0.5}},
            {"step_id": "s3", "candidate_weights": {"a": 0.25, "b": 0.25, "c": 0.5}},
        ]
    )
    assert ent.status == "OK"
    assert ent.value is not None
    assert "series" in ent.extras

    bundle = compute_all_metrics(
        token_bucket=bucket,
        fault_trace=FaultInjectionTrace(
            injection_task_version="fi-v1",
            injection_points=["n1"],
            expected_impact_boundary=["n2"],
            actual_affected_nodes=["n2", "n3"],
            injected_error_count=1,
            independent_eval_evidence_refs=["ev-1"],
        ),
        dispatch_steps=[
            {"step_id": "s1", "candidate_weights": {"a": 1.0, "b": 0.0}},
        ],
    )
    assert bundle["contract_version"]
    # Recomputable from raw fields.
    again = compute_coordination_token_share(
        TokenBucket(**bundle["recomputable_from"]["token_bucket"])
    )
    assert again.value == share.value


def test_mast_codes_positive_negative_low_confidence() -> None:
    assert len(MAST_CODES) == 9
    pos = classify_mast_failure(
        signals={"repeated_step_count": 3},
        original_failure_code="GENERIC",
        trajectory_refs=["turn:4"],
    )
    assert pos.mast_code == "MAST_STEP_REPETITION"
    assert is_mast_code(pos.mast_code)
    assert pos.effective_failure_code() == "MAST_STEP_REPETITION"

    neg = classify_mast_failure(signals={}, original_failure_code="TIMEOUT")
    assert neg.mast_code is None
    assert neg.effective_failure_code() == "TIMEOUT"

    low = classify_mast_failure(
        signals={"premature_stop": True},
        original_failure_code="EARLY_STOP",
        confidence_threshold=0.99,
    )
    assert low.low_confidence is True
    assert low.effective_failure_code() == "EARLY_STOP"


def test_member_contract_certification_invalidation() -> None:
    members = certified_hive_member_contracts()
    topo = enrich_topology_with_member_contracts(
        {
            "template_id": CERTIFIED_HIVE_TEMPLATE_ID,
            "strategy": "FIXED_TEMPLATE",
            "roles": [
                {"role": "pm", "capabilities": ["delivery-review-v1"]},
                {"role": "dev", "capabilities": ["product-surface-v1"]},
                {"role": "qa", "capabilities": ["delivery-review-v1"], "independent_reviewer": True},
            ],
            "invariants": ["producer_reviewer_separation"],
        },
        members=members,
    )
    cert1 = compute_template_certification(
        template_id=CERTIFIED_HIVE_TEMPLATE_ID,
        semantic_version="1.0.0",
        topology=topo,
        members=members,
    )
    # Change a member prompt/tool binding → new cert, old not inherited.
    members2 = list(members)
    altered = members2[1].model_copy(update={"tool_allowlist": ["read_file"]})
    members2[1] = altered
    cert2 = compute_template_certification(
        template_id=CERTIFIED_HIVE_TEMPLATE_ID,
        semantic_version="1.0.1",
        topology=topo,
        members=members2,
        prompt_skill_tool={"dev_prompt": "changed"},
    )
    check = validate_certification_inheritance(previous=cert1, current=cert2)
    assert check.accepted is False
    assert "certification_invalidated" in check.reason

    suite = run_template_regression_suite(
        template_id=CERTIFIED_HIVE_TEMPLATE_ID,
        topology=topo,
        certification=cert1,
    )
    assert len(suite) == 5
    assert all(r.passed for r in suite)


def test_task_features_prune_strong_sequential() -> None:
    candidates = [
        {
            "name": "single-agent-v1",
            "topology_json": {
                "template_id": "single-agent-v1",
                "strategy": "SINGLE_AGENT",
                "roles": [{"role": "executor"}],
            },
        },
        {
            "name": CERTIFIED_HIVE_TEMPLATE_ID,
            "topology_json": {
                "template_id": CERTIFIED_HIVE_TEMPLATE_ID,
                "strategy": "FIXED_TEMPLATE",
                "roles": [
                    {"role": "pm"},
                    {"role": "dev"},
                    {"role": "qa", "independent_reviewer": True},
                ],
                "invariants": ["producer_reviewer_separation"],
            },
        },
    ]
    features = TaskFeatures(
        tool_call_density=0.2,
        decomposability_score=0.8,
        sequential_dependency_score=0.9,
        single_agent_baseline_success_rate=0.2,
        independent_verification_required=True,
        estimated_parallelism_ceiling=0.5,
    )
    pruned = prune_organization_space(candidates, features)
    assert "single-agent-v1" in pruned.as_dict()["admitted_template_ids"]
    assert CERTIFIED_HIVE_TEMPLATE_ID in pruned.as_dict()["excluded_template_ids"]
    assert any(h.rule_id == "R2_STRONG_SEQUENTIAL" for h in pruned.hits)

    high_baseline = features.model_copy(update={"sequential_dependency_score": 0.1, "single_agent_baseline_success_rate": 0.6})
    pruned2 = prune_organization_space(candidates, high_baseline)
    assert any(h.rule_id == "R1_HIGH_SINGLE_AGENT_BASELINE" for h in pruned2.hits)


@pytest.mark.asyncio
async def test_execution_plan_checkpoint_and_no_reopen(db_sessions) -> None:
    goal_id = uuid.uuid4()
    async with db_sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                original_input="plan durability",
                created_by="tester",
                correlation_id=uuid.uuid4(),
                status=GoalState.ACTIVE.value,
                metadata_json={},
            )
        )
    svc = ExecutionPlanService(db_sessions)
    await svc.upsert_items(
        [
            UpsertPlanItem(goal_id=goal_id, item_key="a", content="first", status="completed"),
            UpsertPlanItem(
                goal_id=goal_id,
                item_key="b",
                content="second",
                status="pending",
                dependencies=["a"],
            ),
        ]
    )
    cp = await svc.checkpoint(goal_id)
    assert "a" in cp["completed_item_keys"]
    runnable = await svc.next_runnable(goal_id)
    assert [i.item_key for i in runnable] == ["b"]

    # Restore must not downgrade completed.
    await svc.restore_from_checkpoint(cp)
    items = {i.item_key: i for i in await svc.list_items(goal_id)}
    assert items["a"].status == "completed"

    with pytest.raises(Exception):
        await svc.upsert_items(
            [UpsertPlanItem(goal_id=goal_id, item_key="a", content="reopen", status="pending")]
        )


@pytest.mark.asyncio
async def test_dispatch_decision_entropy_replay(db_sessions) -> None:
    goal_id = uuid.uuid4()
    async with db_sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                original_input="dispatch",
                created_by="tester",
                correlation_id=uuid.uuid4(),
                status=GoalState.ACTIVE.value,
                metadata_json={},
            )
        )
    svc = DispatchDecisionService(db_sessions)
    await svc.record(
        DispatchDecisionInput(
            goal_id=goal_id,
            run_id=None,
            step_id="step-1",
            organization_version_id=None,
            source_agent_id="orch",
            selected_agent_id="dev",
            candidate_agent_ids=["dev", "qa"],
            candidate_weights={"dev": 0.7, "qa": 0.3},
            evidence_refs=["ev-1"],
            reason_code="CAPABILITY_MATCH",
            capability_scope=["product-surface-v1"],
            output_summary={"selected": "dev"},
        )
    )
    await svc.record(
        DispatchDecisionInput(
            goal_id=goal_id,
            run_id=None,
            step_id="step-2",
            organization_version_id=None,
            source_agent_id="orch",
            selected_agent_id="qa",
            candidate_agent_ids=["qa"],
            candidate_weights={"qa": 1.0},
            evidence_refs=["ev-2"],
            reason_code="INDEPENDENT_VERIFY",
        )
    )
    report = await svc.entropy_report(goal_id)
    assert report["dispatch_count"] == 2
    assert report["metric"]["status"] == "OK"
    assert report["replay"][0]["reason_code"] == "CAPABILITY_MATCH"
    assert report["replay"][0]["selected_agent_id"] == "dev"


@pytest.mark.asyncio
async def test_context_artifact_offload_and_rehydrate(db_sessions, tmp_path: Path) -> None:
    goal_id = uuid.uuid4()
    async with db_sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                original_input="offload",
                created_by="tester",
                correlation_id=uuid.uuid4(),
                status=GoalState.ACTIVE.value,
                metadata_json={},
            )
        )
    big = "x" * (20_000 * 4 + 10)
    assert should_offload_tool_result(big) is True
    svc = ContextArtifactService(db_sessions, artifact_root=tmp_path)
    ref = await svc.offload_tool_result(
        goal_id=goal_id, text=big, producer_ref="tester"
    )
    assert ref is not None
    loaded = await svc.read_by_hash(ref.content_hash)
    assert loaded == big

    summary = build_structured_compact_summary(
        goal_intent="build app",
        produced_artifacts=[ref.uri],
        open_risks=["budget"],
        next_actions=["verify"],
        hard_constraints=["no prod write"],
        permit_state={"status": "NONE"},
        open_human_tasks=[],
    )
    ok = rehydrate_context(
        summary=summary,
        artifact_payloads={"tool": big},
        expected_hashes={"tool": ref.content_hash},
    )
    assert ok["ok"] is True
    bad = rehydrate_context(
        summary=summary,
        artifact_payloads={"tool": "tampered"},
        expected_hashes={"tool": ref.content_hash},
    )
    assert bad["ok"] is False


def test_p24_experiment_keeps_adaptive_gated() -> None:
    exp = P24FrozenExperiment(
        FrozenExperimentConfig(
            name="abc",
            task_set_hash="abc",
            model_freeze="m1",
            tool_freeze="t1",
            budget_units=100,
        )
    )
    for i in range(5):
        exp.record(
            VariantRunResult(
                variant="A_single_agent",
                task_id=f"t{i}",
                passed=True,
                cost_units=10,
                coordination_tokens=1,
                total_tokens=20,
            )
        )
        exp.record(
            VariantRunResult(
                variant="B_certified_hive",
                task_id=f"t{i}",
                passed=True,
                cost_units=12,
                coordination_tokens=5,
                total_tokens=30,
            )
        )
    report = exp.report()
    assert report["org_adaptive_status"] == ROLLOUT_NOT_ALLOWED
    assert report["decision"] in {
        "KEEP_SINGLE_AGENT",
        "KEEP_SINGLE_AGENT_PENDING_REVIEW",
        "INSUFFICIENT_EVIDENCE",
    }


def test_p25_gate_blocks_without_go() -> None:
    blocked = evaluate_adaptive_rollout_gate(
        {"decision": "KEEP_SINGLE_AGENT", "org_adaptive_status": ROLLOUT_NOT_ALLOWED}
    )
    assert blocked.allowed is False
    assert blocked.status == ROLLOUT_NOT_ALLOWED

    # Even GO decision without lifting adaptive status stays blocked.
    still = evaluate_adaptive_rollout_gate(
        {"decision": "GO_ADAPTIVE_ORG", "org_adaptive_status": ROLLOUT_NOT_ALLOWED}
    )
    assert still.allowed is False

    proposal = enrich_adaptive_proposal_skeleton(
        {"proposed_template": "x"}, decision_record=None
    )
    assert proposal["activation_allowed"] is False
    assert proposal["rollout_gate"] == ROLLOUT_NOT_ALLOWED


def test_a2a_projection_boundary() -> None:
    assert project_run_state("WAITING_HUMAN") == "input_required"
    card = project_agent_card(
        agent_id="a1", name="qa", capabilities=["delivery-review-v1"]
    )
    assert card["grants_goal_permission"] is False
    proj = project_envelope_to_a2a(
        {"goal_id": "g1", "status": "QUEUED", "permit_pending": True}
    )
    assert proj["contextId"] == "g1"
    assert proj["state"] == "submitted"
    assert proj["auth"] == "auth_required"
    assert proj["internal_envelope_retained"] is True
    with pytest.raises(ValueError):
        assert_not_replacing_kernel("CrewAI")
