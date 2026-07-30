"""Independent generation-strategy experiment contract (GQ-0 / Tech-Spec §13.7).

Reuses P2-4 statistics / DecisionRecord shapes but MUST NOT occupy the
A_single_agent / B_certified_hive / C_control organization dimensions.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Sequence

from regent.application.p1_contracts import canonical_hash
from regent.application.quality_metrics import (
    QUALITY_METRICS_CONTRACT_VERSION,
    UserQualityMetrics,
    aggregate_user_quality,
)

GQ_EXPERIMENT_VERSION = "gq-generation-strategy/v1"
# Distinct from P2-4 org variants — do not reuse those labels.
GQ_VARIANTS = ("artifact_backed", "agentic")


@dataclass(frozen=True, slots=True)
class PreregisteredThresholds:
    """Frozen before any experimental run (GQ-0 preregistration)."""

    min_success_rate_lift: float = 0.05
    non_inferiority_margin: float = 0.02
    max_mean_cost_degradation: float = 0.25
    max_p95_latency_degradation: float = 0.30
    min_sample_size_per_arm: int = 30
    max_repair_rounds_mean: float = 3.0
    max_human_intervention_rate: float = 0.20
    stop_on_safety_incident: bool = True
    stop_on_serious_quality_regression: bool = True


@dataclass(frozen=True, slots=True)
class FrozenTaskSpec:
    task_id: str
    scenario: str
    difficulty: Literal["easy", "medium", "hard"]
    framework: str
    split: Literal["tune", "final"]


@dataclass(frozen=True, slots=True)
class FrozenTaskSet:
    name: str
    version: str
    tasks: tuple[FrozenTaskSpec, ...]
    blind_eval_owner: str
    tune_task_ids: tuple[str, ...]
    final_task_ids: tuple[str, ...]

    def content_hash(self) -> str:
        return canonical_hash(
            {
                "name": self.name,
                "version": self.version,
                "tasks": [
                    {
                        "task_id": t.task_id,
                        "scenario": t.scenario,
                        "difficulty": t.difficulty,
                        "framework": t.framework,
                        "split": t.split,
                    }
                    for t in self.tasks
                ],
                "blind_eval_owner": self.blind_eval_owner,
                "tune_task_ids": list(self.tune_task_ids),
                "final_task_ids": list(self.final_task_ids),
            }
        )


@dataclass(frozen=True, slots=True)
class StrategyRunResult:
    variant: str  # artifact_backed | agentic
    task_id: str
    passed: bool
    cost_units: float
    latency_ms: float
    first_runnable: bool = False
    repair_rounds: int = 0
    human_intervened: bool = False
    safety_incident: bool = False
    shadow: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GenerationStrategyExperimentConfig:
    name: str
    task_set: FrozenTaskSet
    thresholds: PreregisteredThresholds
    model_freeze: str
    tool_freeze: str
    budget_units: float
    confidence_level: float = 0.95
    shadow_isolation: bool = True
    # Canary only after GQ-2 feedback loop is closed (diagnosis order).
    canary_allowed: bool = False


def default_preregistered_thresholds() -> PreregisteredThresholds:
    return PreregisteredThresholds()


def default_frozen_task_set() -> FrozenTaskSet:
    """Minimal representative freeze for contract tests (not a production eval set)."""
    tasks = (
        FrozenTaskSpec(
            task_id="gq-flask-hello",
            scenario="first_runnable_web",
            difficulty="easy",
            framework="flask",
            split="tune",
        ),
        FrozenTaskSpec(
            task_id="gq-fastapi-crud",
            scenario="api_crud",
            difficulty="medium",
            framework="fastapi",
            split="tune",
        ),
        FrozenTaskSpec(
            task_id="gq-static-landing",
            scenario="marketing_landing",
            difficulty="easy",
            framework="static-html",
            split="final",
        ),
        FrozenTaskSpec(
            task_id="gq-flask-auth",
            scenario="auth_form",
            difficulty="hard",
            framework="flask",
            split="final",
        ),
    )
    return FrozenTaskSet(
        name="gq-generation-strategy-v1",
        version="2026-07-31",
        tasks=tasks,
        blind_eval_owner="regent-gq-blind-eval",
        tune_task_ids=tuple(t.task_id for t in tasks if t.split == "tune"),
        final_task_ids=tuple(t.task_id for t in tasks if t.split == "final"),
    )


def _wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((centre - margin) / denom, (centre + margin) / denom)


def summarize_strategy_arm(results: Sequence[StrategyRunResult]) -> dict[str, Any]:
    n = len(results)
    if n == 0:
        return {
            "n": 0,
            "pass_rate": None,
            "ci95": None,
            "mean_cost": None,
            "p95_latency_ms": None,
            "user_quality": None,
            "status": "INSUFFICIENT_EVIDENCE",
        }
    successes = sum(1 for r in results if r.passed)
    costs = [r.cost_units for r in results]
    latencies = sorted(r.latency_ms for r in results)
    p95_idx = min(len(latencies) - 1, max(0, math.ceil(0.95 * len(latencies)) - 1))
    ci = _wilson_interval(successes, n)
    user = aggregate_user_quality(
        [
            UserQualityMetrics(
                first_runnable=r.first_runnable,
                repair_rounds=r.repair_rounds,
                human_intervened=r.human_intervened,
                wall_time_to_usable_ms=r.latency_ms if r.passed else None,
                passed=r.passed,
            )
            for r in results
        ]
    )
    return {
        "n": n,
        "pass_rate": successes / n,
        "ci95": None if ci is None else {"low": round(ci[0], 4), "high": round(ci[1], 4)},
        "mean_cost": sum(costs) / n,
        "cost_per_verified_success": (sum(costs) / successes) if successes else None,
        "p95_latency_ms": latencies[p95_idx],
        "user_quality": user,
        "safety_incidents": sum(1 for r in results if r.safety_incident),
        "status": "OK",
    }


class GenerationStrategyExperiment:
    """In-process harness for artifact-backed vs agentic (not org Hive A/B/C)."""

    def __init__(self, config: GenerationStrategyExperimentConfig) -> None:
        self.config = config
        self._results: list[StrategyRunResult] = []

    def record(self, result: StrategyRunResult) -> None:
        if result.variant not in GQ_VARIANTS:
            raise ValueError(
                f"variant {result.variant!r} is not a generation-strategy arm; "
                f"do not use P2-4 org dimensions here"
            )
        self._results.append(result)

    def report(self, *, actor: str = "regent-core") -> dict[str, Any]:
        by_variant: dict[str, list[StrategyRunResult]] = {v: [] for v in GQ_VARIANTS}
        for row in self._results:
            by_variant.setdefault(row.variant, []).append(row)

        summaries = {k: summarize_strategy_arm(v) for k, v in by_variant.items()}
        thr = self.config.thresholds
        a = summaries.get("artifact_backed") or summarize_strategy_arm([])
        b = summaries.get("agentic") or summarize_strategy_arm([])

        decision = "INSUFFICIENT_EVIDENCE"
        rationale = "missing variant samples"
        guardrail_trips: list[str] = []

        if a.get("status") == "OK" and b.get("status") == "OK":
            if a["n"] < thr.min_sample_size_per_arm or b["n"] < thr.min_sample_size_per_arm:
                decision = "INSUFFICIENT_EVIDENCE"
                rationale = (
                    f"need ≥{thr.min_sample_size_per_arm} samples/arm "
                    f"(got artifact={a['n']}, agentic={b['n']})"
                )
            else:
                a_rate = float(a["pass_rate"])
                b_rate = float(b["pass_rate"])
                a_cost = float(a["mean_cost"] or 0)
                b_cost = float(b["mean_cost"] or 0)
                a_p95 = float(a["p95_latency_ms"] or 0)
                b_p95 = float(b["p95_latency_ms"] or 0)
                b_user = b.get("user_quality") or {}

                if b.get("safety_incidents", 0) and thr.stop_on_safety_incident:
                    guardrail_trips.append("safety_incident")
                if (
                    b_user.get("mean_repair_rounds") is not None
                    and float(b_user["mean_repair_rounds"]) > thr.max_repair_rounds_mean
                ):
                    guardrail_trips.append("repair_rounds")
                if (
                    b_user.get("human_intervention_rate") is not None
                    and float(b_user["human_intervention_rate"])
                    > thr.max_human_intervention_rate
                ):
                    guardrail_trips.append("human_intervention")
                if a_cost > 0 and (b_cost - a_cost) / a_cost > thr.max_mean_cost_degradation:
                    guardrail_trips.append("cost_degradation")
                if a_p95 > 0 and (b_p95 - a_p95) / a_p95 > thr.max_p95_latency_degradation:
                    guardrail_trips.append("latency_degradation")

                lift = b_rate - a_rate
                if guardrail_trips:
                    decision = "KEEP_ARTIFACT_BACKED"
                    rationale = f"guardrails tripped: {', '.join(guardrail_trips)}"
                elif lift >= thr.min_success_rate_lift:
                    decision = "PROMOTE_AGENTIC_CANDIDATE"
                    rationale = (
                        f"agentic lift={lift:.3f} ≥ {thr.min_success_rate_lift}; "
                        "requires GQ-4 DecisionRecord before default switch"
                    )
                elif lift >= -thr.non_inferiority_margin and b_cost <= a_cost * (
                    1 + thr.max_mean_cost_degradation
                ):
                    decision = "NON_INFERIOR_REVIEW"
                    rationale = "within non-inferiority margin; review before promote"
                else:
                    decision = "KEEP_ARTIFACT_BACKED"
                    rationale = f"insufficient lift ({lift:.3f})"

        record = {
            "version": GQ_EXPERIMENT_VERSION,
            "quality_metrics_contract_version": QUALITY_METRICS_CONTRACT_VERSION,
            "name": self.config.name,
            "task_set_hash": self.config.task_set.content_hash(),
            "task_set_name": self.config.task_set.name,
            "blind_eval_owner": self.config.task_set.blind_eval_owner,
            "thresholds": {
                "min_success_rate_lift": thr.min_success_rate_lift,
                "non_inferiority_margin": thr.non_inferiority_margin,
                "max_mean_cost_degradation": thr.max_mean_cost_degradation,
                "max_p95_latency_degradation": thr.max_p95_latency_degradation,
                "min_sample_size_per_arm": thr.min_sample_size_per_arm,
                "max_repair_rounds_mean": thr.max_repair_rounds_mean,
                "max_human_intervention_rate": thr.max_human_intervention_rate,
            },
            "model_freeze": self.config.model_freeze,
            "tool_freeze": self.config.tool_freeze,
            "budget_units": self.config.budget_units,
            "confidence_level": self.config.confidence_level,
            "shadow_isolation": self.config.shadow_isolation,
            "canary_allowed": self.config.canary_allowed,
            "variants": list(GQ_VARIANTS),
            "not_p24_org_dimensions": True,
            "summaries": summaries,
            "decision": decision,
            "rationale": rationale,
            "guardrail_trips": guardrail_trips,
            "actor": actor,
            "created_at": datetime.now(UTC).isoformat(),
            "experiment_id": str(uuid.uuid4()),
        }
        record["content_hash"] = canonical_hash(
            {
                k: v
                for k, v in record.items()
                if k not in {"content_hash", "created_at", "experiment_id"}
            }
        )
        return record


def gq4_default_switch_gate(
    experiment_report: dict[str, Any],
    *,
    kill_switch: bool,
) -> dict[str, Any]:
    """GQ-4 hook: only PROMOTE_AGENTIC_CANDIDATE + no kill switch may switch default."""
    allowed = (
        not kill_switch
        and experiment_report.get("decision") == "PROMOTE_AGENTIC_CANDIDATE"
    )
    return {
        "activation_allowed": allowed,
        "proposed_default": "agentic" if allowed else "artifact-backed",
        "reason": (
            "ok"
            if allowed
            else (
                "kill_switch"
                if kill_switch
                else f"decision={experiment_report.get('decision')}"
            )
        ),
        "in_flight_run_semantics": (
            "new Runs use fallback; in-flight complete frozen plan or cancel"
        ),
    }
