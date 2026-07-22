import pytest
from pydantic import ValidationError
from regent.api.main import create_app
from regent.application.feedback_service import (
    Aggregation,
    Comparison,
    FeedbackService,
    MetricDefinition,
)
from regent.domain.errors import DomainError


def test_feedback_routes_are_exposed() -> None:
    paths = set(create_app().openapi()["paths"])
    assert {
        "/v1/deployments/{deployment_id}/metric-bindings",
        "/v1/goals/{goal_id}/gate-evaluations",
        "/v1/gate-evaluations/{gate_id}",
        "/v1/gate-evaluations/{gate_id}/iteration-decisions",
        "/v1/goals/{goal_id}/iteration-decisions",
    } <= paths


def test_feedback_aggregation_and_comparison_are_deterministic() -> None:
    assert FeedbackService._aggregate([1.0, 2.0, 3.0], Aggregation.COUNT) == 3.0
    assert FeedbackService._aggregate([1.0, 2.0, 3.0], Aggregation.SUM) == 6.0
    assert FeedbackService._aggregate([1.0, 2.0, 3.0], Aggregation.AVERAGE) == 2.0
    assert FeedbackService._compare(2.0, 2.0, Comparison.GTE)
    assert FeedbackService._compare(2.0, 2.0, Comparison.LTE)


def test_metric_definition_requires_evidence_floor() -> None:
    with pytest.raises(ValidationError):
        MetricDefinition(
            metric_key="activation",
            definition_version="v1",
            observation_source="app",
            aggregation=Aggregation.COUNT,
            comparison=Comparison.GTE,
            threshold=1,
            minimum_samples=0,
        )


def test_metric_values_reject_boolean_and_non_numeric_values() -> None:
    with pytest.raises(DomainError):
        FeedbackService._numeric({"value": True}, "value")
    with pytest.raises(DomainError):
        FeedbackService._numeric({"value": "1"}, "value")
    assert FeedbackService._numeric({"value": 1}, "value") == 1.0
