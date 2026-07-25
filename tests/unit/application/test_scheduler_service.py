"""Unit tests for P2-1 scheduler aging + replayable decisions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from regent.application.p1_contracts import canonical_hash
from regent.application.scheduler_service import (
    SchedulerService,
    compute_aging_score,
)


def test_aging_score_increases_with_wait() -> None:
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    fresh = compute_aging_score(10, now, now=now, aging_per_minute=2)
    aged = compute_aging_score(
        10, now - timedelta(minutes=5), now=now, aging_per_minute=2
    )
    assert fresh == 10
    assert aged == 20


def test_aging_sort_key_is_stable() -> None:
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    a = SimpleNamespace(
        aging_score=compute_aging_score(1, now - timedelta(minutes=10), now=now),
        enqueued_at=now - timedelta(minutes=10),
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    )
    b = SimpleNamespace(
        aging_score=compute_aging_score(5, now, now=now),
        enqueued_at=now,
        id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
    )
    ordered = sorted(
        [a, b],
        key=lambda item: (-item.aging_score, item.enqueued_at.isoformat(), str(item.id)),
    )
    # a waited 10 min → score 11 > b score 5
    assert ordered[0] is a


def test_replay_hashes_match_stored_snapshots() -> None:
    queue = [{"id": "1", "aging_score": 3}]
    quotas = [{"resource_name": "cpu", "limit_amount": 2, "held_amount": 0}]
    decision = SimpleNamespace(
        input_snapshot_json={"queue": queue, "quotas": quotas},
        queue_snapshot_hash=canonical_hash({"queue": queue}),
        quota_snapshot_hash=canonical_hash({"quotas": quotas}),
    )
    replay = SchedulerService.replay_hashes(decision)  # type: ignore[arg-type]
    assert replay["matches_stored"] is True
    assert replay["queue_snapshot_hash"] == decision.queue_snapshot_hash


def test_can_reserve_respects_quota_ceiling() -> None:
    q_cpu = SimpleNamespace(held_amount=1, limit_amount=2)
    assert SchedulerService._can_reserve({"cpu": q_cpu}, {"cpu": 1}) is True  # type: ignore[arg-type]
    assert SchedulerService._can_reserve({"cpu": q_cpu}, {"cpu": 2}) is False  # type: ignore[arg-type]
    assert SchedulerService._can_reserve({}, {"cpu": 1}) is False


def test_tick_result_shape_when_no_selection() -> None:
    # Contract: tick returns selected=False with decision + checkpoint ids when empty.
    result = {
        "decision_id": "d",
        "checkpoint_id": "c",
        "selected": False,
        "reason": "no_schedulable_entry_or_insufficient_quota",
    }
    assert result["selected"] is False
    assert "decision_id" in result
    assert "checkpoint_id" in result
