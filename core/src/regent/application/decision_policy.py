"""Session-level decision preference + per-action allow/deny rules.

Priority: safety invariant > deny rules > preference default > allow rules > ask.
See docs/console-dialog-prd-2026-07-31.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable

from regent.application.confirmation import (
    ConfirmationRequest,
    DecisionPreference,
    RiskLevel,
    TimeoutDefault,
    preference_timeout_default,
    resolve_default,
)


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


# Minimal first-slice action vocabulary (Web Console / HumanTask).
KNOWN_ACTIONS: frozenset[str] = frozenset(
    {
        "goal_confirm",
        "release_approval",
        "quality_approval",
        "delivery_gap_intervene",
        "external_effect",
    }
)

# Default risk when building confirmations for known actions.
ACTION_RISK: dict[str, RiskLevel] = {
    "goal_confirm": RiskLevel.LOW,
    "release_approval": RiskLevel.MEDIUM,
    "quality_approval": RiskLevel.MEDIUM,
    "delivery_gap_intervene": RiskLevel.HIGH,
    "external_effect": RiskLevel.HIGH,
}


def _parse_action_set(raw: str | Iterable[str] | None) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return frozenset(parts)
    return frozenset(str(p).strip() for p in raw if str(p).strip())


@dataclass(frozen=True, slots=True)
class DecisionRules:
    allow_actions: frozenset[str] = field(default_factory=frozenset)
    deny_actions: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_settings(
        cls,
        *,
        allow_actions: str | Iterable[str] | None = None,
        deny_actions: str | Iterable[str] | None = None,
    ) -> DecisionRules:
        return cls(
            allow_actions=_parse_action_set(allow_actions),
            deny_actions=_parse_action_set(deny_actions),
        )


@dataclass(frozen=True, slots=True)
class DecisionPolicy:
    preference: DecisionPreference = DecisionPreference.BALANCED
    rules: DecisionRules = field(default_factory=DecisionRules)

    @classmethod
    def from_settings(
        cls,
        *,
        preference: str | DecisionPreference = DecisionPreference.BALANCED,
        allow_actions: str | Iterable[str] | None = None,
        deny_actions: str | Iterable[str] | None = None,
    ) -> DecisionPolicy:
        return cls(
            preference=DecisionPreference(preference),
            rules=DecisionRules.from_settings(
                allow_actions=allow_actions, deny_actions=deny_actions
            ),
        )

    def evaluate(
        self,
        action: str,
        *,
        risk_level: RiskLevel | str | None = None,
        safety_invariant: bool = False,
    ) -> tuple[PolicyDecision, tuple[str, ...]]:
        """Return (decision, rules_applied) for an action under this policy."""
        applied: list[str] = []
        if safety_invariant:
            applied.append("safety_invariant")
            return PolicyDecision.DENY, tuple(applied)

        if action in self.rules.deny_actions:
            applied.append(f"deny_rule:{action}")
            return PolicyDecision.DENY, tuple(applied)

        risk = RiskLevel(risk_level) if risk_level is not None else ACTION_RISK.get(
            action, RiskLevel.MEDIUM
        )
        probe = ConfirmationRequest(
            action=action,
            summary=action,
            rules_applied=(),
            risk_level=risk,
            rationale="",
            on_allow="",
            on_deny="",
            timeout_seconds=1,
            default_on_timeout=preference_timeout_default(self.preference),
        )
        routed = resolve_default(self.preference, probe)
        applied.append(f"preference:{self.preference.value}")
        applied.append(f"risk:{risk.value}")

        if routed is TimeoutDefault.ALLOW:
            applied.append("preference_default:allow")
            return PolicyDecision.ALLOW, tuple(applied)
        if routed is TimeoutDefault.DENY:
            applied.append("preference_default:deny")
            return PolicyDecision.DENY, tuple(applied)

        # CANCEL / ask — allow-list may still auto-approve
        if action in self.rules.allow_actions:
            applied.append(f"allow_rule:{action}")
            return PolicyDecision.ALLOW, tuple(applied)

        applied.append("ask")
        return PolicyDecision.ASK, tuple(applied)


def load_decision_policy_from_config() -> DecisionPolicy:
    from regent.config import get_settings

    settings = get_settings()
    return DecisionPolicy.from_settings(
        preference=settings.decision_preference,
        allow_actions=settings.decision_allow_actions,
        deny_actions=settings.decision_deny_actions,
    )
