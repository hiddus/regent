"""Multi-agent / Hive policy with explicit, evidence-bearing opt-in."""

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
    """True only when the goal explicitly requests collaborative execution."""
    meta = dict(metadata or {})
    if force_single_agent(meta):
        return False
    return meta.get("hive_enabled") is True or meta.get("enable_hive") is True


def coding_default_is_primary(metadata: dict[str, Any] | None) -> bool:
    """Single Agent is primary unless Hive was explicitly enabled."""
    return not hive_opt_in_allowed(metadata)
