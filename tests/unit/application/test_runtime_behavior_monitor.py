"""Tests for runtime_behavior_monitor — independent observation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from regent.application.runtime_behavior_monitor import (
    BehaviorObservation,
    RuntimeBehaviorMonitor,
    _visible_text,
)


class TestVisibleText:
    def test_strips_html_tags(self) -> None:
        assert _visible_text("<p>Hello <b>world</b></p>") == "Hello world"

    def test_collapses_whitespace(self) -> None:
        assert _visible_text("  hello   \n  world  ") == "hello world"

    def test_empty_input(self) -> None:
        assert _visible_text("") == ""


class TestBehaviorObservation:
    def test_as_dict(self) -> None:
        obs = BehaviorObservation(
            goal_id=uuid.uuid4(),
            observed_at=datetime.now(UTC),
            metric_name="test",
            metric_value={"key": "value"},
            anomaly=True,
            severity="MEDIUM",
            detail="test detail",
        )
        d = obs.as_dict()
        assert d["metric_name"] == "test"
        assert d["anomaly"] is True
        assert d["severity"] == "MEDIUM"


class TestRuntimeBehaviorMonitor:
    def setup_method(self) -> None:
        self.monitor = RuntimeBehaviorMonitor()
        self.goal_id = uuid.uuid4()

    def test_content_volume_low(self) -> None:
        obs = self.monitor._check_content_volume(
            self.goal_id, "http://test", "short"
        )
        assert obs.anomaly is True
        assert obs.severity == "MEDIUM"

    def test_content_volume_ok(self) -> None:
        obs = self.monitor._check_content_volume(
            self.goal_id, "http://test", "x" * 300
        )
        assert obs.anomaly is False
        assert obs.severity == "NONE"

    def test_dialogue_night_outdoor_anomaly(self) -> None:
        text = "深夜 23:30 小明在公园里散步 凌晨 00:15 还在户外 午夜时分 街道上 广场中"
        obs_list = self.monitor._check_dialogue_realism(
            self.goal_id, "http://test", text
        )
        anomalies = [o for o in obs_list if o.anomaly]
        assert len(anomalies) >= 1
        assert any(o.metric_name == "dialogue_time_distribution" for o in anomalies)

    def test_dialogue_no_time_refs(self) -> None:
        text = "小明和小红在聊天，讨论天气和美食"
        obs_list = self.monitor._check_dialogue_realism(
            self.goal_id, "http://test", text
        )
        assert len(obs_list) == 1
        assert obs_list[0].severity == "LOW"

    def test_content_repetition_detected(self) -> None:
        phrase = "这是一个测试段落用来检测重复内容的功能"
        text = "。".join([phrase] * 5)
        obs_list = self.monitor._check_character_diversity(
            self.goal_id, "http://test", text
        )
        assert len(obs_list) >= 1
        assert obs_list[0].metric_name == "content_repetition"

    def test_world_background_insufficient(self) -> None:
        text = "一些普通的内容描述"
        obs_list = self.monitor._check_world_background(
            self.goal_id, "http://test", text, "<html></html>"
        )
        assert len(obs_list) == 1
        assert obs_list[0].anomaly is True
        assert "世界背景要素不足" in obs_list[0].detail

    def test_world_background_sufficient(self) -> None:
        text = "在这个宁静的小镇，现代与传统交融。人们遵循着古老的习俗，享受着当代的便利。"
        obs_list = self.monitor._check_world_background(
            self.goal_id, "http://test", text, "<html></html>"
        )
        # Should have 3+ elements: location(小镇), time(现代/当代), atmosphere(宁静), rules(习俗)
        anomalies = [o for o in obs_list if o.anomaly]
        assert len(anomalies) == 0

    @pytest.mark.asyncio
    async def test_observe_empty_url(self) -> None:
        result = await self.monitor.observe(self.goal_id, "")
        assert result == []
