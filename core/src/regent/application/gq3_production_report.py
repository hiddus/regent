"""GQ-3 production report helpers — map live Goal流 into StrategyRunResult.

Decision contract (decision-note-gq3-window): samples come from production Goals,
not FrozenTaskSet. Attribution: generation_plans.contract_json.generator_ref.
Unit of analysis: one independent Goal (intent-to-treat = first plan in window).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, Sequence

from regent.application.generation_strategy_experiment import (
    GQ_VARIANTS,
    GenerationStrategyExperiment,
    GenerationStrategyExperimentConfig,
    PreregisteredThresholds,
    StrategyRunResult,
    default_frozen_task_set,
    default_preregistered_thresholds,
)
from regent.application.generator_metadata import AGENTIC_REF, ARTIFACT_BACKED_REF

# Token → cost_units (relative; freeze for this window).
_COST_PER_1K_TOKENS = 1.0

# Decision-note stop rule: agentic fail rate − control fail rate ≥ this → stop.
STOP_FAIL_RATE_DELTA = 0.15


def variant_from_generator_ref(ref: str | None) -> str | None:
    if ref == ARTIFACT_BACKED_REF:
        return "artifact_backed"
    if ref == AGENTIC_REF:
        return "agentic"
    return None


@dataclass(frozen=True, slots=True)
class GoalArmObservation:
    """One independent Goal assigned to a generation-strategy arm."""

    goal_id: str
    variant: str
    goal_status: str
    repair_rounds: int
    human_intervened: bool
    input_tokens: int
    output_tokens: int
    latency_ms: float
    first_plan_at: str | None
    generator_ref: str
    safety_incident: bool = False
    preview_ready: bool = False


def observation_passed(goal_status: str) -> bool:
    """Success = Goal attained (ACHIEVED). Other terminals count as fail/incomplete."""
    return str(goal_status).upper() == "ACHIEVED"


def observation_preview_ready(obs: GoalArmObservation) -> bool:
    """Parallel product signal: preview URL / stage reached (does not replace ACHIEVED)."""
    if obs.preview_ready:
        return True
    return str(obs.goal_status).upper() == "ACHIEVED"


def observation_to_result(obs: GoalArmObservation) -> StrategyRunResult:
    tokens = max(0, int(obs.input_tokens) + int(obs.output_tokens))
    cost = (tokens / 1000.0) * _COST_PER_1K_TOKENS
    passed = observation_passed(obs.goal_status)
    return StrategyRunResult(
        variant=obs.variant,
        task_id=obs.goal_id,
        passed=passed,
        cost_units=float(cost),
        latency_ms=max(0.0, float(obs.latency_ms)),
        first_runnable=passed,
        repair_rounds=max(0, int(obs.repair_rounds)),
        human_intervened=bool(obs.human_intervened),
        safety_incident=bool(obs.safety_incident),
        shadow=False,
        metrics={
            "goal_status": obs.goal_status,
            "generator_ref": obs.generator_ref,
            "input_tokens": obs.input_tokens,
            "output_tokens": obs.output_tokens,
            "first_plan_at": obs.first_plan_at,
            "preview_ready": observation_preview_ready(obs),
        },
    )


def fail_rate(results: Sequence[StrategyRunResult]) -> float | None:
    if not results:
        return None
    return sum(1 for r in results if not r.passed) / len(results)


def stop_rule_triggered(
    results: Sequence[StrategyRunResult],
    *,
    delta: float = STOP_FAIL_RATE_DELTA,
) -> dict[str, Any]:
    """Decision-note: relative fail-rate +15pp vs control → stop."""
    by: dict[str, list[StrategyRunResult]] = {v: [] for v in GQ_VARIANTS}
    for r in results:
        by.setdefault(r.variant, []).append(r)
    a = fail_rate(by.get("artifact_backed") or [])
    b = fail_rate(by.get("agentic") or [])
    if a is None or b is None:
        return {"triggered": False, "reason": "insufficient_arms", "delta": None}
    d = b - a
    return {
        "triggered": d >= delta,
        "reason": f"agentic_fail_rate - control_fail_rate = {d:.3f} (threshold {delta})",
        "delta": d,
        "artifact_backed_fail_rate": a,
        "agentic_fail_rate": b,
        "threshold": delta,
    }


def window_expired(opened_at: datetime, *, max_days: int, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=UTC)
    return (now - opened_at).total_seconds() >= max_days * 86400


def build_production_experiment(
    observations: Iterable[GoalArmObservation],
    *,
    thresholds: PreregisteredThresholds | None = None,
    model_freeze: str = "production-live",
    tool_freeze: str = "production-live",
    window_label: str = "gq3-production-goal-stream",
) -> GenerationStrategyExperiment:
    """Populate harness from production observations (skips FrozenTaskSet runner)."""
    thr = thresholds or default_preregistered_thresholds()
    # Task set hash still required by report schema; use default contract set
    # marked as non-eval placeholder — production samples are the real n.
    config = GenerationStrategyExperimentConfig(
        name=window_label,
        task_set=default_frozen_task_set(),
        thresholds=thr,
        model_freeze=model_freeze,
        tool_freeze=tool_freeze,
        budget_units=0.0,
        confidence_level=0.95,
        shadow_isolation=False,
        canary_allowed=True,
    )
    exp = GenerationStrategyExperiment(config)
    for obs in observations:
        if obs.variant not in GQ_VARIANTS:
            continue
        exp.record(observation_to_result(obs))
    return exp


def enrich_report(
    report: dict[str, Any],
    *,
    observations: Sequence[GoalArmObservation],
    window_opened_at: str,
    window_max_days: int,
    since: str,
    until: str | None,
) -> dict[str, Any]:
    """Attach production provenance + stop/expiry advisories to experiment report."""
    results = [observation_to_result(o) for o in observations]
    stop = stop_rule_triggered(results)
    out = dict(report)
    out["production_window"] = {
        "opened_at": window_opened_at,
        "max_days": window_max_days,
        "sample_since": since,
        "sample_until": until,
        "unit_of_analysis": "independent_goal",
        "assignment": "intent_to_treat_first_plan_in_window",
        "success_definition": "goal.status == ACHIEVED",
        "n_goals": len(observations),
        "n_by_variant": {
            v: sum(1 for o in observations if o.variant == v) for v in GQ_VARIANTS
        },
    }
    out["stop_rule"] = stop
    out["source"] = "production_goal_stream"
    # If window expired and still insufficient, force decision label for operators.
    if report.get("decision") == "INSUFFICIENT_EVIDENCE":
        try:
            opened = datetime.fromisoformat(window_opened_at.replace("Z", "+00:00"))
        except ValueError:
            opened = None
        if opened is not None and window_expired(opened, max_days=window_max_days):
            out["decision"] = "INSUFFICIENT_EVIDENCE"
            out["rationale"] = (
                f"{report.get('rationale')}; window max_days={window_max_days} elapsed "
                "→ close canary without promotion"
            )
            out["window_closed"] = True
    if stop.get("triggered"):
        out["decision"] = "KEEP_ARTIFACT_BACKED"
        out["rationale"] = (
            f"stop rule: {stop.get('reason')}; "
            f"override prior decision={report.get('decision')}"
        )
        out["guardrail_trips"] = list(report.get("guardrail_trips") or []) + [
            "fail_rate_delta_15pp"
        ]

    # Funnel health gate: zero pass_rate with meaningful sample ⇒ degraded window.
    # Blocks GQ-4 narrative until delivery pipeline is healthy again.
    summaries = dict(out.get("summaries") or {})
    n_goals = len(observations)
    degraded = False
    degraded_reasons: list[str] = []
    if n_goals >= 10:
        for arm in ("artifact-backed", "agentic"):
            arm_summary = summaries.get(arm) or {}
            n_arm = int(arm_summary.get("n") or 0)
            pass_rate = arm_summary.get("pass_rate")
            if n_arm >= 3 and pass_rate is not None and float(pass_rate) <= 0.0:
                degraded = True
                degraded_reasons.append(f"{arm}_pass_rate_zero_n={n_arm}")
    out["funnel_degraded"] = degraded
    out["funnel_health"] = {
        "degraded": degraded,
        "reasons": degraded_reasons,
        "recovery_criteria": [
            "stale_active_over_2h_ratio < 20%",
            "generation_run_requested_pending_backlog near zero",
            "daily_goals_reaching_plan healthy",
            "at least one arm pass_rate > 0 with n>=10 before lifting degraded",
        ],
    }
    if degraded:
        prior = out.get("decision")
        if prior == "PROMOTE_AGENTIC_CANDIDATE":
            out["decision"] = "KEEP_ARTIFACT_BACKED"
        out["rationale"] = (
            f"{out.get('rationale') or ''}; funnel_degraded: "
            + ", ".join(degraded_reasons)
            + " → pause GQ-4 promotion until delivery pipeline is healthy"
        ).strip("; ")

    # Parallel product metric (does not affect ACHIEVED / promotion contract).
    preview_by_arm: dict[str, dict[str, float | int]] = {}
    for variant_key, label in (
        ("artifact_backed", "artifact-backed"),
        ("agentic", "agentic"),
    ):
        arm_obs = [o for o in observations if o.variant == variant_key]
        n = len(arm_obs)
        ready_n = sum(1 for o in arm_obs if observation_preview_ready(o))
        preview_by_arm[label] = {
            "n": n,
            "preview_ready_n": ready_n,
            "preview_ready_rate": (ready_n / n) if n else 0.0,
        }
    all_n = len(observations)
    all_ready = sum(1 for o in observations if observation_preview_ready(o))
    out["preview_ready"] = {
        "n": all_n,
        "preview_ready_n": all_ready,
        "preview_ready_rate": (all_ready / all_n) if all_n else 0.0,
        "by_arm": preview_by_arm,
        "note": "product signal only; GQ-4 promotion still requires ACHIEVED pass_rate contract",
    }
    return out
