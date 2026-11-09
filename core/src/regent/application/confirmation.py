"""Console confirmation contract: preference defaults + ConfirmationRequest.

Web Console (not CLI) uses this envelope for HumanTask / chat approvals.
See docs/console-dialog-prd-2026-07-31.md.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Mapping


class DecisionPreference(StrEnum):
    AGGRESSIVE = "aggressive"
    BALANCED = "balanced"
    CONSERVATIVE = "conservative"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TimeoutDefault(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    CANCEL = "cancel"


DEFAULT_OPTIONS: tuple[str, ...] = ("allow", "allow_always", "deny", "deny_always")


@dataclass(frozen=True, slots=True)
class ConfirmationRequest:
    """Structured confirmation shown in Console; raw errors go in ``detail``."""

    action: str
    summary: str
    rules_applied: tuple[str, ...]
    risk_level: RiskLevel
    rationale: str
    on_allow: str
    on_deny: str
    timeout_seconds: int
    default_on_timeout: TimeoutDefault
    options: tuple[str, ...] = DEFAULT_OPTIONS
    safety_invariant: bool = False
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds < 0:
            raise ValueError("timeout_seconds must be >= 0")
        if self.safety_invariant and self.timeout_seconds != 0:
            object.__setattr__(self, "timeout_seconds", 0)
        if self.safety_invariant:
            object.__setattr__(self, "default_on_timeout", TimeoutDefault.DENY)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["risk_level"] = self.risk_level.value
        payload["default_on_timeout"] = self.default_on_timeout.value
        payload["rules_applied"] = list(self.rules_applied)
        payload["options"] = list(self.options)
        return payload

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ConfirmationRequest:
        return cls(
            action=str(data.get("action") or "unknown"),
            summary=str(data.get("summary") or ""),
            rules_applied=tuple(str(r) for r in (data.get("rules_applied") or ())),
            risk_level=RiskLevel(str(data.get("risk_level") or RiskLevel.MEDIUM)),
            rationale=str(data.get("rationale") or ""),
            on_allow=str(data.get("on_allow") or ""),
            on_deny=str(data.get("on_deny") or ""),
            timeout_seconds=int(data.get("timeout_seconds") or 0),
            default_on_timeout=TimeoutDefault(
                str(data.get("default_on_timeout") or TimeoutDefault.DENY)
            ),
            options=tuple(str(o) for o in (data.get("options") or DEFAULT_OPTIONS)),
            safety_invariant=bool(data.get("safety_invariant", False)),
            detail=(str(data["detail"]) if data.get("detail") is not None else None),
        )


def preference_timeout_default(preference: DecisionPreference) -> TimeoutDefault:
    if preference is DecisionPreference.AGGRESSIVE:
        return TimeoutDefault.ALLOW
    return TimeoutDefault.DENY


def resolve_default(
    preference: DecisionPreference | str,
    request: ConfirmationRequest,
) -> TimeoutDefault:
    """Route preference × risk to allow / deny / cancel (cancel = must ask).

    Safety invariants always deny. Explicit request.default_on_timeout is used
    only for timeout application via CountdownConfirmation / HumanTask — this
    function returns the *auto-resolve* default for the preference matrix.
    """
    if request.safety_invariant:
        return TimeoutDefault.DENY

    pref = DecisionPreference(preference)
    risk = request.risk_level

    if pref is DecisionPreference.AGGRESSIVE:
        if risk is RiskLevel.HIGH:
            return TimeoutDefault.CANCEL
        return TimeoutDefault.ALLOW

    if pref is DecisionPreference.CONSERVATIVE:
        if risk is RiskLevel.HIGH:
            return TimeoutDefault.DENY
        return TimeoutDefault.CANCEL

    # balanced
    if risk is RiskLevel.LOW:
        return TimeoutDefault.ALLOW
    if risk is RiskLevel.HIGH:
        return TimeoutDefault.DENY
    return TimeoutDefault.CANCEL


def safety_invariant_request(
    *,
    action: str,
    summary: str,
    rationale: str,
    rules_applied: tuple[str, ...] | list[str],
    detail: str | None = None,
    on_allow: str = "不允许：安全不变量禁止自动放行",
    on_deny: str = "保持拒绝，流程 fail-closed",
) -> ConfirmationRequest:
    """Build a non-timeout confirmation envelope for fail-closed gates."""
    return ConfirmationRequest(
        action=action,
        summary=summary,
        rules_applied=tuple(rules_applied),
        risk_level=RiskLevel.HIGH,
        rationale=rationale,
        on_allow=on_allow,
        on_deny=on_deny,
        timeout_seconds=0,
        default_on_timeout=TimeoutDefault.DENY,
        safety_invariant=True,
        detail=detail,
    )


def build_confirmation(
    *,
    action: str,
    summary: str,
    risk_level: RiskLevel | str,
    rationale: str,
    on_allow: str,
    on_deny: str,
    preference: DecisionPreference | str = DecisionPreference.BALANCED,
    rules_applied: tuple[str, ...] | list[str] | None = None,
    timeout_seconds: int | None = None,
    detail: str | None = None,
    safety_invariant: bool = False,
) -> ConfirmationRequest:
    """Construct a confirmation with preference-derived timeout default."""
    pref = DecisionPreference(preference)
    risk = RiskLevel(risk_level)
    if safety_invariant:
        return safety_invariant_request(
            action=action,
            summary=summary,
            rationale=rationale,
            rules_applied=rules_applied or (f"safety:{action}",),
            detail=detail,
            on_allow=on_allow,
            on_deny=on_deny,
        )
    timeout = 300 if timeout_seconds is None else int(timeout_seconds)
    rules = list(rules_applied or ())
    rules.append(f"preference:{pref.value}")
    rules.append(f"risk:{risk.value}")
    return ConfirmationRequest(
        action=action,
        summary=summary,
        rules_applied=tuple(rules),
        risk_level=risk,
        rationale=rationale,
        on_allow=on_allow,
        on_deny=on_deny,
        timeout_seconds=timeout,
        default_on_timeout=preference_timeout_default(pref),
        detail=detail,
    )


@dataclass(frozen=True, slots=True)
class CountdownConfirmation:
    """Server-side timeout primitive for confirmation defaults.

    Web Console mirrors ``timeout_seconds`` / ``default_on_timeout`` in UI;
    HumanTask.due_at is the durable clock.
    """

    request: ConfirmationRequest

    def deadline_applies(self) -> bool:
        return self.request.timeout_seconds > 0 and not self.request.safety_invariant

    def on_timeout_decision(self) -> TimeoutDefault:
        if self.request.safety_invariant or not self.deadline_applies():
            return TimeoutDefault.DENY
        return self.request.default_on_timeout

    async def wait_or_timeout(
        self,
        waiter,
    ) -> TimeoutDefault:
        """Race ``waiter()`` against timeout; never hangs when timeout > 0.

        ``waiter`` is an awaitable returning TimeoutDefault (or allow/deny/cancel str).
        """
        import asyncio

        if not self.deadline_applies():
            result = await waiter()
            return TimeoutDefault(result) if not isinstance(result, TimeoutDefault) else result

        try:
            result = await asyncio.wait_for(
                waiter(), timeout=float(self.request.timeout_seconds)
            )
            return TimeoutDefault(result) if not isinstance(result, TimeoutDefault) else result
        except TimeoutError:
            return self.on_timeout_decision()
