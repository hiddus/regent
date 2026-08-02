"""Unit tests for GQ-3 production report mapping (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from regent.application.gq3_production_report import (
    GoalArmObservation,
    build_production_experiment,
    enrich_report,
    observation_passed,
    observation_to_result,
    stop_rule_triggered,
    variant_from_generator_ref,
    window_expired,
)
from regent.application.generator_metadata import AGENTIC_REF, ARTIFACT_BACKED_REF


def test_variant_from_generator_ref() -> None:
    assert variant_from_generator_ref(ARTIFACT_BACKED_REF) == "artifact_backed"
    assert variant_from_generator_ref(AGENTIC_REF) == "agentic"
    assert variant_from_generator_ref("other") is None


def test_success_is_achieved_only() -> None:
    assert observation_passed("ACHIEVED") is True
    assert observation_passed("EXHAUSTED") is False
    assert observation_passed("ACTIVE") is False


def test_build_report_insufficient_then_stop_override() -> None:
    obs = [
        GoalArmObservation(
            goal_id=f"a-{i}",
            variant="artifact_backed",
            goal_status="ACHIEVED",
            repair_rounds=0,
            human_intervened=False,
            input_tokens=1000,
            output_tokens=500,
            latency_ms=1000,
            first_plan_at="2026-07-31T12:00:00+00:00",
            generator_ref=ARTIFACT_BACKED_REF,
        )
        for i in range(5)
    ] + [
        GoalArmObservation(
            goal_id=f"b-{i}",
            variant="agentic",
            goal_status="EXHAUSTED",
            repair_rounds=2,
            human_intervened=True,
            input_tokens=2000,
            output_tokens=800,
            latency_ms=5000,
            first_plan_at="2026-07-31T12:00:00+00:00",
            generator_ref=AGENTIC_REF,
        )
        for i in range(5)
    ]
    exp = build_production_experiment(obs)
    report = exp.report(actor="test")
    assert report["decision"] == "INSUFFICIENT_EVIDENCE"
    results = [observation_to_result(o) for o in obs]
    stop = stop_rule_triggered(results)
    assert stop["triggered"] is True
    enriched = enrich_report(
        report,
        observations=obs,
        window_opened_at="2026-07-31T10:00:00+00:00",
        window_max_days=21,
        since="2026-07-31T10:00:00+00:00",
        until=None,
    )
    assert enriched["decision"] == "KEEP_ARTIFACT_BACKED"
    assert "fail_rate_delta_15pp" in enriched["guardrail_trips"]
    assert "preview_ready" in enriched
    assert enriched["preview_ready"]["n"] == 10


def test_window_expired() -> None:
    opened = datetime(2026, 7, 1, tzinfo=UTC)
    assert window_expired(opened, max_days=21, now=datetime(2026, 7, 23, tzinfo=UTC))
    assert not window_expired(
        opened, max_days=21, now=opened + timedelta(days=10)
    )


def test_enrich_report_invalid_baseline_when_control_zero_and_starved() -> None:
    obs = [
        GoalArmObservation(
            goal_id=f"c-{i}",
            variant="artifact_backed",
            goal_status="EXHAUSTED",
            repair_rounds=1,
            human_intervened=False,
            input_tokens=1000,
            output_tokens=500,
            latency_ms=1000,
            first_plan_at="2026-07-31T12:00:00+00:00",
            generator_ref=ARTIFACT_BACKED_REF,
        )
        for i in range(12)
    ] + [
        GoalArmObservation(
            goal_id=f"a-{i}",
            variant="agentic",
            goal_status="EXHAUSTED",
            repair_rounds=2,
            human_intervened=True,
            input_tokens=2000,
            output_tokens=800,
            latency_ms=5000,
            first_plan_at="2026-07-31T12:00:00+00:00",
            generator_ref=AGENTIC_REF,
        )
        for i in range(2)
    ]
    exp = build_production_experiment(obs)
    report = exp.report(actor="test")
    enriched = enrich_report(
        report,
        observations=obs,
        window_opened_at="2026-07-31T10:00:00+00:00",
        window_max_days=21,
        since="2026-07-31T10:00:00+00:00",
        until=None,
    )
    assert enriched["decision"] == "INVALID_BASELINE"
    assert enriched["baseline_invalid"] is True
    assert enriched["invalid_baseline_reasons"] == [
        "control_verified_success_rate_zero",
        "candidate_starved_of_traffic",
        "funnel_gate_depends_on_failed_control",
        "cost_and_freeze_metadata_incomplete",
    ]
    assert enriched["artifact_backed_role"]["role"] == "FALLBACK_ONLY"
    assert enriched["artifact_backed_role"]["eligible_as_champion"] is False

