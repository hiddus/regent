"""Agent invocation guard — prevent cross-agent dead-loops and token waste.

Enforces hub-and-spoke discipline:
- The orchestrator dispatches tasks to specialist agents.
- Specialist agents execute and report back; they do NOT invoke each other.
- ``delegate_plan_item`` nesting is capped at depth 1 (main → sub, no further).

This module provides:
1. ``check_invocation_allowed`` — called before any cross-agent dispatch.
2. ``InvocationGuardError`` — raised when a disallowed invocation is detected.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class InvocationGuardError(Exception):
    """Raised when a cross-agent invocation violates hub-and-spoke rules."""

    def __init__(self, source: str, target: str, reason: str) -> None:
        self.source = source
        self.target = target
        self.reason = reason
        super().__init__(f"Agent invocation denied: {source} → {target}: {reason}")


@dataclass(frozen=True, slots=True)
class InvocationDecision:
    """Result of an invocation guard check."""

    allowed: bool
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason}


# Maximum depth for delegate_plan_item nesting.
# 0 = main agent can delegate; sub-agents cannot re-delegate.
# This is enforced at the AgentRunner level via max_subagent_depth=1.
MAX_EFFECTIVE_DELEGATE_DEPTH = 1


def check_subagent_delegate_allowed(
    *,
    current_depth: int,
    max_depth: int | None = None,
) -> InvocationDecision:
    """Check whether a sub-agent at ``current_depth`` may delegate further.

    Rules:
    - depth 0 (main agent): always allowed.
    - depth >= 1 (sub-agent): denied — sub-agents must not re-delegate.
    """
    effective_max = max_depth if max_depth is not None else MAX_EFFECTIVE_DELEGATE_DEPTH
    if current_depth >= effective_max:
        return InvocationDecision(
            allowed=False,
            reason=(
                f"Sub-agent at depth {current_depth} cannot delegate further "
                f"(max effective depth {effective_max}). "
                "Hub-and-spoke: only the main agent dispatches work."
            ),
        )
    return InvocationDecision(allowed=True)


def check_cross_deployment_invocation(
    *,
    source_deployment_id: uuid.UUID,
    target_deployment_id: uuid.UUID,
    source_role: str | None,
    target_role: str | None,
    active_chain: list[uuid.UUID] | None = None,
) -> InvocationDecision:
    """Check whether source deployment may dispatch a task to target.

    Prevents:
    - Circular routing: A → B → A (detected via active_chain).
    - Peer-to-peer invocation between delivery roles (product → tech, etc.).
    """
    if source_deployment_id == target_deployment_id:
        return InvocationDecision(
            allowed=False,
            reason="Self-invocation denied: source == target.",
        )

    # Cycle detection via active chain.
    chain = active_chain or []
    if target_deployment_id in chain:
        cycle = " → ".join(str(c)[:8] for c in chain + [target_deployment_id])
        return InvocationDecision(
            allowed=False,
            reason=f"Circular invocation detected: {cycle}.",
        )

    # Delivery role peer-to-peer check: roles in the delivery swarm must not
    # invoke each other. Only the orchestrator (no role or role="orchestrator")
    # may dispatch to them.
    delivery_roles = {"product", "tech", "test", "ux", "ops"}
    if source_role in delivery_roles and target_role in delivery_roles:
        return InvocationDecision(
            allowed=False,
            reason=(
                f"Delivery role '{source_role}' cannot invoke '{target_role}'. "
                "Hub-and-spoke: delivery roles report to the orchestrator only."
            ),
        )

    return InvocationDecision(allowed=True)


def log_invocation_denied(
    *,
    source: str,
    target: str,
    reason: str,
    goal_id: uuid.UUID | None = None,
) -> None:
    """Structured log for denied invocations — aids debugging and audit."""
    logger.warning(
        "agent invocation denied",
        extra={
            "source": source,
            "target": target,
            "reason": reason,
            "goal_id": str(goal_id) if goal_id else None,
        },
    )
