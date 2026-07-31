"""CON-0 / CON-1 / CON-2 confirmation + decision policy unit tests."""

from __future__ import annotations

import asyncio

import pytest

from regent.application.confirmation import (
    ConfirmationRequest,
    CountdownConfirmation,
    DecisionPreference,
    RiskLevel,
    TimeoutDefault,
    resolve_default,
    safety_invariant_request,
)
from regent.application.decision_policy import DecisionPolicy, PolicyDecision


def _req(risk: RiskLevel, *, safety: bool = False) -> ConfirmationRequest:
    if safety:
        return safety_invariant_request(
            action="assert_generator_consistency",
            summary="safety",
            rationale="fail-closed",
            rules_applied=("safety",),
        )
    return ConfirmationRequest(
        action="release_approval",
        summary="test",
        rules_applied=("t",),
        risk_level=risk,
        rationale="why",
        on_allow="go",
        on_deny="stop",
        timeout_seconds=30,
        default_on_timeout=TimeoutDefault.DENY,
    )


@pytest.mark.parametrize(
    ("pref", "risk", "expected"),
    [
        (DecisionPreference.AGGRESSIVE, RiskLevel.LOW, TimeoutDefault.ALLOW),
        (DecisionPreference.AGGRESSIVE, RiskLevel.MEDIUM, TimeoutDefault.ALLOW),
        (DecisionPreference.AGGRESSIVE, RiskLevel.HIGH, TimeoutDefault.CANCEL),
        (DecisionPreference.BALANCED, RiskLevel.LOW, TimeoutDefault.ALLOW),
        (DecisionPreference.BALANCED, RiskLevel.MEDIUM, TimeoutDefault.CANCEL),
        (DecisionPreference.BALANCED, RiskLevel.HIGH, TimeoutDefault.DENY),
        (DecisionPreference.CONSERVATIVE, RiskLevel.LOW, TimeoutDefault.CANCEL),
        (DecisionPreference.CONSERVATIVE, RiskLevel.MEDIUM, TimeoutDefault.CANCEL),
        (DecisionPreference.CONSERVATIVE, RiskLevel.HIGH, TimeoutDefault.DENY),
    ],
)
def test_resolve_default_matrix(pref, risk, expected) -> None:
    assert resolve_default(pref, _req(risk)) is expected


def test_safety_invariant_always_deny_any_preference() -> None:
    req = _req(RiskLevel.LOW, safety=True)
    assert req.timeout_seconds == 0
    for pref in DecisionPreference:
        assert resolve_default(pref, req) is TimeoutDefault.DENY


def test_deny_rule_overrides_preference_allow() -> None:
    policy = DecisionPolicy.from_settings(
        preference="aggressive",
        allow_actions="",
        deny_actions="release_approval",
    )
    decision, rules = policy.evaluate("release_approval", risk_level=RiskLevel.LOW)
    assert decision is PolicyDecision.DENY
    assert any(r.startswith("deny_rule:") for r in rules)


def test_allow_rule_overrides_ask() -> None:
    policy = DecisionPolicy.from_settings(
        preference="conservative",
        allow_actions="goal_confirm",
        deny_actions="",
    )
    decision, rules = policy.evaluate("goal_confirm", risk_level=RiskLevel.LOW)
    assert decision is PolicyDecision.ALLOW
    assert "allow_rule:goal_confirm" in rules


def test_safety_beats_allow_rule() -> None:
    policy = DecisionPolicy.from_settings(
        preference="aggressive",
        allow_actions="external_effect",
        deny_actions="",
    )
    decision, rules = policy.evaluate(
        "external_effect", risk_level=RiskLevel.LOW, safety_invariant=True
    )
    assert decision is PolicyDecision.DENY
    assert "safety_invariant" in rules


@pytest.mark.asyncio
async def test_countdown_triggers_default_on_timeout() -> None:
    req = ConfirmationRequest(
        action="quality_approval",
        summary="q",
        rules_applied=("t",),
        risk_level=RiskLevel.MEDIUM,
        rationale="r",
        on_allow="a",
        on_deny="d",
        timeout_seconds=1,
        default_on_timeout=TimeoutDefault.DENY,
    )
    clock = CountdownConfirmation(req)

    async def never() -> TimeoutDefault:
        await asyncio.sleep(5)
        return TimeoutDefault.ALLOW

    result = await clock.wait_or_timeout(never)
    assert result is TimeoutDefault.DENY


@pytest.mark.asyncio
async def test_countdown_safety_never_auto_times_out() -> None:
    req = safety_invariant_request(
        action="gq4_default_switch_gate",
        summary="blocked",
        rationale="gate",
        rules_applied=("gq4",),
    )
    clock = CountdownConfirmation(req)
    assert not clock.deadline_applies()

    async def human() -> TimeoutDefault:
        return TimeoutDefault.DENY

    assert await clock.wait_or_timeout(human) is TimeoutDefault.DENY
