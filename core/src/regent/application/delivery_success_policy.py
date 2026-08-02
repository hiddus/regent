"""Shared helpers for delivery success / concurrency optimization."""

from __future__ import annotations

from typing import Any

# Gaps that remain fail-closed even for SMALL / soft-pass ACHIEVE.
BLOCKING_DELIVERY_GAP_CODES: frozenset[str] = frozenset(
    {
        "forbid-unrendered-templates",
        "forbid-demo-shell",
        "forbid-demo-copy",
        "forbid-placeholder-content",
        "forbid-trivial-server",
        "forbid-pure-static-backend",
        "goal-semantic-alignment",
    }
)

# Same gap_kind may only auto-escalate this many times before a soft reset / pause.
SAME_GAP_KIND_HARD_CAP = 3

# After hard-cap / ladder exhaust: auto-reset and continue this many times, then
# soft-pause (conversation note only — never a permission TaskCard).
DELIVERY_GAP_AUTO_CONTINUE_MAX = 2

# Absolute ceiling across gap_kind flips. Auto-continue must NOT reset this —
# otherwise alternating presentation/product_surface burns forever.
DELIVERY_GAP_TOTAL_ATTEMPTS_HARD_CAP = 6


def effective_max_concurrent_generating(settings: Any) -> int:
    """Fleet-aware generation cap.

    ``max_concurrent_generating <= 0`` → auto = replicas × dispatch × 2.
    Explicit positive value is an absolute cap (may be lower than auto to
    protect a fragile model gateway, or higher for burst capacity).
    """
    replicas = max(1, int(getattr(settings, "worker_replicas", 1) or 1))
    dispatch = max(1, int(getattr(settings, "worker_dispatch_concurrency", 1) or 1))
    auto = replicas * dispatch * 2
    configured = int(getattr(settings, "max_concurrent_generating", 0) or 0)
    if configured <= 0:
        return auto
    return configured


def _gap_codes(verification: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for key in ("failed_checks", "gaps", "reasons"):
        raw = verification.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, str):
                code = item.split(":", 1)[0].strip()
                if code:
                    codes.append(code)
            elif isinstance(item, dict):
                code = str(item.get("code") or item.get("id") or "").strip()
                if code:
                    codes.append(code)
    return codes


def verification_allows_achieve(
    verification: dict[str, Any] | None,
    *,
    goal_scale: str | None,
    has_preview: bool,
) -> tuple[bool, str]:
    """Return (allowed, reason). PASS always; SMALL+preview may soft-pass non-blocking gaps."""
    payload = dict(verification or {})
    verdict = str(payload.get("verdict") or "").upper()
    if verdict == "PASS":
        return True, "pass"
    small = str(goal_scale or "").upper() == "SMALL"
    if not small or not has_preview:
        return False, verdict or "MISSING"
    codes = _gap_codes(payload)
    if any(code in BLOCKING_DELIVERY_GAP_CODES for code in codes):
        return False, "blocking_gaps"
    # Preview exists and no hard blockers → treat as delivered-for-review success.
    return True, "soft_pass_preview"
