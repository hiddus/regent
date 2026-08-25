"""Tests for GoalRevisionService — 目标进化外环."""
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from regent.application.goal_revision_service import (
    CORRECTION_ACCUMULATION_THRESHOLD,
    GoalRevisionResult,
    GoalRevisionService,
)


class TestShouldRevise:
    """Test the _should_revise decision logic."""

    def _make_service(self) -> GoalRevisionService:
        return GoalRevisionService(AsyncMock())

    def _make_goal(self, **meta_overrides) -> MagicMock:
        goal = MagicMock()
        goal.metadata_json = {
            "goal_revision_history": [],
            "active_corrections": [],
            **meta_overrides,
        }
        return goal

    def _make_spec(self, version: int = 1) -> MagicMock:
        spec = MagicMock()
        spec.version = version
        spec.explicit_constraints = {}
        spec.system_inferences = {}
        spec.unknowns = []
        spec.success_criteria = {}
        spec.source_refs = []
        return spec

    def test_milestone_boundary_always_triggers(self):
        svc = self._make_service()
        goal = self._make_goal()
        spec = self._make_spec()
        should, reason = svc._should_revise(
            trigger="milestone_boundary", goal=goal, latest_spec=spec,
            revision_context={},
        )
        assert should is True
        assert "milestone boundary" in reason

    def test_delivery_failure_always_triggers(self):
        svc = self._make_service()
        goal = self._make_goal()
        spec = self._make_spec()
        should, reason = svc._should_revise(
            trigger="delivery_failure", goal=goal, latest_spec=spec,
            revision_context={"gate_status": "FAILED"},
        )
        assert should is True
        assert "delivery failure" in reason

    def test_user_direction_change_triggers(self):
        svc = self._make_service()
        goal = self._make_goal()
        spec = self._make_spec()
        should, reason = svc._should_revise(
            trigger="user_direction_change", goal=goal, latest_spec=spec,
            revision_context={},
        )
        assert should is True

    def test_correction_accumulation_triggers_at_threshold(self):
        svc = self._make_service()
        corrections = [{"target": "x", "detail": "y"}] * CORRECTION_ACCUMULATION_THRESHOLD
        goal = self._make_goal(active_corrections=corrections)
        spec = self._make_spec()
        should, reason = svc._should_revise(
            trigger="correction_accumulation", goal=goal, latest_spec=spec,
            revision_context={},
        )
        assert should is True
        assert str(CORRECTION_ACCUMULATION_THRESHOLD) in reason

    def test_correction_accumulation_below_threshold(self):
        svc = self._make_service()
        corrections = [{"target": "x", "detail": "y"}] * (CORRECTION_ACCUMULATION_THRESHOLD - 1)
        goal = self._make_goal(active_corrections=corrections)
        spec = self._make_spec()
        should, reason = svc._should_revise(
            trigger="correction_accumulation", goal=goal, latest_spec=spec,
            revision_context={},
        )
        assert should is False

    def test_rate_limit_prevents_oscillation(self):
        svc = self._make_service()
        history = [
            {"from_version": 1, "to_version": 2},
            {"from_version": 2, "to_version": 3},
            {"from_version": 3, "to_version": 4},
        ]
        goal = self._make_goal(goal_revision_history=history)
        spec = self._make_spec()
        should, reason = svc._should_revise(
            trigger="milestone_boundary", goal=goal, latest_spec=spec,
            revision_context={},
        )
        assert should is False
        assert "rate limit" in reason

    def test_unknown_trigger_does_not_revise(self):
        svc = self._make_service()
        goal = self._make_goal()
        spec = self._make_spec()
        should, reason = svc._should_revise(
            trigger="bogus_trigger", goal=goal, latest_spec=spec,
            revision_context={},
        )
        assert should is False
        assert "unknown trigger" in reason


class TestBuildEvolvedContent:
    """Test spec content evolution logic."""

    def _make_service(self) -> GoalRevisionService:
        return GoalRevisionService(AsyncMock())

    def _make_spec(self) -> MagicMock:
        spec = MagicMock()
        spec.version = 2
        spec.explicit_constraints = {"tech_stack": "python", "response_time_ms": "200"}
        spec.system_inferences = {"target_users": "developers"}
        spec.unknowns = [{"question": "auth method?"}]
        spec.success_criteria = {"works": True}
        spec.source_refs = [{"type": "conversation"}]
        return spec

    def test_milestone_boundary_carries_forward(self):
        svc = self._make_service()
        spec = self._make_spec()
        result = svc._build_evolved_content(
            latest_spec=spec, trigger="milestone_boundary",
            revision_context={"completed_milestone": "M1"},
        )
        # Core content preserved
        assert result["system_inferences"]["target_users"] == "developers"
        assert result["unknowns"] == [{"question": "auth method?"}]
        assert result["success_criteria"] == {"works": True}
        # Source refs get revision provenance
        assert any(r["type"] == "goal_revision" for r in result["source_refs"])

    def test_delivery_failure_relaxates_metric_constraints(self):
        svc = self._make_service()
        spec = self._make_spec()
        result = svc._build_evolved_content(
            latest_spec=spec, trigger="delivery_failure",
            revision_context={"gate_status": "FAILED", "reorg_exhausted": True},
        )
        # Structural constraints kept
        assert "tech_stack" in result["explicit_constraints"]
        # Metric constraints relaxed
        assert "response_time_ms" not in result["explicit_constraints"]

    def test_correction_accumulation_marks_merged(self):
        svc = self._make_service()
        spec = self._make_spec()
        result = svc._build_evolved_content(
            latest_spec=spec, trigger="correction_accumulation",
            revision_context={"corrections_count": 3},
        )
        assert result["explicit_constraints"].get("_corrections_merged") is True


class TestGoalRevisionResult:
    def test_default_values(self):
        r = GoalRevisionResult(revised=False)
        assert r.revised is False
        assert r.reason == ""
        assert r.new_spec_version == 0

    def test_revised_result(self):
        r = GoalRevisionResult(
            revised=True, reason="test", new_spec_version=3, old_spec_version=2,
        )
        assert r.revised is True
        assert r.new_spec_version == 3
