"""Generation-strategy policy (GQ contract + ProjectAgentSession product path).

M3 product rule (DecisionNote project-agent-session):
- Product execution path is always ``agentic`` (AgentRunner + Session).
- ``artifact-backed`` is SCAFFOLD / kill-switch fallback only — never a peer champion.
- AB ↔ agentic peer canary is deprecated and ignored for selection.

Qualification state remains an ops signal for rollout reporting; it no longer
demotes the product path to a one-shot generator.
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

# Historical ladder labels (ops reporting). Traffic eligibility no longer gates
# whether AgentRunner is the product path — see resolve_effective_generation_strategy.
QUALIFICATION_TRAFFIC_ELIGIBLE: Final[frozenset[str]] = frozenset(
    {
        "INTERNAL_DOGFOOD",
        "CANARY_5",
        "CANARY_25",
        "CANARY_50",
        "DEFAULT",
    }
)

QUALIFICATION_EXPLICIT_AGENTIC_ELIGIBLE: Final[frozenset[str]] = frozenset(
    {"OFFLINE_QUALIFICATION", *QUALIFICATION_TRAFFIC_ELIGIBLE}
)

ARTIFACT_BACKED_ROLE: Final[dict[str, Any]] = {
    "role": "SCAFFOLD_OR_KILL_SWITCH_FALLBACK",
    "eligible_as_champion": False,
    "verified_delivery_claim": False,
    "peer_canary_with_agentic": False,
    "allowed_uses": (
        "scaffold_project_tool",
        "kill_switch_fallback",
        "explicit_ops_bootstrap",
    ),
}


def qualification_allows_agentic_traffic(state: str | None) -> bool:
    return str(state or "DISABLED") in QUALIFICATION_TRAFFIC_ELIGIBLE


def qualification_allows_explicit_agentic(state: str | None) -> bool:
    """Deprecated gate: product path is agentic regardless of qualification."""
    return str(state or "DISABLED") in QUALIFICATION_EXPLICIT_AGENTIC_ELIGIBLE


def stable_canary_bucket(key: str, *, buckets: int = 100) -> int:
    """Stable 0..buckets-1 assignment (retained for future capability canaries)."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % buckets


def resolve_effective_generation_strategy(
    settings: Any,
    *,
    goal_id: str | None = None,
    gq2_closed: bool | None = None,
    live_active: bool | None = None,
) -> GenerationStrategy:
    """Resolve runtime strategy.

    Order (M3):
    1. Kill switch → artifact-backed fallback (scaffold / safety).
    2. Explicit ``generation_strategy=artifact-backed`` → scaffold-only opt-in.
    3. Otherwise → agentic (product Agent runtime). Peer AB↔agentic canary ignored.
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
    configured = getattr(settings, "generation_strategy", "agentic")
    if canary_variant not in {"artifact-backed", "agentic"}:
        canary_variant = "agentic"
    if gq2_closed is None:
        gq2_closed = bool(getattr(settings, "generation_strategy_canary_gate", False))

    reason = "product_agent_runtime"
    selected: GenerationStrategy
    bucket: int | None = None

    if kill_switch:
        reason = "kill_switch"
        selected = (
            fallback if fallback in {"artifact-backed", "agentic"} else "artifact-backed"
        )
        # Kill-switch fallback must stay non-agentic for safety rollback.
        if selected == "agentic":
            selected = "artifact-backed"
            reason = "kill_switch_forced_scaffold"
    elif configured == "artifact-backed":
        reason = "explicit_scaffold"
        selected = "artifact-backed"
    else:
        selected = "agentic"
        reason = "product_agent_runtime"
        if canary_percent > 0:
            # Deprecated: treating artifact-backed as a peer canary arm.
            bucket = stable_canary_bucket(str(goal_id)) if goal_id else None
            reason = "product_agent_runtime_ab_peer_canary_deprecated"
            logger.warning(
                "AB↔agentic peer canary is deprecated; product path is agentic",
                extra={
                    "goal_id": goal_id,
                    "canary_percent": canary_percent,
                    "canary_variant": canary_variant,
                    "qualification_state": qual_state,
                    "live_active": live_active,
                    "gq2_closed": bool(gq2_closed),
                },
            )

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
            "artifact_backed_role": ARTIFACT_BACKED_ROLE["role"],
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
        "fallback_role": ARTIFACT_BACKED_ROLE["role"],
    }


def canary_rollout_allowed(*, kill_switch: bool, gq2_closed: bool) -> bool:
    """Legacy ops door retained for tooling; does not select AB as product path."""
    return (not kill_switch) and bool(gq2_closed)


def peer_ab_agentic_canary_deprecated() -> dict[str, Any]:
    """Contract note: do not A/B artifact-backed vs agentic as equal strategies."""
    return {
        "version": "m3-agent-runtime/v1",
        "deprecated": True,
        "message": (
            "Comparing artifact-backed vs agentic as peer generation strategies is a "
            "type error. Product path is Persistent Agent Session + AgentRunner; "
            "artifact-backed is scaffold/fallback only. Future experiments compare "
            "Agent capability configs (tools/memory/model), not presence of Agent."
        ),
        "artifact_backed_role": ARTIFACT_BACKED_ROLE,
    }
