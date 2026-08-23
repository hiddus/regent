"""Tests for behavior_monitor_tick — worker-side periodic monitoring."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from regent.application import behavior_monitor_tick
from regent.application.behavior_monitor_tick import tick_behavior_monitoring
from regent.application.runtime_behavior_monitor import BehaviorObservation


class _FakeExecutionReceipt:
    def __init__(self) -> None:
        self.goal_id = None
        self.project_id = None
        self.status = "ACTIVE"
        self.stage = "QUEUED"
        self.event_id = uuid.uuid4()


class _FakeGoalExecutionService:
    instances: list[_FakeGoalExecutionService] = []

    def __init__(self, sessions) -> None:
        self.sessions = sessions
        self.calls: list[dict] = []
        _FakeGoalExecutionService.instances.append(self)

    async def start(self, goal_id, *, actor, idempotency_key):
        self.calls.append(
            {"goal_id": goal_id, "actor": actor, "idempotency_key": idempotency_key}
        )
        return _FakeExecutionReceipt()

    @classmethod
    def reset(cls) -> None:
        cls.instances = []


@pytest.fixture
def fake_execution_service(monkeypatch):
    _FakeGoalExecutionService.reset()
    monkeypatch.setattr(
        "regent.application.goal_execution_service.GoalExecutionService",
        _FakeGoalExecutionService,
    )
    return _FakeGoalExecutionService


@pytest.fixture
def fake_monitor(monkeypatch):
    """Replace RuntimeBehaviorMonitor.observe with a canned anomaly."""
    observed: list[str] = []

    class _FakeMonitor:
        async def observe(self, goal_id, preview_url, *, goal_profile=None):
            observed.append(preview_url)
            return [
                BehaviorObservation(
                    goal_id=goal_id,
                    observed_at=datetime.now(UTC),
                    metric_name="content_volume",
                    metric_value={"visible_chars": 12},
                    anomaly=True,
                    severity="MEDIUM",
                    detail="页面可见文本仅 12 字符，可能为空白壳",
                    preview_url=preview_url,
                )
            ]

    monkeypatch.setattr(
        "regent.application.runtime_behavior_monitor.RuntimeBehaviorMonitor",
        _FakeMonitor,
    )
    return observed


async def _make_goal(sessions, *, status="ACTIVE", metadata=None):
    from types import SimpleNamespace

    from regent.infrastructure.models import AppProjectModel, GoalModel

    project_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    correlation_id = uuid.uuid4()
    async with sessions() as session:
        session.add(
            AppProjectModel(
                id=project_id,
                name="test-project",
                product_intent="test",
                status="ACTIVE",
                created_by="test",
            )
        )
        session.add(
            GoalModel(
                id=goal_id,
                app_project_id=project_id,
                original_input="interactive town simulation",
                status=status,
                version=1,
                created_by="test",
                correlation_id=correlation_id,
                metadata_json=dict(metadata or {}),
            )
        )
        await session.commit()
    return SimpleNamespace(id=goal_id, correlation_id=correlation_id)


_MONITORING_META = {
    "org_mode": {
        "enable_monitoring": True,
        "enable_repair_loop": True,
        "max_iterations": 3,
    },
    "goal_profile": {"domain": "interactive-app"},
    "behavior_monitor_preview_url": "http://preview.test/app-a",
}


class TestTickBehaviorMonitoring:
    @pytest.mark.asyncio
    async def test_tick_observes_due_goal_and_repairs(
        self, db_sessions, fake_monitor, fake_execution_service
    ) -> None:
        goal = await _make_goal(db_sessions, metadata=_MONITORING_META)

        stats = await tick_behavior_monitoring(db_sessions)

        assert stats["scanned"] == 1
        assert stats["monitored"] == 1
        assert stats["observed"] == 1
        assert fake_monitor == ["http://preview.test/app-a"]

        # Repair ran and re-triggered execution through the fake service.
        svc = fake_execution_service.instances[-1]
        assert len(svc.calls) == 1
        assert svc.calls[0]["goal_id"] == goal.id
        assert svc.calls[0]["idempotency_key"].startswith(
            f"guidance-continue:behavior-repair:{goal.id}:"
        )

        # Observation + steering persisted on the goal.
        async with db_sessions() as session:
            from regent.infrastructure.models import GoalModel

            refreshed = await session.get(GoalModel, goal.id)
            meta = refreshed.metadata_json or {}
            assert meta.get("behavior_monitor_ran_at")
            assert "session_steer_brief" in meta

    @pytest.mark.asyncio
    async def test_tick_skips_recently_observed_goal(self, db_sessions, fake_monitor) -> None:
        await _make_goal(
            db_sessions,
            metadata={
                **_MONITORING_META,
                "behavior_monitor_ran_at": datetime.now(UTC).isoformat(),
            },
        )
        stats = await tick_behavior_monitoring(db_sessions)
        assert stats["monitored"] == 0
        assert stats["observed"] == 0
        assert fake_monitor == []

    @pytest.mark.asyncio
    async def test_tick_skips_goal_without_monitoring_flag(
        self, db_sessions, fake_monitor
    ) -> None:
        await _make_goal(
            db_sessions,
            metadata={
                "behavior_monitor_preview_url": "http://preview.test/app-b",
            },
        )
        stats = await tick_behavior_monitoring(db_sessions)
        assert stats["monitored"] == 0
        assert fake_monitor == []

    @pytest.mark.asyncio
    async def test_tick_skips_goal_without_preview_url(
        self, db_sessions, fake_monitor
    ) -> None:
        await _make_goal(
            db_sessions,
            metadata={"org_mode": {"enable_monitoring": True}},
        )
        stats = await tick_behavior_monitoring(db_sessions)
        assert stats["monitored"] == 0
        assert fake_monitor == []

    @pytest.mark.asyncio
    async def test_tick_ignores_non_active_goals(self, db_sessions, fake_monitor) -> None:
        await _make_goal(
            db_sessions,
            status="ACHIEVED",
            metadata=_MONITORING_META,
        )
        stats = await tick_behavior_monitoring(db_sessions)
        assert stats["scanned"] == 0
        assert fake_monitor == []

    @pytest.mark.asyncio
    async def test_tick_observes_without_repair_when_loop_disabled(
        self, db_sessions, fake_monitor, fake_execution_service
    ) -> None:
        meta = {
            **_MONITORING_META,
            "org_mode": {"enable_monitoring": True, "enable_repair_loop": False},
        }
        await _make_goal(db_sessions, metadata=meta)
        stats = await tick_behavior_monitoring(db_sessions)
        assert stats["observed"] == 1
        assert fake_monitor == ["http://preview.test/app-a"]
        # No repair service was constructed for this goal.
        assert not any(svc.calls for svc in fake_execution_service.instances)


class TestMinIntervalParsing:
    def test_parse_iso_handles_naive_and_invalid(self) -> None:
        assert behavior_monitor_tick._parse_iso(None) is None
        assert behavior_monitor_tick._parse_iso("garbage") is None
        parsed = behavior_monitor_tick._parse_iso("2026-08-23T10:00:00")
        assert parsed is not None and parsed.tzinfo is not None
