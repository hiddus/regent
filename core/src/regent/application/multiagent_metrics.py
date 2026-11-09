"""P2-4 multi-agent metric calculation contracts (PRD §10.1 / Spec §18.3).

Frozen formulas:
- coordination_token_share = coordination_message_tokens / total_tokens
- error_amplification_factor = affected_downstream / injected_errors (fault-injection only)
- dispatch_entropy = H(candidate weight distribution) per dispatch step

Missing required fields → status INSUFFICIENT_EVIDENCE (never fill with 0).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

METRICS_CONTRACT_VERSION = "multiagent-metrics-v1"
COORDINATION_SHARE_ALERT_THRESHOLD = 0.20
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
OK = "OK"

TokenKind = Literal[
    "coordination",
    "agent_execution",
    "orchestrator",
    "evaluator",
    "cache",
]


@dataclass(frozen=True, slots=True)
class TokenBucket:
    """Versioned token accounting for one Run/Goal window."""

    coordination_message_tokens: int | None = None
    agent_execution_tokens: int | None = None
    orchestrator_tokens: int | None = None
    evaluator_tokens: int | None = None
    cache_tokens: int | None = None  # reported separately; not in share denominator

    def as_dict(self) -> dict[str, int | None]:
        return {
            "coordination_message_tokens": self.coordination_message_tokens,
            "agent_execution_tokens": self.agent_execution_tokens,
            "orchestrator_tokens": self.orchestrator_tokens,
            "evaluator_tokens": self.evaluator_tokens,
            "cache_tokens": self.cache_tokens,
        }


@dataclass(frozen=True, slots=True)
class MetricResult:
    name: str
    value: float | None
    status: str
    contract_version: str = METRICS_CONTRACT_VERSION
    numerators: dict[str, float | int | None] = field(default_factory=dict)
    denominators: dict[str, float | int | None] = field(default_factory=dict)
    detail: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "status": self.status,
            "contract_version": self.contract_version,
            "numerators": self.numerators,
            "denominators": self.denominators,
            "detail": self.detail,
            "extras": self.extras,
        }


def classify_token_kind(
    *,
    message_type: str | None = None,
    is_peer_to_peer: bool = False,
    is_orchestrator: bool = False,
    is_evaluator: bool = False,
    is_cache_hit: bool = False,
) -> TokenKind:
    """Classify a token spend for coordination-share accounting."""
    if is_cache_hit:
        return "cache"
    if is_evaluator or (message_type or "").upper() in {"EVAL", "EVALUATOR", "JUDGE"}:
        return "evaluator"
    if is_orchestrator or (message_type or "").upper() in {
        "ORCHESTRATOR",
        "DISPATCH",
        "SCHEDULING",
    }:
        return "orchestrator"
    if is_peer_to_peer or (message_type or "").upper() in {
        "COORDINATION",
        "HANDOFF",
        "PEER",
        "DELEGATION",
    }:
        return "coordination"
    return "agent_execution"


def accumulate_token_bucket(
    events: Sequence[Mapping[str, Any]],
) -> TokenBucket:
    """Rebuild TokenBucket from raw trajectory events.

    Each event may include: tokens (int), kind (TokenKind), or
    message_type / is_peer_to_peer / is_orchestrator / is_evaluator / is_cache_hit.
    """
    buckets: dict[TokenKind, int] = {
        "coordination": 0,
        "agent_execution": 0,
        "orchestrator": 0,
        "evaluator": 0,
        "cache": 0,
    }
    seen = False
    for event in events:
        tokens = event.get("tokens")
        if tokens is None:
            continue
        seen = True
        kind = event.get("kind")
        if kind not in buckets:
            kind = classify_token_kind(
                message_type=event.get("message_type"),
                is_peer_to_peer=bool(event.get("is_peer_to_peer")),
                is_orchestrator=bool(event.get("is_orchestrator")),
                is_evaluator=bool(event.get("is_evaluator")),
                is_cache_hit=bool(event.get("is_cache_hit")),
            )
        buckets[kind] = buckets[kind] + max(0, int(tokens))  # type: ignore[index]
    if not seen:
        return TokenBucket()
    return TokenBucket(
        coordination_message_tokens=buckets["coordination"],
        agent_execution_tokens=buckets["agent_execution"],
        orchestrator_tokens=buckets["orchestrator"],
        evaluator_tokens=buckets["evaluator"],
        cache_tokens=buckets["cache"],
    )


def compute_coordination_token_share(bucket: TokenBucket) -> MetricResult:
    """coordination_token_share = coordination / (coord + agent + orch + eval).

    Cache tokens are reported separately and excluded from the denominator.
    """
    name = "coordination_token_share"
    required = (
        bucket.coordination_message_tokens,
        bucket.agent_execution_tokens,
        bucket.orchestrator_tokens,
        bucket.evaluator_tokens,
    )
    if any(v is None for v in required):
        return MetricResult(
            name=name,
            value=None,
            status=INSUFFICIENT,
            numerators={"coordination_message_tokens": bucket.coordination_message_tokens},
            denominators={
                "agent_execution_tokens": bucket.agent_execution_tokens,
                "orchestrator_tokens": bucket.orchestrator_tokens,
                "evaluator_tokens": bucket.evaluator_tokens,
            },
            detail="missing required token fields; refuse zero-fill",
            extras={"cache_tokens": bucket.cache_tokens, "bucket": bucket.as_dict()},
        )
    coordination = int(bucket.coordination_message_tokens or 0)
    total = (
        coordination
        + int(bucket.agent_execution_tokens or 0)
        + int(bucket.orchestrator_tokens or 0)
        + int(bucket.evaluator_tokens or 0)
    )
    if total <= 0:
        return MetricResult(
            name=name,
            value=None,
            status=INSUFFICIENT,
            numerators={"coordination_message_tokens": coordination},
            denominators={"total_tokens": total},
            detail="total_tokens must be > 0",
            extras={"cache_tokens": bucket.cache_tokens},
        )
    share = coordination / total
    alert = share >= COORDINATION_SHARE_ALERT_THRESHOLD
    return MetricResult(
        name=name,
        value=round(share, 6),
        status=OK,
        numerators={"coordination_message_tokens": coordination},
        denominators={"total_tokens": total},
        detail=(
            f"alert_line={COORDINATION_SHARE_ALERT_THRESHOLD}"
            + ("; ENGINEERING_ALERT" if alert else "")
        ),
        extras={
            "cache_tokens": bucket.cache_tokens,
            "engineering_alert": alert,
            "alert_threshold": COORDINATION_SHARE_ALERT_THRESHOLD,
        },
    )


@dataclass(frozen=True, slots=True)
class FaultInjectionTrace:
    """Versioned fault-injection evidence for error amplification."""

    injection_task_version: str
    injection_points: Sequence[str]
    expected_impact_boundary: Sequence[str]
    actual_affected_nodes: Sequence[str]
    injected_error_count: int | None
    independent_eval_evidence_refs: Sequence[str] = ()


def compute_error_amplification_factor(trace: FaultInjectionTrace) -> MetricResult:
    """error_amplification_factor only on versioned fault-injection tasks."""
    name = "error_amplification_factor"
    if not trace.injection_task_version:
        return MetricResult(
            name=name,
            value=None,
            status=INSUFFICIENT,
            detail="injection_task_version required",
        )
    if trace.injected_error_count is None:
        return MetricResult(
            name=name,
            value=None,
            status=INSUFFICIENT,
            numerators={"actual_affected_nodes": len(trace.actual_affected_nodes)},
            denominators={"injected_error_count": None},
            detail="injected_error_count missing",
            extras={
                "injection_task_version": trace.injection_task_version,
                "injection_points": list(trace.injection_points),
                "expected_impact_boundary": list(trace.expected_impact_boundary),
                "actual_affected_nodes": list(trace.actual_affected_nodes),
                "evidence_refs": list(trace.independent_eval_evidence_refs),
            },
        )
    if trace.injected_error_count <= 0:
        return MetricResult(
            name=name,
            value=None,
            status=INSUFFICIENT,
            denominators={"injected_error_count": trace.injected_error_count},
            detail="injected_error_count must be > 0",
        )
    if not trace.independent_eval_evidence_refs:
        return MetricResult(
            name=name,
            value=None,
            status=INSUFFICIENT,
            detail="independent_eval_evidence_refs required",
            extras={"injection_task_version": trace.injection_task_version},
        )
    affected = len(trace.actual_affected_nodes)
    factor = affected / float(trace.injected_error_count)
    return MetricResult(
        name=name,
        value=round(factor, 6),
        status=OK,
        numerators={"actual_affected_node_count": affected},
        denominators={"injected_error_count": trace.injected_error_count},
        extras={
            "injection_task_version": trace.injection_task_version,
            "injection_points": list(trace.injection_points),
            "expected_impact_boundary": list(trace.expected_impact_boundary),
            "actual_affected_nodes": list(trace.actual_affected_nodes),
            "evidence_refs": list(trace.independent_eval_evidence_refs),
        },
    )


def shannon_entropy(weights: Sequence[float]) -> float | None:
    """Shannon entropy (bits) of a non-negative weight distribution."""
    cleaned = [max(0.0, float(w)) for w in weights]
    total = sum(cleaned)
    if total <= 0:
        return None
    entropy = 0.0
    for w in cleaned:
        if w <= 0:
            continue
        p = w / total
        entropy -= p * math.log2(p)
    return entropy


@dataclass(frozen=True, slots=True)
class DispatchEntropyStep:
    step_id: str
    candidate_weights: Mapping[str, float]
    entropy: float | None = None


def compute_step_entropy(weights: Mapping[str, float]) -> float | None:
    return shannon_entropy(list(weights.values()))


def compute_dispatch_entropy(
    steps: Sequence[DispatchEntropyStep | Mapping[str, Any]],
    *,
    terminal_window: int = 3,
) -> MetricResult:
    """Aggregate dispatch entropy series: mean, slope, peak, terminal window."""
    name = "dispatch_entropy"
    if not steps:
        return MetricResult(
            name=name,
            value=None,
            status=INSUFFICIENT,
            detail="no dispatch steps",
        )

    series: list[dict[str, Any]] = []
    values: list[float] = []
    for raw in steps:
        if isinstance(raw, DispatchEntropyStep):
            step_id = raw.step_id
            weights = dict(raw.candidate_weights)
            ent = raw.entropy if raw.entropy is not None else compute_step_entropy(weights)
        else:
            step_id = str(raw.get("step_id") or "")
            weights = dict(raw.get("candidate_weights") or {})
            if "entropy" in raw and raw["entropy"] is not None:
                ent = float(raw["entropy"])
            else:
                ent = compute_step_entropy(weights)
        if not step_id or not weights or ent is None:
            return MetricResult(
                name=name,
                value=None,
                status=INSUFFICIENT,
                detail="each step requires step_id, candidate_weights, and computable entropy",
                extras={"partial_series": series},
            )
        series.append(
            {
                "step_id": step_id,
                "entropy": round(ent, 6),
                "candidate_ids": sorted(weights.keys()),
                "weights": {k: float(v) for k, v in weights.items()},
            }
        )
        values.append(ent)

    mean = sum(values) / len(values)
    peak = max(values)
    slope = _linear_slope(values)
    window = values[-terminal_window:] if terminal_window > 0 else values
    terminal_mean = sum(window) / len(window)
    diverging = slope is not None and slope > 0.05 and terminal_mean > mean
    return MetricResult(
        name=name,
        value=round(mean, 6),
        status=OK,
        numerators={"step_count": len(values)},
        denominators={},
        detail="diverging" if diverging else "stable_or_converging",
        extras={
            "mean": round(mean, 6),
            "slope": None if slope is None else round(slope, 6),
            "peak": round(peak, 6),
            "terminal_window": terminal_window,
            "terminal_mean": round(terminal_mean, 6),
            "diverging_alert": diverging,
            "series": series,
        },
    )


def _linear_slope(values: Sequence[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    xs = list(range(n))
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values))
    den = sum((x - x_mean) ** 2 for x in xs)
    if den == 0:
        return None
    return num / den


def compute_all_metrics(
    *,
    token_bucket: TokenBucket | None = None,
    token_events: Sequence[Mapping[str, Any]] | None = None,
    fault_trace: FaultInjectionTrace | None = None,
    dispatch_steps: Sequence[DispatchEntropyStep | Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute the three frozen metrics; each may independently be INSUFFICIENT."""
    bucket = token_bucket or (
        accumulate_token_bucket(token_events or []) if token_events is not None else TokenBucket()
    )
    share = compute_coordination_token_share(bucket)
    amp = (
        compute_error_amplification_factor(fault_trace)
        if fault_trace is not None
        else MetricResult(
            name="error_amplification_factor",
            value=None,
            status=INSUFFICIENT,
            detail="no fault-injection trace provided",
        )
    )
    entropy = (
        compute_dispatch_entropy(dispatch_steps)
        if dispatch_steps is not None
        else MetricResult(
            name="dispatch_entropy",
            value=None,
            status=INSUFFICIENT,
            detail="no dispatch steps provided",
        )
    )
    return {
        "contract_version": METRICS_CONTRACT_VERSION,
        "metrics": {
            share.name: share.as_dict(),
            amp.name: amp.as_dict(),
            entropy.name: entropy.as_dict(),
        },
        "recomputable_from": {
            "token_bucket": bucket.as_dict(),
            "fault_trace": None
            if fault_trace is None
            else {
                "injection_task_version": fault_trace.injection_task_version,
                "injection_points": list(fault_trace.injection_points),
                "expected_impact_boundary": list(fault_trace.expected_impact_boundary),
                "actual_affected_nodes": list(fault_trace.actual_affected_nodes),
                "injected_error_count": fault_trace.injected_error_count,
                "independent_eval_evidence_refs": list(
                    fault_trace.independent_eval_evidence_refs
                ),
            },
            "dispatch_steps": [
                s if isinstance(s, dict) else {
                    "step_id": s.step_id,
                    "candidate_weights": dict(s.candidate_weights),
                    "entropy": s.entropy,
                }
                for s in (dispatch_steps or [])
            ],
        },
    }
