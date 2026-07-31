"""GQ-4 default-switch promotion control flow (Tech-Spec §13.7, Plan §13).

Wires the previously-orphan ``gq4_default_switch_gate`` into an enforced
promotion path. The runtime default itself is still driven by the
``generation_strategy`` env knob; this module is the mandatory pre-flight
gate an operator/CI MUST pass before flipping that knob to ``agentic``.

Control flow:
  1. Run the GQ-3 experiment (``drive_generation_strategy_experiment``) → report.
  2. ``evaluate_gq4_promotion(report, decision_record_ref, kill_switch=...)`` →
     returns the gate decision (activation_allowed / reason).
  3. If allowed, the operator records the DecisionRecord and flips
     ``REGENT_GENERATION_STRATEGY=agentic``. ``apply_gq4_promotion`` raises
     ``DomainError`` when the gate is not satisfied, so promotion cannot
     proceed without evidence. Kill switch always overrides at runtime.
"""

from __future__ import annotations

from typing import Any

from regent.application.generation_strategy_experiment import gq4_default_switch_gate
from regent.domain.errors import DomainError, ErrorCode


def evaluate_gq4_promotion(
    experiment_report: dict[str, Any],
    *,
    kill_switch: bool,
    decision_record_ref: str,
) -> dict[str, Any]:
    """Evaluate the GQ-4 gate without raising.

    Returns the gate decision enriched with the DecisionRecord reference. The
    caller decides whether to record the DecisionRecord and flip the knob.
    """
    gate = gq4_default_switch_gate(experiment_report, kill_switch=kill_switch)
    return {
        **gate,
        "decision_record_ref": decision_record_ref,
    }


def apply_gq4_promotion(
    experiment_report: dict[str, Any],
    *,
    kill_switch: bool,
    decision_record_ref: str,
) -> dict[str, Any]:
    """Enforced promotion gate: raise unless the GQ-4 gate allows activation.

    Call this from the promotion process (operator script / CI) before setting
    ``REGENT_GENERATION_STRATEGY=agentic``. On success returns the decision so
    the caller can persist the DecisionRecord and flip the knob.
    """
    gate = gq4_default_switch_gate(experiment_report, kill_switch=kill_switch)
    if not gate["activation_allowed"]:
        raise DomainError(
            ErrorCode.POLICY_DENIED,
            f"GQ-4 promotion blocked (decision_record_ref={decision_record_ref}): "
            f"{gate['reason']}",
        )
    return {
        **gate,
        "decision_record_ref": decision_record_ref,
    }
