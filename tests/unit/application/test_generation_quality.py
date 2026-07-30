"""GQ-0…GQ-4 generation quality contract tests."""

from __future__ import annotations

import pytest

from regent.application.generator_factory import build_code_generator
from regent.application.generator_metadata import (
    AGENTIC_REF,
    ARTIFACT_BACKED_REF,
    assert_generator_consistency,
    metadata_for_strategy,
)
from regent.application.generation_strategy_experiment import (
    GQ_VARIANTS,
    FrozenTaskSpec,
    GenerationStrategyExperiment,
    GenerationStrategyExperimentConfig,
    StrategyRunResult,
    default_frozen_task_set,
    default_preregistered_thresholds,
    gq4_default_switch_gate,
)
from regent.application.generation_strategy_policy import (
    canary_rollout_allowed,
    kill_switch_contract,
    resolve_effective_generation_strategy,
    shadow_isolation_contract,
    stable_canary_bucket,
)
from regent.application.failure_envelope import (
    STAGE_REPAIR_POLICY,
    clip_error_summary,
    is_non_retryable,
)
from regent.application.quality_metrics import UserQualityMetrics, aggregate_user_quality
from regent.agent.generator import AgenticCodeGenerator
from regent.agent.verification import _resolve_test_command
from regent.config import Settings
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.code_generator import ArtifactBackedCodeGenerator


class _FakeProvider:
    pass


class _FakeArtifacts:
    root = None


def test_generator_metadata_protocol_on_both_implementations(tmp_path) -> None:
    artifact = ArtifactBackedCodeGenerator(_FakeProvider(), _FakeArtifacts())  # type: ignore[arg-type]
    assert artifact.generator_type == "artifact-backed"
    assert artifact.generator_ref == ARTIFACT_BACKED_REF
    assert artifact.prompt_version == "code-generation-v1"

    agentic = AgenticCodeGenerator(
        _FakeProvider(),  # type: ignore[arg-type]
        _FakeArtifacts(),  # type: ignore[arg-type]
        workspace_root=tmp_path,
    )
    assert agentic.generator_type == "agentic"
    assert agentic.generator_ref == AGENTIC_REF


def test_assert_generator_consistency_fail_closed_on_mismatch(tmp_path) -> None:
    """Annotated agentic but object is artifact-backed → reject (no silent fallback)."""
    wrong = ArtifactBackedCodeGenerator(_FakeProvider(), _FakeArtifacts())  # type: ignore[arg-type]
    with pytest.raises(DomainError) as exc:
        assert_generator_consistency(
            strategy="agentic",
            generator=wrong,
            plan_id="plan-1",
            run_id="run-1",
            contract_generator_ref=AGENTIC_REF,
            contract_prompt_version="agentic-generation-v1",
        )
    assert exc.value.code == ErrorCode.GENERATOR_METADATA_MISMATCH
    assert "generator_type" in exc.value.message


def test_assert_generator_consistency_ok_when_aligned(tmp_path) -> None:
    gen = ArtifactBackedCodeGenerator(_FakeProvider(), _FakeArtifacts())  # type: ignore[arg-type]
    assert_generator_consistency(
        strategy="artifact-backed",
        generator=gen,
        contract_generator_ref=ARTIFACT_BACKED_REF,
        contract_prompt_version="code-generation-v1",
    )


def test_factory_dispatches_both_strategies(tmp_path, monkeypatch) -> None:
    from regent.infrastructure.artifact_store import FileArtifactStore

    artifacts = FileArtifactStore(tmp_path / "arts")
    settings = Settings(
        generation_strategy="artifact-backed",
        workspace_root=str(tmp_path / "ws"),
    )
    gen = build_code_generator(settings, _FakeProvider(), artifacts, enforce_consistency=True)  # type: ignore[arg-type]
    assert isinstance(gen, ArtifactBackedCodeGenerator)

    settings_a = Settings(
        generation_strategy="agentic",
        workspace_root=str(tmp_path / "ws2"),
    )
    gen_a = build_code_generator(settings_a, _FakeProvider(), artifacts, enforce_consistency=True)  # type: ignore[arg-type]
    assert isinstance(gen_a, AgenticCodeGenerator)


def test_kill_switch_forces_fallback() -> None:
    settings = Settings(
        generation_strategy="agentic",
        generation_strategy_kill_switch=True,
        generation_strategy_fallback="artifact-backed",
    )
    assert resolve_effective_generation_strategy(settings, goal_id="g1") == "artifact-backed"


def test_canary_stable_bucket_and_gate() -> None:
    a = stable_canary_bucket("goal-aaa")
    b = stable_canary_bucket("goal-aaa")
    assert a == b
    assert 0 <= a < 100
    assert canary_rollout_allowed(kill_switch=False, gq2_closed=False) is False
    assert canary_rollout_allowed(kill_switch=False, gq2_closed=True) is True
    assert canary_rollout_allowed(kill_switch=True, gq2_closed=True) is False

    settings = Settings(
        generation_strategy="artifact-backed",
        generation_strategy_canary_percent=100,
        generation_strategy_canary_variant="agentic",
    )
    assert resolve_effective_generation_strategy(settings, goal_id="any") == "agentic"


