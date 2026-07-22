from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class LongTaskStatus(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"
    DEAD_LETTER = "DEAD_LETTER"


class FailureDisposition(StrEnum):
    RETRYABLE = "RETRYABLE"
    TERMINAL = "TERMINAL"
    UNKNOWN = "UNKNOWN"


class LongTaskCommand(BaseModel):
    task_id: str = Field(min_length=1)
    command_type: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)


class LongTaskProgress(BaseModel):
    task_id: str = Field(min_length=1)
    status: LongTaskStatus
    completed_units: int = Field(default=0, ge=0)
    total_units: int | None = Field(default=None, ge=0)
    message: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


LongTaskHandler = Callable[[LongTaskCommand], Awaitable[LongTaskProgress]]


class LongTaskHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], LongTaskHandler] = {}

    def register(self, command_type: str, schema_version: str, handler: LongTaskHandler) -> None:
        key = (command_type, schema_version)
        if key in self._handlers:
            raise ValueError(f"handler already registered: {key}")
        self._handlers[key] = handler

    def resolve(self, command: LongTaskCommand) -> LongTaskHandler:
        key = (command.command_type, command.schema_version)
        try:
            return self._handlers[key]
        except KeyError as exc:
            raise LookupError(f"no handler registered: {key}") from exc


def retry_disposition(
    status: LongTaskStatus, attempt: int, max_attempts: int
) -> FailureDisposition:
    if status is LongTaskStatus.UNKNOWN:
        return FailureDisposition.UNKNOWN
    if status is LongTaskStatus.FAILED and attempt + 1 < max_attempts:
        return FailureDisposition.RETRYABLE
    return FailureDisposition.TERMINAL
