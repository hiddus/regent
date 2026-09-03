"""P2-4 frozen A/B/C experiment scaffolding (MA-5).

Compares strong single-agent champion vs certified fixed hive under identical
budget / blind eval / confidence intervals. Does NOT enable adaptive topology.
Produces a DecisionRecord-shaped report; adaptive remains ROLLOUT_NOT_ALLOWED
unless an explicit GO DecisionRecord exists (checked by p25_adaptive_gate).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Sequence

from regent.application.aar1_contract import (
    CERTIFIED_HIVE_TEMPLATE_ID,
    SINGLE_AGENT_TEMPLATE_ID,
)
from regent.application.multiagent_metrics import (
    METRICS_CONTRACT_VERSION,
    TokenBucket,
    compute_all_metrics,
)
from regent.application.statistics import wilson_interval as _wilson_interval
from regent.application.p1_contracts import canonical_hash

P24_EXPERIMENT_VERSION = "p24-frozen-abc/v1"
ORG_ADAPTIVE_STATUS = "ROLLOUT_NOT_ALLOWED"


@dataclass(frozen=True, slots=True)
class VariantRunResult:
    variant: str  # A=single, B=certified_hive, C=optional control
    task_id: str
    passed: bool
    cost_units: float
    coordination_tokens: int | None = None
    total_tokens: int | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    blind_score: float | None = None


@dataclass(frozen=True, slots=True)
class FrozenExperimentConfig:
    name: str
    task_set_hash: str
    model_freeze: str
    tool_freeze: str
    budget_units: float
    repeats: int = 3
    confidence_level: float = 0.95
    variants: tuple[str, ...] = ("A_single_agent", "B_certified_hive", "C_control")


def summarize_variant(results: Sequence[VariantRunResult]) -> dict[str, Any]:
    n = len(results)
    if n == 0:
        return {
            "n": 0,
            "pass_rate": None,
            "ci95": None,
            "mean_cost": None,
            "status": "INSUFFICIENT_EVIDENCE",
        }
    successes = sum(1 for r in results if r.passed)
    costs = [r.cost_units for r in results]
    ci = _wilson_interval(successes, n)
    return {
        "n": n,
        "pass_rate": successes / n,
        "ci95": None if ci is None else {"low": round(ci[0], 4), "high": round(ci[1], 4)},
        "mean_cost": sum(costs) / n,
        "cost_per_verified_success": (
            (sum(costs) / successes) if successes else None
        ),
        "status": "OK",
    }


class P24FrozenExperiment:
    """In-process A/B/C harness that emits a signed-ready DecisionRecord payload."""

    def __init__(self, config: FrozenExperimentConfig) -> None:
        self.config = config
        self._results: list[VariantRunResult] = []

    def record(self, result: VariantRunResult) -> None:
        self._results.append(result)

    def report(self, *, actor: str = "regent-core") -> dict[str, Any]:
        by_variant: dict[str, list[VariantRunResult]] = {v: [] for v in self.config.variants}
        for row in self._results:
            by_variant.setdefault(row.variant, []).append(row)

        summaries = {k: summarize_variant(v) for k, v in by_variant.items()}
        a = summaries.get("A_single_agent") or summarize_variant([])
        b = summaries.get("B_certified_hive") or summarize_variant([])

        decision = "INSUFFICIENT_EVIDENCE"
        rationale = "missing variant samples"
        if a.get("status") == "OK" and b.get("status") == "OK":
            a_rate = float(a["pass_rate"])
            b_rate = float(b["pass_rate"])
            a_cost = a.get("cost_per_verified_success")
            b_cost = b.get("cost_per_verified_success")
            # Positive net: higher or equal pass with lower cost, or clearly higher pass.
            positive = False
            if b_rate > a_rate + 0.05:
                positive = True
            elif abs(b_rate - a_rate) <= 0.05 and a_cost and b_cost and b_cost < a_cost * 0.95:
                positive = True
            if positive:
                decision = "KEEP_SINGLE_AGENT_PENDING_REVIEW"
                rationale = (
                    "fixed hive shows candidate lift but adaptive topology remains "
                    "ROLLOUT_NOT_ALLOWED; requires product DecisionRecord GO"
                )
            else:
                decision = "KEEP_SINGLE_AGENT"
                rationale = "no positive net benefit vs strong single-agent champion"

        # Aggregate frozen metrics from variant extras when present.
        metric_bundle = compute_all_metrics(
            token_bucket=TokenBucket(
                coordination_message_tokens=_sum_optional(
                    r.coordination_tokens for r in self._results
                ),
                agent_execution_tokens=_sum_optional(
                    (r.total_tokens - (r.coordination_tokens or 0))
                    if r.total_tokens is not None
                    else None
                    for r in self._results
                ),
                orchestrator_tokens=0,
                evaluator_tokens=0,
                cache_tokens=0,
            )
            if any(r.total_tokens is not None for r in self._results)
            else None
        )

        record = {
            "version": P24_EXPERIMENT_VERSION,
            "metrics_contract_version": METRICS_CONTRACT_VERSION,
            "name": self.config.name,
            "task_set_hash": self.config.task_set_hash,
            "model_freeze": self.config.model_freeze,
            "tool_freeze": self.config.tool_freeze,
            "budget_units": self.config.budget_units,
            "repeats": self.config.repeats,
            "confidence_level": self.config.confidence_level,
            "blind_eval": True,
            "templates": {
                "A": SINGLE_AGENT_TEMPLATE_ID,
                "B": CERTIFIED_HIVE_TEMPLATE_ID,
                "C": "control",
            },
            "summaries": summaries,
            "metrics": metric_bundle,
            "decision": decision,
            "rationale": rationale,
            "org_adaptive_status": ORG_ADAPTIVE_STATUS,
            "actor": actor,
            "created_at": datetime.now(UTC).isoformat(),
            "experiment_id": str(uuid.uuid4()),
        }
        record["content_hash"] = canonical_hash(
            {k: v for k, v in record.items() if k not in {"content_hash", "created_at", "experiment_id"}}
        )
        return record


def _sum_optional(values: Any) -> int | None:
    total = 0
    seen = False
    for v in values:
        if v is None:
            continue
        seen = True
        total += int(v)
    return total if seen else None
