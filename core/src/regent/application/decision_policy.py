"""Session-level decision preference + per-action allow/deny rules.

Priority: safety invariant > deny rules > allow rules > preference default > ask.

Explicit allow (Console「总是允许」→ goal.metadata decision_allow_actions) must
beat preference-matrix auto-deny (e.g. balanced×HIGH), otherwise always-allow
is a no-op for delivery_gap_intervene and similar high-risk actions.
See docs/console-dialog-prd-2026-07-31.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Mapping

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
# delivery_gap_intervene = continue/replan inside the goal — not a permission/danger gate.
# Humans only for release / quality / external_effect; delivery gaps auto-pass (LOW).
ACTION_RISK: dict[str, RiskLevel] = {
    "goal_confirm": RiskLevel.LOW,
    "release_approval": RiskLevel.MEDIUM,
    "quality_approval": RiskLevel.MEDIUM,
    "delivery_gap_intervene": RiskLevel.LOW,
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

    def with_extra_allow_actions(self, extra: Iterable[str] | None) -> DecisionPolicy:
        """Return a copy that unions ``extra`` into allow_actions (goal-scoped)."""
        added = _parse_action_set(extra)
        if not added:
            return self
        return DecisionPolicy(
            preference=self.preference,
            rules=DecisionRules(
                allow_actions=self.rules.allow_actions | added,
                deny_actions=self.rules.deny_actions,
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

        # Explicit allow beats preference auto-deny (「总是允许」).
        if action in self.rules.allow_actions:
            applied.append(f"allow_rule:{action}")
            return PolicyDecision.ALLOW, tuple(applied)

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


def decision_policy_for_goal_metadata(
    metadata: Mapping[str, Any] | None,
) -> DecisionPolicy:
    """Config policy + goal.metadata_json.decision_allow_actions (CD-3.5)."""
    base = load_decision_policy_from_config()
    raw = (metadata or {}).get("decision_allow_actions")
    if isinstance(raw, str):
        extra: Iterable[str] = [p.strip() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        extra = raw
    else:
        extra = ()
    return base.with_extra_allow_actions(extra)


def action_preauthorized(
    metadata: Mapping[str, Any] | None,
    action: str,
    *,
    risk_level: RiskLevel | str | None = None,
) -> bool:
    """True only for explicit allow-list (「总是允许」), not preference auto-allow.

    Preference matrix (e.g. balanced×LOW→ALLOW) must not silently pre-authorize
    delivery recovery — that would infinite-reset the ladder on exhaustion.
    """
    del risk_level  # explicit allow-list only; risk unused
    policy = decision_policy_for_goal_metadata(metadata)
    if action in policy.rules.deny_actions:
        return False
    return action in policy.rules.allow_actions
