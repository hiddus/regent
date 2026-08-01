"""Generation-strategy canary / kill-switch policy (GQ-0 contract, GQ-3/GQ-4 hooks).

Independent of P2-4 organization A/B/C dimensions. Default remains
artifact-backed until a GQ-4 DecisionRecord promotes agentic.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from regent.application.generator_metadata import GenerationStrategy

logger = logging.getLogger(__name__)

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
    live_active: bool | None = None,
) -> GenerationStrategy:
    """Resolve runtime strategy with kill switch and optional canary.

    Order:
    1. Kill switch → fallback (never agentic while switch is on).
    2. Canary requires BOTH ``canary_percent > 0`` AND the GQ-2 feedback loop
       closed (``generation_strategy_canary_gate``), enforced via
       ``canary_rollout_allowed``. This is the diagnosis order: GQ-2 before GQ-3.
       When active and ``goal_id`` is present, a stable bucket may select the
       canary variant. Pass ``live_active=False`` to skip canary on zombie goals.
    3. Otherwise settings.generation_strategy.
    """
    fallback: GenerationStrategy = getattr(
        settings, "generation_strategy_fallback", "artifact-backed"
    )
    kill_switch: bool = bool(getattr(settings, "generation_strategy_kill_switch", False))
    canary_percent = int(getattr(settings, "generation_strategy_canary_percent", 0) or 0)
    canary_variant: GenerationStrategy = getattr(
        settings, "generation_strategy_canary_variant", "agentic"
    )
    if canary_variant not in {"artifact-backed", "agentic"}:
        canary_variant = "agentic"
    if gq2_closed is None:
        gq2_closed = bool(getattr(settings, "generation_strategy_canary_gate", False))

    reason = "default"
    selected: GenerationStrategy
    bucket: int | None = None

    if kill_switch:
        reason = "kill_switch"
        selected = fallback
    elif live_active is False:
        # Zombie / no-progress goals must not enter canary sample (P1 discipline).
        reason = "canary_skipped_not_live_active"
        strategy = getattr(settings, "generation_strategy", "artifact-backed")
        selected = (
            strategy if strategy in {"artifact-backed", "agentic"} else "artifact-backed"
        )
    elif (
        canary_percent > 0
        and goal_id
        and canary_rollout_allowed(kill_switch=kill_switch, gq2_closed=gq2_closed)
    ):
        bucket = stable_canary_bucket(str(goal_id))
        if bucket < canary_percent:
            reason = "canary_hit"
            selected = canary_variant
        else:
            reason = "canary_miss"
            strategy = getattr(settings, "generation_strategy", "artifact-backed")
            selected = (
                strategy if strategy in {"artifact-backed", "agentic"} else "artifact-backed"
            )
    else:
        strategy = getattr(settings, "generation_strategy", "artifact-backed")
        if strategy not in {"artifact-backed", "agentic"}:
            selected = "artifact-backed"
        else:
            selected = strategy  # type: ignore[assignment]
        if canary_percent > 0 and not goal_id:
            reason = "canary_skipped_no_goal_id"
        elif canary_percent > 0 and not gq2_closed:
            reason = "canary_gate_closed"
        else:
            reason = "default"

    logger.info(
        "generation_strategy_resolved",
        extra={
            "event": "generation_strategy_resolved",
            "goal_id": goal_id,
            "bucket": bucket,
            "canary_percent": canary_percent,
            "gate": bool(gq2_closed),
            "kill_switch": kill_switch,
            "selected": selected,
            "reason": reason,
        },
    )
    return selected


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
    return (not kill_switch) and bool(gq2_closed)
