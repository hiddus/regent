"""ATTRIBUTE_3 capability escalation ladder for Goal attainment.

Order (definition): REUSE → CONFIGURE → COMPOSE → BUILD → ACQUIRE → request human last.
GAC-D maps auto-recovery attempts onto this ladder before explicit termination.
V3 addition: ACQUIRE step allows fetching external capability packages from the network.
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


# Attempt 1..N maps to ladder; beyond → STOP (ATTRIBUTE_7).
_LADDER: tuple[EscalationStep, ...] = (
    EscalationStep.REUSE,
    EscalationStep.COMPOSE,
    EscalationStep.BUILD,
    EscalationStep.ACQUIRE,
)

# Max successful recovery rounds before terminal (matches len(_LADDER)).
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
