"""Multi-agent / Hive policy: multi-agent is the product default.

Single-agent (Primary-only) requires an explicit opt-out on the goal.
"""

from __future__ import annotations

from typing import Any

# Product kinds that historically mapped to ops Hive UX labels.
OPS_GOAL_KINDS = frozenset({"ops", "scenic", "city", "operations", "hive"})


def get_goal_kind(metadata: dict[str, Any] | None) -> str:
    meta = dict(metadata or {})
    raw = meta.get("goal_kind") or meta.get("product_domain") or "coding"
    return str(raw).strip().lower() or "coding"


def force_single_agent(metadata: dict[str, Any] | None) -> bool:
    """True only when the goal explicitly opts out of multi-agent / Hive."""
    meta = dict(metadata or {})
    if meta.get("force_single_agent") is True:
        return True
    if meta.get("single_agent_only") is True:
        return True
    if meta.get("hive_enabled") is False:
        return True
    if meta.get("enable_hive") is False:
        return True
    return False


def hive_opt_in_allowed(metadata: dict[str, Any] | None) -> bool:
    """True when multi-agent / Hive orchestration is allowed (default on)."""
    return not force_single_agent(metadata)


def coding_default_is_primary(metadata: dict[str, Any] | None) -> bool:
    """True only when explicitly forced to a single Primary Agent."""
    return force_single_agent(metadata)
