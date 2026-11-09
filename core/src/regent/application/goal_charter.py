"""Customer operating mandate for autonomous work inside an agreed envelope."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class MetricContract(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    definition: str = Field(min_length=1, max_length=2_000)
    baseline: float | None = None
    target: float | None = None
    source: str = Field(min_length=1, max_length=1_000)
    attribution_window_days: int = Field(default=7, ge=1, le=365)


class GoalCharter(BaseModel):
    owner_intent: str = Field(min_length=1, max_length=5_000)
    primary_metric: MetricContract
    guardrail_metrics: list[MetricContract] = Field(default_factory=list, max_length=20)
    allowed_actions: list[str] = Field(default_factory=list, max_length=100)
    prohibited_actions: list[str] = Field(default_factory=list, max_length=100)
    data_sources: list[str] = Field(default_factory=list, max_length=100)
    budget_limit: float = Field(gt=0)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    maximum_acceptable_loss: float = Field(ge=0)
    exploration_posture: Literal["conservative", "balanced", "progressive"] = "balanced"
    decision_cycle_days: int = Field(default=7, ge=1, le=90)
    owner_id: str = Field(min_length=1, max_length=255)
    confirmed: bool = False

    @model_validator(mode="after")
    def validate_envelope(self) -> "GoalCharter":
        overlap = set(self.allowed_actions) & set(self.prohibited_actions)
        if overlap:
            raise ValueError(f"actions cannot be both allowed and prohibited: {sorted(overlap)}")
        if self.maximum_acceptable_loss > self.budget_limit:
            raise ValueError("maximum_acceptable_loss cannot exceed budget_limit")
        return self

    def permits_commercial_start(self) -> bool:
        return self.confirmed and bool(self.allowed_actions or self.data_sources)
