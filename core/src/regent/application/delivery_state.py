"""Delivery state machine — explicit reification of the existing recovery flow.

This module does NOT introduce a new recovery mechanism. It makes the delivery
state transitions that already exist in ``DeliveryGapRecoveryService`` +
``ExecutionOrchestrator`` explicit and centralizes two things:

* the **no-dead-end** transition rule (CON-5 sibling, AC1), and
* the **persona → budget** coupling (AC5).

Iron rule (shared with CON-5): every terminal state has an explicit exit —
``DELIVERED`` / ``DELIVERED_FOR_REVIEW`` / ``ESCALATED`` all lead somewhere
(accept / review / escalate). There is no silent "failed → task ended" terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from regent.application.confirmation import DecisionPreference


class DeliveryState(StrEnum):
    GENERATING = "GENERATING"
    VERIFYING = "VERIFYING"
    DELIVERED = "DELIVERED"
    AUTO_RECOVERING = "AUTO_RECOVERING"  # budgeted re-plan + re-run
    DELIVERED_FOR_REVIEW = "DELIVERED_FOR_REVIEW"  # current best output handed to human
    ESCALATED = "ESCALATED"  # not self-recoverable -> escalation (still has an exit)


# Persona -> recovery budget multiplier (AC5). 1.0 == balanced baseline.
_DELIVERY_BUDGET_MULTIPLIER: dict[DecisionPreference, float] = {
    DecisionPreference.AGGRESSIVE: 1.5,  # more autonomous attempts, fewer handoffs
    DecisionPreference.BALANCED: 1.0,
    DecisionPreference.CONSERVATIVE: 0.5,  # hand off earlier, spend less
}


@dataclass(frozen=True, slots=True)
class DeliveryVerdict:
    """Explicit delivery decision replacing bare ``ValueError(gap_reasons)``."""

    status: Literal["delivered", "partial", "failed"]
    state: DeliveryState
    output: Any | None = None  # current best output; never discarded (AC4)
    errors: list[Any] = field(default_factory=list)
    recoverable: bool = False
    needs_human: bool = False
    rationale: str = ""
    review_prompt: str | None = None


def decide_delivery_verdict(
    *,
    success: bool,
    needs_human: bool,
    recoverable: bool,
    budget_left: bool,
    output: Any | None = None,
    errors: list[Any] | None = None,
    review_prompt: str | None = None,
) -> DeliveryVerdict:
    """Central state-machine transition. Pure (no I/O) — unit-tested.

    Order matters: subjective-needs-human short-circuits before budget is spent
    (AC3); recoverable-with-budget loops (AUTO_RECOVERING); recoverable-without-
    budget hands the current best output for review; non-recoverable escalates.
    """
    if success:
        return DeliveryVerdict(
            status="delivered",
            state=DeliveryState.DELIVERED,
            output=output,
            errors=errors or [],
            rationale="goal attained",
        )
    if needs_human:
        return DeliveryVerdict(
            status="partial",
            state=DeliveryState.DELIVERED_FOR_REVIEW,
            output=output,
            errors=errors or [],
            recoverable=recoverable,
            needs_human=True,
            rationale="subjective judgment required",
            review_prompt=review_prompt or "请确认方向或授权后继续，不会标记为已完成。",
        )
    if recoverable and budget_left:
        return DeliveryVerdict(
            status="partial",
            state=DeliveryState.AUTO_RECOVERING,
            output=output,
            errors=errors or [],
            recoverable=True,
            rationale="auto-recovering within budget",
        )
    if recoverable:
        return DeliveryVerdict(
            status="partial",
            state=DeliveryState.DELIVERED_FOR_REVIEW,
            output=output,
            errors=errors or [],
            recoverable=True,
            rationale="budget exhausted; handing current best output for review",
            review_prompt=review_prompt or "自动恢复预算已用尽，请评审当前版本并决定下一步。",
        )
    return DeliveryVerdict(
        status="failed",
        state=DeliveryState.ESCALATED,
        output=output,
        errors=errors or [],
        rationale="not self-recoverable; escalated",
    )


def as_delivery_state(*, recovered: bool, terminal_exhaust: bool) -> DeliveryState:
    """Map an existing ``DeliveryGapRecoveryResult`` to the explicit ``DeliveryState``."""
    if recovered:
        return DeliveryState.DELIVERED
    if terminal_exhaust:
        return DeliveryState.DELIVERED_FOR_REVIEW
    return DeliveryState.AUTO_RECOVERING


# ---------------------------------------------------------------------------
# AC5: persona -> budget coupling
# ---------------------------------------------------------------------------


def resolve_delivery_persona(value: Any) -> DecisionPreference:
    """Coerce any input to a ``DecisionPreference``; unknown -> BALANCED (safe default)."""
    try:
        return DecisionPreference(str(value).lower())
    except ValueError:
        return DecisionPreference.BALANCED


def recovery_budget_multiplier(persona: Any) -> float:
    """Multiplier applied to autonomous recovery budget by persona (AC5)."""
    return _DELIVERY_BUDGET_MULTIPLIER.get(resolve_delivery_persona(persona), 1.0)


def resolve_delivery_budget(
    persona: Any,
    base_turns: int,
    base_tokens: int,
    base_wall_seconds: int,
):
    """Scale the agentic autonomous budget by persona (AC5).

    Only ``max_turns`` (autonomous iteration budget) is scaled; token/wall budgets
    stay fixed so a single run cannot blow cost ceilings.
    """
    from regent.agent.types import AgentBudget

    multiplier = recovery_budget_multiplier(persona)
    return AgentBudget(
        max_turns=max(1, int(round(base_turns * multiplier))),
        max_tokens=base_tokens,
        max_wall_seconds=base_wall_seconds,
    )
