"""ATTRIBUTE_3 capability escalation ladder for Goal attainment.

Order (definition): REUSE → CONFIGURE → COMPOSE → BUILD → ACQUIRE → request human last.
GAC-D maps auto-recovery attempts onto this ladder before explicit termination.
V3 addition: ACQUIRE step allows fetching external capability packages from the network.

Product principle: unmet Goal must keep enumerating paths — the ladder runs multiple
cycles before requesting human (WAITING_HUMAN), never calm EXHAUST on first miss.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EscalationStep(StrEnum):
    REUSE = "REUSE"
    CONFIGURE = "CONFIGURE"
    COMPOSE = "COMPOSE"
    BUILD = "BUILD"
    ACQUIRE = "ACQUIRE"
    STOP = "STOP"


# One cycle of the ATTRIBUTE_3 auto ladder (human is outside the cycle).
_CYCLE: tuple[EscalationStep, ...] = (
    EscalationStep.REUSE,
    EscalationStep.CONFIGURE,
    EscalationStep.COMPOSE,
    EscalationStep.BUILD,
    EscalationStep.ACQUIRE,
)

# Two full cycles before requesting human — enough to try alternate strategies.
ATTAINMENT_LADDER_CYCLES = 2

_LADDER: tuple[EscalationStep, ...] = _CYCLE * ATTAINMENT_LADDER_CYCLES

# Max successful recovery rounds before terminal human handoff.
MAX_ATTAINMENT_ESCALATION_ATTEMPTS = len(_LADDER)


@dataclass(frozen=True, slots=True)
class EscalationPlan:
    attempt: int
    step: EscalationStep
    exhausted: bool


def plan_escalation(prior_attempts: int) -> EscalationPlan:
    """Prior attempts already spent; return the next step to apply."""
    next_attempt = int(prior_attempts) + 1
    if next_attempt > MAX_ATTAINMENT_ESCALATION_ATTEMPTS:
        return EscalationPlan(next_attempt, EscalationStep.STOP, exhausted=True)
    index = next_attempt - 1
    return EscalationPlan(next_attempt, _LADDER[index], exhausted=False)


def composed_capability_name(gap_kind: str) -> str:
    return f"composed-delivery-{gap_kind}-v1"


def built_capability_name(gap_kind: str) -> str:
    return f"goal-gap-{gap_kind}-v1"
