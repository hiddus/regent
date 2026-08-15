"""Shared readiness semantics for unresolved GoalSpec questions."""
from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class GoalReadiness:
    phase: str
    verdict: str
    rounds: int
    blocking_unknowns: tuple[Any, ...]
    advisory_unknowns: tuple[Any, ...]

    @property
    def ready(self) -> bool:
        return self.phase == "DRAFT_CONFIRMABLE"


def assess_goal_readiness(
    *, verdict: str | None, rounds: int, unknowns: list[Any] | None
) -> GoalReadiness:
    all_unknowns = list(unknowns or [])
    blockers = blocking_unknowns(all_unknowns)
    advisory = [item for item in all_unknowns if item not in blockers]
    effective = effective_feasibility_verdict(
        verdict, rounds=rounds, unknowns=all_unknowns
    )
    phase = (
        "DRAFT_CONFIRMABLE"
        if effective == "FEASIBLE" and rounds >= 2 and not blockers
        else "DRAFT_CLARIFYING"
    )
    return GoalReadiness(
        phase=phase,
        verdict=effective,
        rounds=rounds,
        blocking_unknowns=tuple(blockers),
        advisory_unknowns=tuple(advisory),
    )


def confirmation_gate_key(goal_id: Any, spec_version: int) -> str:
    return f"goal:{goal_id}:spec:{spec_version}:confirm"
