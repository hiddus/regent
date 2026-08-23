"""Tests for organization_mode_selector — goal-to-mode matching."""

from __future__ import annotations

from regent.application.goal_classifier import GoalProfile
from regent.application.organization_mode_selector import (
    AGILE,
    BATCH,
    HUB_SPOKE,
    WATERFALL,
    select_mode,
    select_mode_from_metadata,
)


class TestSelectMode:
    def test_data_pipeline_goes_batch(self) -> None:
        profile = GoalProfile(domain="data-pipeline", scale="MEDIUM")
        mode = select_mode(profile)
        assert mode.mode_id == "batch"

    def test_interactive_app_goes_hub_spoke(self) -> None:
        profile = GoalProfile(
            domain="interactive-app",
            scale="MEDIUM",
            complexity="MEDIUM",
            monitoring_need="BASIC",
        )
        mode = select_mode(profile)
        assert mode.mode_id == "hub_spoke"
        assert mode.enable_monitoring is True
        assert mode.enable_repair_loop is True

    def test_simple_static_web_goes_agile(self) -> None:
        profile = GoalProfile(
            domain="static-web",
            scale="SMALL",
            complexity="LOW",
        )
        mode = select_mode(profile)
        assert mode.mode_id == "agile"
        assert mode.skip_discovery is True

    def test_large_high_complexity_goes_waterfall(self) -> None:
        profile = GoalProfile(
            domain="api-service",
            scale="LARGE",
            complexity="HIGH",
        )
        mode = select_mode(profile)
        assert mode.mode_id == "waterfall"
        assert mode.skip_discovery is False

    def test_small_default_agile(self) -> None:
        profile = GoalProfile(scale="SMALL", domain="other", complexity="MEDIUM")
        mode = select_mode(profile)
        assert mode.mode_id == "agile"

    def test_large_default_waterfall(self) -> None:
        profile = GoalProfile(scale="LARGE", domain="other", complexity="MEDIUM")
        mode = select_mode(profile)
        assert mode.mode_id == "waterfall"

    def test_medium_with_iteration_goes_hub_spoke(self) -> None:
        profile = GoalProfile(
            scale="MEDIUM",
            domain="other",
            iteration_need="LIGHT",
        )
        mode = select_mode(profile)
        assert mode.mode_id == "hub_spoke"


class TestSelectModeFromMetadata:
    def test_virtual_town_gets_hub_spoke(self) -> None:
        mode, profile = select_mode_from_metadata(
            "创建一个虚拟小镇，角色自主演绎世界，实时监控对话质量",
            metadata={"title": "AI虚拟小镇"},
        )
        assert mode.mode_id == "hub_spoke"
        assert profile.domain == "interactive-app"

    def test_simple_page_gets_agile(self) -> None:
        mode, profile = select_mode_from_metadata(
            "创建一个简单的静态展示页面",
            metadata={"title": "展示页", "goal_scale": "SMALL"},
        )
        assert mode.mode_id == "agile"
        assert profile.domain == "static-web"

    def test_mode_as_dict(self) -> None:
        mode, _ = select_mode_from_metadata("test", metadata={})
        d = mode.as_dict()
        assert "mode_id" in d
        assert "skip_discovery" in d
        assert "enable_monitoring" in d
