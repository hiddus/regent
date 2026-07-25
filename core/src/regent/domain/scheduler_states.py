"""P2-1 scheduler domain states (appendix §11 / §13)."""

from __future__ import annotations

from enum import StrEnum


class QueueEntryState(StrEnum):
    QUEUED = "QUEUED"
    SCHEDULED = "SCHEDULED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ReservationState(StrEnum):
    REQUESTED = "REQUESTED"
    HELD = "HELD"
    RELEASED = "RELEASED"
    PREEMPTED = "PREEMPTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class LedgerEntryType(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"
