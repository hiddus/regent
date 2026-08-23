"""Tests for goal_classifier — multi-dimensional goal profiling."""

from __future__ import annotations

from regent.application.goal_classifier import GoalClassifier


class TestGoalClassifier:
    def setup_method(self) -> None:
        self.classifier = GoalClassifier()

    def test_static_web_simple(self) -> None:
        profile = self.classifier.classify(
            "创建一个简单的静态展示页面，用 HTML 和 CSS",
            metadata={"title": "静态展示页"},
        )
        assert profile.domain == "static-web"
        assert profile.complexity == "LOW"
        assert profile.scale == "SMALL"

    def test_interactive_app_detected(self) -> None:
        profile = self.classifier.classify(
            "创建一个虚拟小镇，每个角色有自己的人设，自主演绎世界。"
            "角色之间会对话，时间会推进，白天和夜晚不同。",
            metadata={"title": "AI虚拟小镇"},
        )
        assert profile.domain == "interactive-app"
        # The text has no explicit iteration keywords, but domain triggers monitoring.
        assert profile.monitoring_need in {"BASIC", "CONTINUOUS"}

    def test_api_service_detected(self) -> None:
        profile = self.classifier.classify(
            "搭建一个 REST API 后端服务，支持用户认证和 CRUD 操作",
            metadata={"title": "API服务"},
        )
        assert profile.domain == "api-service"

    def test_data_pipeline_detected(self) -> None:
        profile = self.classifier.classify(
            "构建一个 ETL 数据清洗管道，从多个数据源聚合数据并生成报表",
            metadata={"title": "数据管道"},
        )
        assert profile.domain == "data-pipeline"

    def test_high_complexity(self) -> None:
        profile = self.classifier.classify(
            "构建一个企业级分布式微服务系统，支持高并发和安全认证",
            metadata={"title": "企业级系统"},
        )
        assert profile.complexity == "HIGH"

    def test_low_complexity(self) -> None:
        profile = self.classifier.classify(
            "做一个简单的 demo 原型",
            metadata={"title": "Demo"},
        )
        assert profile.complexity == "LOW"

    def test_scale_from_metadata(self) -> None:
        profile = self.classifier.classify(
            "some input",
            metadata={"goal_scale": "LARGE"},
        )
        assert profile.scale == "LARGE"

    def test_unknown_domain_for_unrelated(self) -> None:
        profile = self.classifier.classify(
            "做一些事情",
            metadata={},
        )
        assert profile.domain == "other"

    def test_profile_as_dict(self) -> None:
        profile = self.classifier.classify("test", metadata={})
        d = profile.as_dict()
        assert "scale" in d
        assert "domain" in d
        assert "signals" in d
        assert isinstance(d["signals"], list)

    def test_continuous_monitoring_detected(self) -> None:
        profile = self.classifier.classify(
            "实时监控服务器状态，自动检测异常并告警",
            metadata={},
        )
        assert profile.monitoring_need == "CONTINUOUS"
