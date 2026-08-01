"""Primary failure taxonomy for Agent Core Restoration (M0-2).

Each failed generation attempt must map to exactly one primary code.
Secondary causes may be attached for diagnosis but must not replace the primary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PrimaryFailureCode(StrEnum):
    MODEL_TRUNCATED = "MODEL_TRUNCATED"
    TOOL_CALL_INVALID = "TOOL_CALL_INVALID"
    ARTIFACT_INCOMPLETE = "ARTIFACT_INCOMPLETE"
    STATIC_FAILED = "STATIC_FAILED"
    TEST_FAILED = "TEST_FAILED"
    START_FAILED = "START_FAILED"
    SMOKE_FAILED = "SMOKE_FAILED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    PREVIEW_FAILED = "PREVIEW_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    UNKNOWN = "UNKNOWN"


# Legacy aliases observed in production → canonical primary code.
_ALIAS_TO_PRIMARY: dict[str, PrimaryFailureCode] = {
    "EXHAUSTED_BUDGET": PrimaryFailureCode.BUDGET_EXHAUSTED,
    "BUDGET_EXHAUSTED": PrimaryFailureCode.BUDGET_EXHAUSTED,
    "MODEL_TRUNCATED": PrimaryFailureCode.MODEL_TRUNCATED,
    "TOOL_CALL_INVALID": PrimaryFailureCode.TOOL_CALL_INVALID,
    "ARTIFACT_INCOMPLETE": PrimaryFailureCode.ARTIFACT_INCOMPLETE,
    "STATIC_FAILED": PrimaryFailureCode.STATIC_FAILED,
    "TEST_FAILED": PrimaryFailureCode.TEST_FAILED,
    "START_FAILED": PrimaryFailureCode.START_FAILED,
    "SMOKE_FAILED": PrimaryFailureCode.SMOKE_FAILED,
    "PREVIEW_FAILED": PrimaryFailureCode.PREVIEW_FAILED,
    "VERIFICATION_FAILED": PrimaryFailureCode.VERIFICATION_FAILED,
    "project-tests": PrimaryFailureCode.TEST_FAILED,
    "smoke-http": PrimaryFailureCode.SMOKE_FAILED,
}


@dataclass(frozen=True, slots=True)
class PrimaryFailure:
    code: PrimaryFailureCode
    detail: str = ""
    secondary: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def primary_code(self) -> str:
        return str(self.code)


def normalize_primary_failure_code(raw: str | None) -> PrimaryFailureCode:
    """Map raw/legacy codes to a single primary; unknown → UNKNOWN (never success)."""
    if not raw:
        return PrimaryFailureCode.UNKNOWN
    key = str(raw).strip()
    if key in _ALIAS_TO_PRIMARY:
        return _ALIAS_TO_PRIMARY[key]
    upper = key.upper().replace(" ", "_")
    if upper in PrimaryFailureCode.__members__:
        return PrimaryFailureCode[upper]
    if upper in _ALIAS_TO_PRIMARY:
        return _ALIAS_TO_PRIMARY[upper]
    # Gap codes like "forbid-demo-shell: ..." → keep UNKNOWN unless exact alias.
    prefix = key.split(":", 1)[0].strip()
    if prefix in _ALIAS_TO_PRIMARY:
        return _ALIAS_TO_PRIMARY[prefix]
    return PrimaryFailureCode.UNKNOWN


def classify_finish_reason(finish_reason: str | None, *, had_tool_calls: bool) -> PrimaryFailureCode | None:
    """Return a primary failure when finish_reason indicates incomplete generation."""
    reason = str(finish_reason or "stop").lower()
    if reason == "length":
        return PrimaryFailureCode.MODEL_TRUNCATED
    if reason in {"content_filter", "content_filter_text"}:
        return PrimaryFailureCode.MODEL_TRUNCATED
    # tool_calls / stop with tools is normal; stop without tools is soft stop (not failure here).
    if reason == "tool_calls" and not had_tool_calls:
        return PrimaryFailureCode.TOOL_CALL_INVALID
    return None


def assert_not_success(code: PrimaryFailureCode | str | None) -> None:
    """Guard: unknown/exception codes must never be treated as success."""
    normalized = (
        code
        if isinstance(code, PrimaryFailureCode)
        else normalize_primary_failure_code(str(code) if code else None)
    )
    if normalized is PrimaryFailureCode.UNKNOWN:
        # UNKNOWN is a failure, not success — callers must fail-closed.
        return
    # No success codes exist in this enum by design.
    return


PRIMARY_FAILURE_CODES: frozenset[str] = frozenset(c.value for c in PrimaryFailureCode)
