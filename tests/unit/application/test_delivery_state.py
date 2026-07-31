"""Unit tests for the delivery state machine (AC1/AC3/AC5) and the AC1 grep gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from regent.application.capability_ladder import EscalationStep, plan_escalation
from regent.application.delivery_state import (
    DeliveryState,
    DeliveryVerdict,
    as_delivery_state,
    decide_delivery_verdict,
    recovery_budget_multiplier,
    resolve_delivery_budget,
    resolve_delivery_persona,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_SCRIPT = REPO_ROOT / "ops" / "delivery_dead_end_gate.py"


# --- decide_delivery_verdict truth table (no I/O) ---


def test_delivered_on_success():
    v = decide_delivery_verdict(success=True, needs_human=False, recoverable=True, budget_left=True)
    assert v.state is DeliveryState.DELIVERED
    assert v.status == "delivered"
    assert v.recoverable is False


def test_needs_human_short_circuits_before_budget():
    # AC3: subjective judgment -> DELIVERED_FOR_REVIEW even with budget left.
    v = decide_delivery_verdict(
        success=False, needs_human=True, recoverable=True, budget_left=True
    )
    assert v.state is DeliveryState.DELIVERED_FOR_REVIEW
    assert v.needs_human is True
    assert v.review_prompt is not None


def test_auto_recovering_within_budget():
    v = decide_delivery_verdict(
        success=False, needs_human=False, recoverable=True, budget_left=True
    )
    assert v.state is DeliveryState.AUTO_RECOVERING
    assert v.recoverable is True


def test_budget_exhausted_hands_current_output_for_review():
    v = decide_delivery_verdict(
        success=False, needs_human=False, recoverable=True, budget_left=False, output="best"
    )
    assert v.state is DeliveryState.DELIVERED_FOR_REVIEW
    assert v.output == "best"  # AC4: current best output never discarded


def test_not_recoverable_escalates():
    v = decide_delivery_verdict(
        success=False, needs_human=False, recoverable=False, budget_left=True
    )
    assert v.state is DeliveryState.ESCALATED
    assert v.status == "failed"


def test_no_silent_terminal_state():
    # Iron rule: every terminal state has an explicit exit.
    for needs_human in (True, False):
        for recoverable in (True, False):
            for budget_left in (True, False):
                v = decide_delivery_verdict(
                    success=False,
                    needs_human=needs_human,
                    recoverable=recoverable,
                    budget_left=budget_left,
                )
                assert v.state in {
                    DeliveryState.DELIVERED_FOR_REVIEW,
                    DeliveryState.ESCALATED,
                    DeliveryState.AUTO_RECOVERING,
                }


# --- AC5: persona -> budget ---


def test_recovery_budget_multiplier():
    assert recovery_budget_multiplier("aggressive") == 1.5
    assert recovery_budget_multiplier("balanced") == 1.0
    assert recovery_budget_multiplier("conservative") == 0.5
    # unknown -> balanced default
    assert recovery_budget_multiplier("nonsense") == 1.0


def test_resolve_delivery_persona_unknown_defaults_balanced():
    assert resolve_delivery_persona("whatever") is resolve_delivery_persona("balanced")


def test_resolve_delivery_budget_scales_turns_only():
    balanced = resolve_delivery_budget("balanced", 40, 200_000, 900)
    aggressive = resolve_delivery_budget("aggressive", 40, 200_000, 900)
    conservative = resolve_delivery_budget("conservative", 40, 200_000, 900)
    # turns scale, token / wall budgets fixed
    assert balanced.max_turns == 40
    assert aggressive.max_turns == 60
    assert conservative.max_turns == 20
    for b in (balanced, aggressive, conservative):
        assert b.max_tokens == 200_000
        assert b.max_wall_seconds == 900


# --- as_delivery_state mapping ---


def test_as_delivery_state():
    assert as_delivery_state(recovered=True, terminal_exhaust=False) is DeliveryState.AUTO_RECOVERING
    assert (
        as_delivery_state(recovered=False, terminal_exhaust=True)
        is DeliveryState.DELIVERED_FOR_REVIEW
    )
    assert (
        as_delivery_state(recovered=False, terminal_exhaust=False)
        is DeliveryState.AUTO_RECOVERING
    )


def test_decide_delivery_verdict_has_production_caller():
    """CD-6.5: behavior-level check — orchestrator imports and calls the verdict API."""
    import inspect

    from regent.application import execution_orchestrator as orch
    from regent.application.delivery_state import decide_delivery_verdict

    assert orch.decide_delivery_verdict is decide_delivery_verdict
    src = inspect.getsource(orch.ExecutionOrchestrator)
    assert "decide_delivery_verdict(" in src


# --- capability ladder override (AC5 recovery budget) ---


def test_plan_escalation_default_max():
    assert plan_escalation(0).step is EscalationStep.REUSE
    assert plan_escalation(0).exhausted is False
    # default MAX == len(_LADDER) == 10 -> attempt 11 exhausts
    last = plan_escalation(10)
    assert last.exhausted is True
    assert last.step is EscalationStep.STOP


def test_plan_escalation_max_attempts_override():
    # conservative persona shrinks the ladder
    assert plan_escalation(4, max_attempts=5).exhausted is False
    assert plan_escalation(5, max_attempts=5).exhausted is True
    # aggressive persona extends; ladder wraps instead of IndexError
    extended = plan_escalation(10, max_attempts=15)
    assert extended.exhausted is False
    assert extended.step is EscalationStep.REUSE


def test_gate_reorg_max_scales_with_persona() -> None:
    from regent.application.delivery_state import gate_reorg_max, gate_reorg_step_name

    assert gate_reorg_max("balanced") == 6
    assert gate_reorg_max("aggressive") == 9
    assert gate_reorg_max("conservative") == 3
    assert gate_reorg_step_name(0) == "COMPOSE"
    assert gate_reorg_step_name(2) == "ACQUIRE"
    assert gate_reorg_step_name(3) == "COMPOSE"


def test_delivery_rejection_transcript_code() -> None:
    from regent.application.delivery_rejection import DeliveryRejection
    from regent.domain.errors import ErrorCode

    exc = DeliveryRejection(
        reasons=["transcript-persist-failed: boom"],
        code=ErrorCode.TRANSCRIPT_PERSIST_FAILED,
        retryable=True,
    )
    assert exc.code is ErrorCode.TRANSCRIPT_PERSIST_FAILED
    assert exc.retryable is True
    assert "transcript-persist-failed" in exc.reasons[0]


# --- AC1 grep gate ---


def test_ac1_gate_passes_on_repo():
    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_ac1_gate_detects_intentional_dead_end(tmp_path: Path):
    """Meta-test: a method that sets terminal_exhaust=True without handoff must fail."""
    import importlib.util

    bad = tmp_path / "dead_end_sample.py"
    bad.write_text(
        "async def recover_without_exit(self):\n"
        "    return dict(terminal_exhaust=True, recovered=False)\n",
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location("delivery_dead_end_gate", GATE_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    violations = mod.check_file(bad)
    assert violations, "gate must report the intentional dead-end"
