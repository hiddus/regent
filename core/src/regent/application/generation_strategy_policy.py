"""Generation-strategy canary / kill-switch policy (GQ-0 contract, GQ-3/GQ-4 hooks).

Independent of P2-4 organization A/B/C dimensions. Default remains
artifact-backed (FALLBACK_ONLY) until the agentic qualification ladder
reaches DEFAULT via DecisionRecord. Canary traffic requires a
traffic-eligible qualification state — not merely a recovered control funnel.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Final

from regent.application.generator_metadata import GenerationStrategy

logger = logging.getLogger(__name__)

IN_FLIGHT_RUN_SEMANTICS = (
    "On kill-switch or rollback: new Runs use the fallback strategy; "
    "in-flight Runs complete under the already-frozen GenerationPlan "
    "or are explicitly cancelled. Mid-run generator swaps without evidence "
    "are forbidden."
)

# States that may assign agentic via production canary / dogfood rollout.
QUALIFICATION_TRAFFIC_ELIGIBLE: Final[frozenset[str]] = frozenset(
    {
        "INTERNAL_DOGFOOD",
        "CANARY_5",
        "CANARY_25",
        "CANARY_50",
        "DEFAULT",
    }
)

# Explicit generation_strategy=agentic (Offline Qual lane + traffic ladder).
QUALIFICATION_EXPLICIT_AGENTIC_ELIGIBLE: Final[frozenset[str]] = frozenset(
    {"OFFLINE_QUALIFICATION", *QUALIFICATION_TRAFFIC_ELIGIBLE}
)

ARTIFACT_BACKED_ROLE: Final[dict[str, Any]] = {
    "role": "FALLBACK_ONLY",
    "eligible_as_champion": False,
    "verified_delivery_claim": False,
}


def qualification_allows_agentic_traffic(state: str | None) -> bool:
    return str(state or "DISABLED") in QUALIFICATION_TRAFFIC_ELIGIBLE


def qualification_allows_explicit_agentic(state: str | None) -> bool:
    return str(state or "DISABLED") in QUALIFICATION_EXPLICIT_AGENTIC_ELIGIBLE


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
    """Resolve runtime strategy with kill switch, qualification, and optional canary.

    Order:
    1. Kill switch → fallback (never agentic while switch is on).
    2. Canary requires traffic-eligible qualification (DOGFOOD / CANARY_* / DEFAULT)
       plus ``canary_percent > 0`` and GQ-2 gate. Funnel health does not unlock
       traffic. Pass ``live_active=False`` to skip canary on zombie goals.
    3. Explicit ``generation_strategy=agentic`` only when qualification allows
       (Offline Qual or traffic-eligible). Otherwise FALLBACK_ONLY artifact-backed.
    """
    fallback: GenerationStrategy = getattr(
        settings, "generation_strategy_fallback", "artifact-backed"
    )
    kill_switch: bool = bool(getattr(settings, "generation_strategy_kill_switch", False))
    canary_percent = int(getattr(settings, "generation_strategy_canary_percent", 0) or 0)
    canary_variant: GenerationStrategy = getattr(
        settings, "generation_strategy_canary_variant", "agentic"
    )
    qual_state = str(getattr(settings, "agentic_qualification_state", "DISABLED") or "DISABLED")
    configured = getattr(settings, "generation_strategy", "artifact-backed")
    if canary_variant not in {"artifact-backed", "agentic"}:
        canary_variant = "agentic"
    if gq2_closed is None:
        gq2_closed = bool(getattr(settings, "generation_strategy_canary_gate", False))

    reason = "default"
    selected: GenerationStrategy
    bucket: int | None = None

    def _default_strategy() -> GenerationStrategy:
        strategy = configured
        if strategy not in {"artifact-backed", "agentic"}:
            return "artifact-backed"
        if strategy == "agentic" and not qualification_allows_explicit_agentic(qual_state):
            return "artifact-backed"
        return strategy  # type: ignore[return-value]

    if kill_switch:
        reason = "kill_switch"
        selected = fallback if fallback in {"artifact-backed", "agentic"} else "artifact-backed"
        if selected == "agentic" and not qualification_allows_explicit_agentic(qual_state):
            selected = "artifact-backed"
    elif live_active is False:
        reason = "canary_skipped_not_live_active"
        selected = _default_strategy()
        if configured == "agentic" and selected == "artifact-backed":
            reason = "qualification_not_eligible"
    elif (
        qualification_allows_agentic_traffic(qual_state)
        and canary_percent > 0
        and goal_id
        and canary_rollout_allowed(kill_switch=kill_switch, gq2_closed=gq2_closed)
    ):
        bucket = stable_canary_bucket(str(goal_id))
        if bucket < canary_percent:
            reason = "canary_hit"
            selected = canary_variant
        else:
            reason = "canary_miss"
            selected = _default_strategy()
    else:
        selected = _default_strategy()
        if configured == "agentic" and selected == "artifact-backed":
            reason = "qualification_not_eligible"
        elif canary_percent > 0 and not qualification_allows_agentic_traffic(qual_state):
            reason = "qualification_not_eligible"
        elif canary_percent > 0 and not goal_id:
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
            "qualification_state": qual_state,
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
    """Ops second door: kill switch off + canary gate on.

    Funnel health does not unlock traffic (self-lock protocol abolished).
    ``gq2_closed`` here is the ops ``generation_strategy_canary_gate`` flag —
    extra insurance after qualification_state already qualifies the lane.
    """
    return (not kill_switch) and bool(gq2_closed)
