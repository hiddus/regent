"""GAC-C2 / GAC-C3 smoke checks for timer command and scheduler table wiring."""

from __future__ import annotations

from regent.infrastructure.models import ExecutionQueueEntryModel
from regent.runtime.timers import DurableTimerService


def test_scheduler_model_uses_execution_queue_entries() -> None:
    """GAC-C3: real table is execution_queue_entries (migration 0025), not scheduler_*."""
    assert ExecutionQueueEntryModel.__tablename__ == "execution_queue_entries"


def test_durable_timer_service_exposes_schedule_cancel() -> None:
    """GAC-C2 relies on DurableTimer schedule/cancel for gate insufficient timeout."""
    assert callable(DurableTimerService.schedule)
    assert callable(DurableTimerService.cancel)
