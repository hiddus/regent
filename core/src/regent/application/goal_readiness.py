"""Shared readiness semantics for unresolved GoalSpec questions."""
from __future__ import annotations

from typing import Any


def is_blocking_unknown(item: Any) -> bool:
    """Legacy strings block; structured questions may explicitly be advisory."""
    if isinstance(item, dict):
        return item.get("blocking") is not False
    return bool(str(item or "").strip())


def blocking_unknowns(items: list[Any] | None) -> list[Any]:
    return [item for item in list(items or []) if is_blocking_unknown(item)]


def effective_feasibility_verdict(
    verdict: str | None, *, rounds: int, unknowns: list[Any] | None
) -> str:
    normalized = str(verdict or "REVISION_REQUIRED").upper()
    if (
        normalized == "REVISION_REQUIRED"
        and rounds >= 2
        and not blocking_unknowns(unknowns)
    ):
        return "FEASIBLE"
    return normalized
