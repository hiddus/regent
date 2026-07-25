"""Unit tests for product rejection → REVISE evolution loop helpers."""

from __future__ import annotations

from regent.application.evidence_policy import goal_requires_external_evidence
from regent.application.feedback_service import Aggregation, Comparison, MetricDefinition


def test_paste_summary_goal_does_not_require_external_feeds() -> None:
    assert (
        goal_requires_external_evidence(
            "做一个内部团队周报汇总 Web 工具，支持粘贴文本并生成结构化摘要页",
            {},
        )
        is False
    )


def test_rejection_metric_definition_is_lte_zero_guardrail() -> None:
    metric = MetricDefinition(
        metric_key="product_rejection_count",
        definition_version="v1",
        observation_source="product-analytics",
        value_field="value",
        aggregation=Aggregation.COUNT,
        comparison=Comparison.LTE,
        threshold=0.0,
        minimum_samples=1,
        exclude_bots=True,
        exclude_internal=True,
    )
    assert metric.comparison is Comparison.LTE
    assert metric.threshold == 0.0
