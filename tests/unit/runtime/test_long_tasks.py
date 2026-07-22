import pytest
from regent.runtime.long_tasks import (
    FailureDisposition,
    LongTaskCommand,
    LongTaskHandlerRegistry,
    LongTaskStatus,
    retry_disposition,
)


async def handler(command: LongTaskCommand):
    return None


def test_registry_is_versioned() -> None:
    registry = LongTaskHandlerRegistry()
    registry.register("discover", "v1", handler)
    command = LongTaskCommand(
        task_id="1",
        command_type="discover",
        schema_version="v1",
        idempotency_key="i",
        correlation_id="c",
    )
    assert registry.resolve(command) is handler
    with pytest.raises(ValueError):
        registry.register("discover", "v1", handler)


def test_retry_policy_preserves_unknown() -> None:
    assert retry_disposition(LongTaskStatus.UNKNOWN, 0, 3) is FailureDisposition.UNKNOWN
    assert retry_disposition(LongTaskStatus.FAILED, 0, 3) is FailureDisposition.RETRYABLE
    assert retry_disposition(LongTaskStatus.FAILED, 2, 3) is FailureDisposition.TERMINAL
