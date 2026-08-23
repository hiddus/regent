"""Tests for behavior_repair_loop — observation-to-repair connection."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from regent.application.behavior_repair_loop import (
    _SEVERITY_ORDER,
    BehaviorRepairLoop,
    RepairDecision,
)


class TestRepairDecision:
    def test_as_dict(self) -> None:
        d = RepairDecision(
            action="REPAIR",
            reason="test",
            anomalies_injected=3,
            steering_text="fix this",
        ).as_dict()
        assert d["action"] == "REPAIR"
        assert d["anomalies_injected"] == 3

    def test_as_dict_includes_retrigger_fields(self) -> None:
        d = RepairDecision(
            action="REPAIR",
            reason="test",
            retriggered=True,
            retrigger_reason="execution re-queued (stage=QUEUED)",
        ).as_dict()
        assert d["retriggered"] is True
        assert "re-queued" in d["retrigger_reason"]


class TestBuildSteeringText:
    def setup_method(self) -> None:
        self.loop = BehaviorRepairLoop()

    def test_steering_contains_anomaly_details(self) -> None:
        anomalies = [
            {
                "metric_name": "dialogue_time_distribution",
                "detail": "深夜对话占比 80%，角色在户外",
                "severity": "MEDIUM",
            },
            {
                "metric_name": "world_background",
                "detail": "世界背景要素不足（检测到 1/4）",
                "severity": "MEDIUM",
            },
        ]
        text = self.loop._build_steering_text(anomalies)
        assert "dialogue_time_distribution" in text
        assert "world_background" in text
        assert "运行时行为监控" in text

    def test_steering_caps_at_six(self) -> None:
        anomalies = [
            {"metric_name": f"metric_{i}", "detail": f"detail_{i}", "severity": "MEDIUM"}
            for i in range(10)
        ]
        text = self.loop._build_steering_text(anomalies)
        # Should include at most 6 anomalies
        assert text.count("metric_") <= 6


class TestSeverityOrder:
    def test_ordering(self) -> None:
        assert _SEVERITY_ORDER["NONE"] < _SEVERITY_ORDER["LOW"]
        assert _SEVERITY_ORDER["LOW"] < _SEVERITY_ORDER["MEDIUM"]
        assert _SEVERITY_ORDER["MEDIUM"] < _SEVERITY_ORDER["HIGH"]


class TestEvaluateAndRepair:
    @pytest.mark.asyncio
    async def test_no_anomalies_returns_no_action(
        self, async_session_factory
    ) -> None:
        loop = BehaviorRepairLoop()
        observations = [
            {
                "anomaly": False,
                "severity": "NONE",
                "metric_name": "test",
                "detail": "ok",
            }
        ]
        decision = await loop.evaluate_and_repair(
            async_session_factory, uuid.uuid4(), observations
        )
        assert decision.action == "NO_ACTION"

    @pytest.mark.asyncio
    async def test_low_severity_returns_no_action(
        self, async_session_factory
    ) -> None:
        loop = BehaviorRepairLoop()
        observations = [
            {
                "anomaly": True,
                "severity": "LOW",
                "metric_name": "test",
                "detail": "minor issue",
            }
        ]
        decision = await loop.evaluate_and_repair(
            async_session_factory, uuid.uuid4(), observations
        )
        assert decision.action == "NO_ACTION"


# Fixture for async tests that need a session factory.
@pytest.fixture
def async_session_factory():
    """Mock session factory — returns None for goal lookup (goal not found)."""

    class _MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args, **kwargs):
            return None

    class _MockFactory:
        def __call__(self):
            return _MockSession()

    return _MockFactory()


# ---------------------------------------------------------------------------
# Execution re-trigger (bounded auto-repair)
# ---------------------------------------------------------------------------

_MEDIUM_ANOMALY = [
    {
        "anomaly": True,
        "severity": "MEDIUM",
        "metric_name": "content_volume",
        "detail": "页面可见文本仅 12 字符，可能为空白壳",
    }
]


class _FakeExecutionReceipt:
    def __init__(self) -> None:
        self.goal_id = None
        self.project_id = None
        self.status = "ACTIVE"
        self.stage = "QUEUED"
        self.event_id = uuid.uuid4()


class _FakeGoalExecutionService:
    """Records start() calls instead of running the real pipeline."""

    instances: ClassVar[list[_FakeGoalExecutionService]] = []

    def __init__(self, sessions) -> None:
        self.sessions = sessions
        self.calls: list[dict] = []
        _FakeGoalExecutionService.instances.append(self)

    async def start(self, goal_id, *, actor, idempotency_key):
        self.calls.append(
            {
                "goal_id": goal_id,
                "actor": actor,
                "idempotency_key": idempotency_key,
            }
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


class TestRetriggerExecution:
    @pytest.mark.asyncio
    async def test_repair_retriggers_execution(
        self, db_sessions, fake_execution_service
    ) -> None:
        goal = await _make_goal(
            db_sessions,
            metadata={
                "org_mode": {
                    "enable_monitoring": True,
                    "enable_repair_loop": True,
                    "max_iterations": 3,
                }
            },
        )
        decision = await BehaviorRepairLoop().evaluate_and_repair(
            db_sessions,
            goal.id,
            _MEDIUM_ANOMALY,
            retrigger_execution=True,
        )
        assert decision.action == "REPAIR"
        assert decision.retriggered is True
        assert "re-queued" in decision.retrigger_reason
        # Execution service got the re-trigger with the guidance-continue channel.
        svc = fake_execution_service.instances[-1]
        assert len(svc.calls) == 1
        call = svc.calls[0]
        assert call["goal_id"] == goal.id
        assert call["actor"] == "regent-behavior-repair"
        assert call["idempotency_key"].startswith(
            f"guidance-continue:behavior-repair:{goal.id}:"
        )
        # Steering was persisted on the goal.
        async with db_sessions() as session:
            from regent.infrastructure.models import GoalModel

            refreshed = await session.get(GoalModel, goal.id)
            assert "运行时行为监控" in (refreshed.metadata_json or {}).get(
                "session_steer_brief", ""
            )
            assert (refreshed.metadata_json or {}).get("behavior_repair_history")

    @pytest.mark.asyncio
    async def test_retrigger_disabled_by_default(
        self, db_sessions, fake_execution_service
    ) -> None:
        goal = await _make_goal(db_sessions)
        decision = await BehaviorRepairLoop().evaluate_and_repair(
            db_sessions, goal.id, _MEDIUM_ANOMALY
        )
        assert decision.action == "REPAIR"
        assert decision.retriggered is False
        assert decision.retrigger_reason == "retrigger disabled"
        assert not any(svc.calls for svc in fake_execution_service.instances)

    @pytest.mark.asyncio
    async def test_retrigger_skipped_when_iteration_cap_reached(
        self, db_sessions, fake_execution_service
    ) -> None:
        two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        goal = await _make_goal(
            db_sessions,
            metadata={
                "org_mode": {
                    "enable_repair_loop": True,
                    "max_iterations": 2,
                },
                "behavior_repair_history": [
                    {"repaired_at": two_hours_ago},
                    {"repaired_at": two_hours_ago},
                ],
            },
        )
        decision = await BehaviorRepairLoop().evaluate_and_repair(
            db_sessions, goal.id, _MEDIUM_ANOMALY, retrigger_execution=True
        )
        assert decision.action == "REPAIR"
        assert decision.retriggered is False
        assert "cap" in decision.retrigger_reason
        assert not any(svc.calls for svc in fake_execution_service.instances)

    @pytest.mark.asyncio
    async def test_retrigger_skipped_when_goal_not_active(
        self, db_sessions, fake_execution_service
    ) -> None:
        goal = await _make_goal(db_sessions, status="ACHIEVED")
        decision = await BehaviorRepairLoop().evaluate_and_repair(
            db_sessions, goal.id, _MEDIUM_ANOMALY, retrigger_execution=True
        )
        assert decision.action == "REPAIR"
        assert decision.retriggered is False
        assert "ACHIEVED" in decision.retrigger_reason
        assert not any(svc.calls for svc in fake_execution_service.instances)

    @pytest.mark.asyncio
    async def test_retrigger_skipped_when_run_live(
        self, db_sessions, fake_execution_service
    ) -> None:
        from regent.infrastructure.models import RunModel, WorkModel

        goal = await _make_goal(db_sessions)
        async with db_sessions() as session:
            work = WorkModel(
                id=uuid.uuid4(),
                goal_id=goal.id,
                purpose="deliver",
                input_refs=[],
                acceptance_criteria={},
                dependency_ids=[],
                priority=0,
                budget={},
                status="PLANNED",
                version=0,
                correlation_id=goal.correlation_id,
            )
            session.add(work)
            await session.flush()
            session.add(
                RunModel(
                    id=uuid.uuid4(),
                    work_id=work.id,
                    status="RUNNING",
                    version=0,
                    actor_id="test",
                    input_version="0",
                    idempotency_key=f"run-{uuid.uuid4()}",
                    correlation_id=goal.correlation_id,
                )
            )
            await session.commit()

        decision = await BehaviorRepairLoop().evaluate_and_repair(
            db_sessions, goal.id, _MEDIUM_ANOMALY, retrigger_execution=True
        )
        assert decision.action == "REPAIR"
        assert decision.retriggered is False
        assert "live run" in decision.retrigger_reason
        assert not any(svc.calls for svc in fake_execution_service.instances)

    @pytest.mark.asyncio
    async def test_retrigger_skipped_when_budget_blocked(
        self, db_sessions, fake_execution_service
    ) -> None:
        goal = await _make_goal(db_sessions)

        class _BlockedLedger:
            async def check_budget_limit(self, goal_id):
                from types import SimpleNamespace

                return SimpleNamespace(is_blocked=True, total_cost=99.0, limit=10.0)

        decision = await BehaviorRepairLoop().evaluate_and_repair(
            db_sessions,
            goal.id,
            _MEDIUM_ANOMALY,
            budget_ledger=_BlockedLedger(),
            retrigger_execution=True,
        )
        assert decision.action == "REPAIR"
        assert decision.retriggered is False
        assert "budget blocked" in decision.retrigger_reason
        assert not any(svc.calls for svc in fake_execution_service.instances)


# ---------------------------------------------------------------------------
# Concurrency hardening: steering merge + retrigger claim
# ---------------------------------------------------------------------------

_USER_STEER = "用户最新指令：把首页改成深色主题，并增加价格表"


class TestSteeringMerge:
    @pytest.mark.asyncio
    async def test_user_steering_preserved_and_comes_first(
        self, db_sessions, fake_execution_service
    ) -> None:
        goal = await _make_goal(
            db_sessions,
            metadata={"session_steer_brief": _USER_STEER},
        )
        decision = await BehaviorRepairLoop().evaluate_and_repair(
            db_sessions, goal.id, _MEDIUM_ANOMALY, retrigger_execution=True
        )
        assert decision.action == "REPAIR"
        async with db_sessions() as session:
            from regent.infrastructure.models import GoalModel

            refreshed = await session.get(GoalModel, goal.id)
            meta = refreshed.metadata_json or {}
            brief = meta.get("session_steer_brief", "")
        # User steering survives, appears BEFORE the repair note.
        assert _USER_STEER in brief
        assert "运行时行为监控" in brief
        assert brief.index(_USER_STEER) < brief.index("运行时行为监控")
        # Own-brief marker lets the next repair replace only its own note.
        assert meta.get("behavior_repair_own_brief")

    @pytest.mark.asyncio
    async def test_second_repair_replaces_own_note_not_accumulates(
        self, db_sessions, fake_execution_service
    ) -> None:
        goal = await _make_goal(
            db_sessions,
            metadata={"session_steer_brief": _USER_STEER},
        )
        await BehaviorRepairLoop().evaluate_and_repair(
            db_sessions, goal.id, _MEDIUM_ANOMALY, retrigger_execution=True
        )
        # Age the repair history so the cooldown lets a second repair in.
        two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        async with db_sessions() as session:
            from regent.infrastructure.models import GoalModel

            g = await session.get(GoalModel, goal.id)
            meta = dict(g.metadata_json or {})
            meta["behavior_repair_history"] = [
                {**h, "repaired_at": two_hours_ago}
                for h in meta.get("behavior_repair_history", [])
            ]
            g.metadata_json = meta
            await session.commit()

        second = await BehaviorRepairLoop().evaluate_and_repair(
            db_sessions, goal.id, _MEDIUM_ANOMALY, retrigger_execution=True
        )
        assert second.action == "REPAIR"
        async with db_sessions() as session:
            from regent.infrastructure.models import GoalModel

            refreshed = await session.get(GoalModel, goal.id)
            brief = (refreshed.metadata_json or {}).get("session_steer_brief", "")
        # User note once + latest repair note once — no accumulation of
        # the first repair note.
        assert brief.count("运行时行为监控") == 1
        assert brief.count(_USER_STEER) == 1
        assert brief.index(_USER_STEER) < brief.index("运行时行为监控")


class TestRetriggerClaim:
    @pytest.mark.asyncio
    async def test_fresh_claim_blocks_second_retrigger(
        self, db_sessions, fake_execution_service
    ) -> None:
        goal = await _make_goal(
            db_sessions,
            metadata={
                "behavior_repair_retrigger_claim": {
                    "at": datetime.now(UTC).isoformat(),
                    "actor": "regent-behavior-repair",
                }
            },
        )
        decision = await BehaviorRepairLoop().evaluate_and_repair(
            db_sessions, goal.id, _MEDIUM_ANOMALY, retrigger_execution=True
        )
        assert decision.action == "REPAIR"
        assert decision.retriggered is False
        assert "claim" in decision.retrigger_reason
        assert not any(svc.calls for svc in fake_execution_service.instances)
        # Steering is still injected even when the claim is held.
        async with db_sessions() as session:
            from regent.infrastructure.models import GoalModel

            refreshed = await session.get(GoalModel, goal.id)
            assert "运行时行为监控" in (
                refreshed.metadata_json or {}
            ).get("session_steer_brief", "")

    @pytest.mark.asyncio
    async def test_stale_claim_allows_retrigger(
        self, db_sessions, fake_execution_service
    ) -> None:
        stale = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        goal = await _make_goal(
            db_sessions,
            metadata={
                "behavior_repair_retrigger_claim": {
                    "at": stale,
                    "actor": "regent-behavior-repair",
                }
            },
        )
        decision = await BehaviorRepairLoop().evaluate_and_repair(
            db_sessions, goal.id, _MEDIUM_ANOMALY, retrigger_execution=True
        )
        assert decision.retriggered is True
        assert any(svc.calls for svc in fake_execution_service.instances)

    @pytest.mark.asyncio
    async def test_claim_cleared_after_start(
        self, db_sessions, fake_execution_service
    ) -> None:
        goal = await _make_goal(db_sessions)
        decision = await BehaviorRepairLoop().evaluate_and_repair(
            db_sessions, goal.id, _MEDIUM_ANOMALY, retrigger_execution=True
        )
        assert decision.retriggered is True
        async with db_sessions() as session:
            from regent.infrastructure.models import GoalModel

            refreshed = await session.get(GoalModel, goal.id)
            meta = refreshed.metadata_json or {}
        assert "behavior_repair_retrigger_claim" not in meta

    @pytest.mark.asyncio
    async def test_claim_cleared_even_when_budget_blocked(
        self, db_sessions, fake_execution_service
    ) -> None:
        goal = await _make_goal(db_sessions)

        class _BlockedLedger:
            async def check_budget_limit(self, goal_id):
                from types import SimpleNamespace

                return SimpleNamespace(is_blocked=True, total_cost=99.0, limit=10.0)

        decision = await BehaviorRepairLoop().evaluate_and_repair(
            db_sessions,
            goal.id,
            _MEDIUM_ANOMALY,
            budget_ledger=_BlockedLedger(),
            retrigger_execution=True,
        )
        assert decision.retriggered is False
        async with db_sessions() as session:
            from regent.infrastructure.models import GoalModel

            refreshed = await session.get(GoalModel, goal.id)
            meta = refreshed.metadata_json or {}
        assert "behavior_repair_retrigger_claim" not in meta
