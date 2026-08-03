"""H3 prebury: Hive / ops goal_kind opt-in gates (not coding default)."""

from __future__ import annotations

from typing import Any

# Explicit product kinds that may enable Hive orchestration later.
OPS_GOAL_KINDS = frozenset({"ops", "scenic", "city", "operations", "hive"})


def get_goal_kind(metadata: dict[str, Any] | None) -> str:
    meta = dict(metadata or {})
    raw = meta.get("goal_kind") or meta.get("product_domain") or "coding"
    return str(raw).strip().lower() or "coding"


def hive_opt_in_allowed(metadata: dict[str, Any] | None) -> bool:
    """True only when goal explicitly opts into ops Hive (H3 gate)."""
    meta = dict(metadata or {})
    if meta.get("hive_enabled") is True:
        return True
    if meta.get("enable_hive") is True:
        return True
    return get_goal_kind(meta) in OPS_GOAL_KINDS


def coding_default_is_primary(metadata: dict[str, Any] | None) -> bool:
    """Coding path must remain Primary Agent unless opt-in."""
    return not hive_opt_in_allowed(metadata)