def test_shadow_and_kill_switch_contracts_frozen() -> None:
    shadow = shadow_isolation_contract()
    assert shadow["forbid_publish"] is True
    assert shadow["forbid_external_side_effects"] is True
    kill = kill_switch_contract()
    assert kill["forbid_mid_run_generator_swap"] is True


def test_generation_strategy_experiment_rejects_p24_org_dimensions() -> None:
    cfg = GenerationStrategyExperimentConfig(
        name="gq-unit",
        task_set=default_frozen_task_set(),
        thresholds=default_preregistered_thresholds(),
        model_freeze="test-model",
        tool_freeze="test-tools",
        budget_units=10.0,
    )
    exp = GenerationStrategyExperiment(cfg)
    with pytest.raises(ValueError, match="P2-4"):
        exp.record(
            StrategyRunResult(
                variant="A_single_agent",
                task_id="t1",
                passed=True,
                cost_units=1.0,
                latency_ms=100,
            )
        )


def test_generation_strategy_experiment_report_and_gq4_gate() -> None:
    thr = default_preregistered_thresholds()
    # Lower sample gate for unit test.
    thr = type(thr)(
        min_success_rate_lift=0.05,
        non_inferiority_margin=thr.non_inferiority_margin,
        max_mean_cost_degradation=thr.max_mean_cost_degradation,
        max_p95_latency_degradation=thr.max_p95_latency_degradation,
        min_sample_size_per_arm=3,
        max_repair_rounds_mean=thr.max_repair_rounds_mean,
        max_human_intervention_rate=thr.max_human_intervention_rate,
    )
    cfg = GenerationStrategyExperimentConfig(
        name="gq-unit",
        task_set=default_frozen_task_set(),
        thresholds=thr,
        model_freeze="test-model",
        tool_freeze="test-tools",
        budget_units=10.0,
        canary_allowed=False,
    )
    exp = GenerationStrategyExperiment(cfg)
    for i in range(3):
        exp.record(
            StrategyRunResult(
                variant="artifact_backed",
                task_id=f"a{i}",
                passed=i < 1,
                cost_units=2.0,
                latency_ms=100,
                first_runnable=i < 1,
            )
        )
        exp.record(
            StrategyRunResult(
                variant="agentic",
                task_id=f"b{i}",
                passed=True,
                cost_units=1.5,
                latency_ms=90,
                first_runnable=True,
                repair_rounds=1,
            )
        )
    report = exp.report()
    assert report["not_p24_org_dimensions"] is True
    assert set(report["variants"]) == set(GQ_VARIANTS)
    assert "user_quality" in report["summaries"]["agentic"]
    assert report["decision"] == "PROMOTE_AGENTIC_CANDIDATE"

    gate = gq4_default_switch_gate(report, kill_switch=False)
    assert gate["activation_allowed"] is True
    blocked = gq4_default_switch_gate(report, kill_switch=True)
    assert blocked["activation_allowed"] is False
    assert blocked["proposed_default"] == "artifact-backed"


def test_user_quality_metrics_skeleton() -> None:
    agg = aggregate_user_quality(
        [
            UserQualityMetrics(
                first_runnable=True,
                repair_rounds=1,
                human_intervened=False,
                wall_time_to_usable_ms=1000,
                passed=True,
            ),
            UserQualityMetrics(
                first_runnable=False,
                repair_rounds=3,
                human_intervened=True,
                wall_time_to_usable_ms=None,
                passed=False,
            ),
        ]
    )
    assert agg["status"] == "OK"
    assert agg["first_runnable_rate"] == 0.5
    assert agg["human_intervention_rate"] == 0.5


def test_failure_envelope_policy_and_clip() -> None:
    assert "build" in STAGE_REPAIR_POLICY
    assert is_non_retryable("generation", "GENERATOR_METADATA_MISMATCH")
    assert not is_non_retryable("build", "TIMEOUT")
    clipped = clip_error_summary("x" * 10_000)
    assert len(clipped) < 10_000
    assert "truncated" in clipped


def test_resolve_test_command_and_degraded_missing() -> None:
    assert _resolve_test_command({}, {}) is None
    cmd = _resolve_test_command({"tests/test_app.py": "def test_ok(): pass"}, {})
    assert cmd is not None
    assert "pytest" in cmd
    explicit = _resolve_test_command({}, {"test_command": "python -m unittest"})
    assert explicit == "python -m unittest"


def test_metadata_for_strategy_table() -> None:
    assert metadata_for_strategy("agentic")["generator_ref"] == AGENTIC_REF
    assert metadata_for_strategy("artifact-backed")["generator_ref"] == ARTIFACT_BACKED_REF


def test_frozen_task_set_isolates_tune_and_final() -> None:
    ts = default_frozen_task_set()
    assert ts.tune_task_ids
    assert ts.final_task_ids
    assert set(ts.tune_task_ids).isdisjoint(set(ts.final_task_ids))
    assert all(isinstance(t, FrozenTaskSpec) for t in ts.tasks)
    assert ts.content_hash()
