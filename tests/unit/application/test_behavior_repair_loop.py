"""Tests for behavior_repair_loop — observation-to-repair connection."""

from __future__ import annotations

import uuid

import pytest

from regent.application.behavior_repair_loop import (
    BehaviorRepairLoop,
    RepairDecision,
    _SEVERITY_ORDER,
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
