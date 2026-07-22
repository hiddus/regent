from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    LEASE_CONFLICT = "LEASE_CONFLICT"
    LEASE_LOST = "LEASE_LOST"
    NOT_FOUND = "NOT_FOUND"
    INVALID_STATE = "INVALID_STATE"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    ACTIVE_RUN_EXISTS = "ACTIVE_RUN_EXISTS"
    PERMIT_REQUIRED = "PERMIT_REQUIRED"
    PERMIT_INVALID = "PERMIT_INVALID"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    GOAL_TERMINAL = "GOAL_TERMINAL"
    POLICY_DENIED = "POLICY_DENIED"


@dataclass(frozen=True, slots=True)
class DomainError(Exception):
    code: ErrorCode
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"
