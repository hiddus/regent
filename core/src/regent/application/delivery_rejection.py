"""Typed delivery rejection — replaces magic-string ValueError contracts."""

from __future__ import annotations

from typing import Any

from regent.domain.errors import DomainError, ErrorCode


class DeliveryRejection(DomainError):
    """Structured delivery-review / verification failure for recovery routing."""

    def __init__(
        self,
        *,
        reasons: list[str],
        gap_kind: str | None = None,
        draft_uri: str | None = None,
        producer_ref: str | None = None,
        message: str = "rejected non-deliverable surface",
        code: ErrorCode = ErrorCode.DELIVERY_REJECTED,
        retryable: bool = False,
    ) -> None:
        clean = [str(r).strip() for r in reasons if str(r).strip()][:12]
        if not clean:
            clean = [message]
        details: dict[str, Any] = {
            "reasons": clean,
            "gap_kind": gap_kind,
            "draft_uri": draft_uri,
            "producer_ref": producer_ref,
            "retryable": retryable,
        }
        # Keep legacy substring so older log greps still match.
        legacy = f"delivery-review-v1 rejected non-deliverable surface: {'; '.join(clean)}"
        super().__init__(code, legacy, details=details)
        self.reasons = clean
        self.gap_kind = gap_kind
        self.draft_uri = draft_uri
        self.producer_ref = producer_ref
        self.retryable = retryable


def reasons_from_exception(exc: BaseException) -> list[str]:
    """Extract gap reasons from a typed DeliveryRejection.

    Orchestrator recovery must branch on ``isinstance(exc, DeliveryRejection)``
    (TS §13.8.3). Legacy substring parsing below is only a defensive fallback
    for reason text extraction — never a routing contract.
    """
    if isinstance(exc, DeliveryRejection):
        return list(exc.reasons)
    text = str(exc)
    if "rejected non-deliverable surface:" in text:
        return [
            part.strip()
            for part in text.split("rejected non-deliverable surface:", 1)[-1].split(";")
            if part.strip()
        ][:12] or [text[:200]]
    return [text[:200]]
