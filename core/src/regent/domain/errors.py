from enum import StrEnum


class ErrorCode(StrEnum):
    LEASE_CONFLICT = "LEASE_CONFLICT"
    LEASE_LOST = "LEASE_LOST"
    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    INVALID_STATE = "INVALID_STATE"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    ACTIVE_RUN_EXISTS = "ACTIVE_RUN_EXISTS"
    PERMIT_REQUIRED = "PERMIT_REQUIRED"
    PERMIT_INVALID = "PERMIT_INVALID"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    GOAL_TERMINAL = "GOAL_TERMINAL"
    POLICY_DENIED = "POLICY_DENIED"
    # AAR-1 Foundation
    NO_ACTIVE_CONSTITUTION = "NO_ACTIVE_CONSTITUTION"
    POLICY_EVALUATION_FAILED = "POLICY_EVALUATION_FAILED"
    NO_FEASIBLE_ORGANIZATION = "NO_FEASIBLE_ORGANIZATION"
    STALE_ORGANIZATION_VERSION = "STALE_ORGANIZATION_VERSION"
    INVALID_AGENT_LIFECYCLE_TRANSITION = "INVALID_AGENT_LIFECYCLE_TRANSITION"
    CAPABILITY_SCOPE_ESCALATION = "CAPABILITY_SCOPE_ESCALATION"
    STALE_LEASE = "STALE_LEASE"
    ENVELOPE_TAMPERED = "ENVELOPE_TAMPERED"
    ENVELOPE_EXPIRED = "ENVELOPE_EXPIRED"
    ENVELOPE_REPLAYED = "ENVELOPE_REPLAYED"
    MCP_SERVER_NOT_CERTIFIED = "MCP_SERVER_NOT_CERTIFIED"
    EXTERNAL_EFFECT_UNKNOWN = "EXTERNAL_EFFECT_UNKNOWN"


class DomainError(Exception):
    """Domain-level error carrying a machine-readable ErrorCode."""

    code: ErrorCode
    message: str

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def __repr__(self) -> str:
        return f"DomainError({self.code!r}, {self.message!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DomainError):
            return NotImplemented
        return (self.code, self.message) == (other.code, other.message)

    def __hash__(self) -> int:
        return hash((self.code, self.message))
