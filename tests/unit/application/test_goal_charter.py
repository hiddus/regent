import pytest
from pydantic import ValidationError

from regent.application.goal_charter import GoalCharter, MetricContract


def _charter(**overrides: object) -> GoalCharter:
    data: dict[str, object] = {
        "owner_intent": "Improve qualified activation without increasing complaints",
        "primary_metric": MetricContract(
            name="activation_rate",
            definition="activated accounts / qualified signups",
            baseline=0.2,
            target=0.25,
            source="warehouse.activation_daily",
        ),
        "guardrail_metrics": [],
        "allowed_actions": ["read_analytics", "draft_experiment"],
        "prohibited_actions": ["change_price"],
        "data_sources": ["warehouse.activation_daily"],
        "budget_limit": 10_000,
        "maximum_acceptable_loss": 1_000,
        "owner_id": "growth-owner",
        "confirmed": True,
    }
    data.update(overrides)
    return GoalCharter.model_validate(data)


def test_confirmed_charter_permits_commercial_start() -> None:
    assert _charter().permits_commercial_start() is True


def test_unconfirmed_charter_does_not_permit_start() -> None:
    assert _charter(confirmed=False).permits_commercial_start() is False


def test_action_cannot_be_allowed_and_prohibited() -> None:
    with pytest.raises(ValidationError, match="both allowed and prohibited"):
        _charter(prohibited_actions=["read_analytics"])


def test_maximum_loss_cannot_exceed_budget() -> None:
    with pytest.raises(ValidationError, match="cannot exceed budget_limit"):
        _charter(maximum_acceptable_loss=20_000)
