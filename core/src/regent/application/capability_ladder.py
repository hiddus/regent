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


def plan_escalation(
    prior_attempts: int, max_attempts: int | None = None
) -> EscalationPlan:
    """Prior attempts already spent; return the next step to apply.

    ``max_attempts`` overrides ``MAX_ATTAINMENT_ESCALATION_ATTEMPTS`` (AC5 persona
    budget). When ``max_attempts`` exceeds the ladder length, the ladder wraps so
    an aggressive persona simply repeats cycles instead of raising IndexError.
    """
    cap = int(max_attempts) if max_attempts is not None else MAX_ATTAINMENT_ESCALATION_ATTEMPTS
    next_attempt = int(prior_attempts) + 1
    if next_attempt > cap:
        return EscalationPlan(next_attempt, EscalationStep.STOP, exhausted=True)
    index = next_attempt - 1
    step = _LADDER[index] if index < len(_LADDER) else _LADDER[index % len(_LADDER)]
    return EscalationPlan(next_attempt, step, exhausted=False)


def composed_capability_name(gap_kind: str) -> str:
    return f"composed-delivery-{gap_kind}-v1"


def built_capability_name(gap_kind: str) -> str:
    return f"goal-gap-{gap_kind}-v1"
