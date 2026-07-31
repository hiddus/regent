"""Generation-strategy canary / kill-switch policy (GQ-0 contract, GQ-3/GQ-4 hooks).

Independent of P2-4 organization A/B/C dimensions. Default remains
artifact-backed until a GQ-4 DecisionRecord promotes agentic.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from regent.application.generator_metadata import GenerationStrategy

IN_FLIGHT_RUN_SEMANTICS = (
    "On kill-switch or rollback: new Runs use the fallback strategy; "
    "in-flight Runs complete under the already-frozen GenerationPlan "
    "or are explicitly cancelled. Mid-run generator swaps without evidence "
    "are forbidden."
)


def stable_canary_bucket(key: str, *, buckets: int = 100) -> int:
    """Stable 0..buckets-1 assignment for canary traffic splitting."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % buckets


def resolve_effective_generation_strategy(
    settings: Any,
    *,
    goal_id: str | None = None,
    gq2_closed: bool | None = None,
) -> GenerationStrategy:
    """Resolve runtime strategy with kill switch and optional canary.

    Order:
    1. Kill switch → fallback (never agentic while switch is on).
    2. Canary requires BOTH ``canary_percent > 0`` AND the GQ-2 feedback loop
       closed (``generation_strategy_canary_gate``), enforced via
       ``canary_rollout_allowed``. This is the diagnosis order: GQ-2 before GQ-3.
       When active and ``goal_id`` is present, a stable bucket may select the
       canary variant.
    3. Otherwise settings.generation_strategy.
    """
    fallback: GenerationStrategy = getattr(
        settings, "generation_strategy_fallback", "artifact-backed"
    )
    kill_switch: bool = bool(getattr(settings, "generation_strategy_kill_switch", False))
    if kill_switch:
        return fallback

    canary_percent = int(getattr(settings, "generation_strategy_canary_percent", 0) or 0)
    canary_variant: GenerationStrategy = getattr(
        settings, "generation_strategy_canary_variant", "agentic"
    )
    if canary_variant not in {"artifact-backed", "agentic"}:
        canary_variant = "agentic"
    if gq2_closed is None:
        # Operator sets this True only after GQ-2 feedback loop is verified.
        gq2_closed = bool(getattr(settings, "generation_strategy_canary_gate", False))
    if (
        canary_percent > 0
        and goal_id
        and canary_rollout_allowed(kill_switch=kill_switch, gq2_closed=gq2_closed)
        and stable_canary_bucket(str(goal_id)) < canary_percent
    ):
        return canary_variant

    strategy = getattr(settings, "generation_strategy", "artifact-backed")
    if strategy not in {"artifact-backed", "agentic"}:
        return "artifact-backed"
    return strategy  # type: ignore[return-value]


def shadow_isolation_contract() -> dict[str, Any]:
    """GQ-0 frozen contract for shadow tasks (no publish / no external side effects)."""
    return {
        "version": "gq-shadow-isolation/v1",
        "require_independent_sandbox": True,
        "require_independent_artifact_namespace": True,
        "forbid_publish": True,
        "forbid_external_side_effects": True,
        "in_flight_run_semantics": IN_FLIGHT_RUN_SEMANTICS,
    }


def kill_switch_contract() -> dict[str, Any]:
    return {
        "version": "gq-kill-switch/v1",
        "config_keys": [
            "REGENT_GENERATION_STRATEGY_KILL_SWITCH",
            "REGENT_GENERATION_STRATEGY_FALLBACK",
        ],
        "in_flight_run_semantics": IN_FLIGHT_RUN_SEMANTICS,
        "forbid_mid_run_generator_swap": True,
    }


def canary_rollout_allowed(*, kill_switch: bool, gq2_closed: bool) -> bool:
    """Diagnosis order: feedback loop (GQ-2) before canary (GQ-3)."""
    if kill_switch:
        return False
    return gq2_closed
