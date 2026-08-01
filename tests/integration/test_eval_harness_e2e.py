"""P2-B: Eval Harness end-to-end test.

Verifies:
- Frozen task set loading
- Blind evaluation
- Statistical gate (pass@k + CI)
- Memory enablement via DecisionRecord
"""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from regent.application.eval_harness_service import EvalHarnessService
from regent.application.memory_service import MemoryService


FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "fixtures", "eval_task_set_v1.json",
)


class TestLoadFrozenTaskSet:
    """P2-B: Frozen task set can be loaded from artifact."""

    @pytest.mark.asyncio
    async def test_load_from_file(self) -> None:
        """Load frozen task set from fixture file."""
        mock_sessions = MagicMock()
        svc = EvalHarnessService(mock_sessions)

        if os.path.isfile(FIXTURE_PATH):
            task_set = await svc.load_frozen_task_set(FIXTURE_PATH)
            assert "tasks" in task_set
            assert len(task_set["tasks"]) == 10
            assert task_set["version"] == "v1"

    @pytest.mark.asyncio
    async def test_load_fail_closed_for_unknown_ref(self) -> None:
        """Unknown artifact ref must fail closed (QA gate)."""
        from regent.domain.errors import DomainError, ErrorCode

        mock_sessions = MagicMock()
        svc = EvalHarnessService(mock_sessions)

        with pytest.raises(DomainError) as exc:
            await svc.load_frozen_task_set("nonexistent-artifact")
        assert exc.value.code is ErrorCode.NOT_FOUND


class TestBlindEvaluation:
    """P2-B: Blind evaluation hides agent identity."""

    @pytest.mark.asyncio
    async def test_blind_eval_scores_with_blind_flag(self) -> None:
        """Blind eval produces scores with blind=True."""
        mock_session = AsyncMock()
        mock_session.begin = MagicMock()
        mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_sessions = MagicMock()
        mock_sessions.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sessions.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_run = MagicMock()
        mock_run.id = uuid.uuid4()
        mock_run.status = "FROZEN"
        mock_run.seed = "test-seed-42"
        mock_run.task_set_json = {
            "tasks": [
                {"id": "t1", "description": "task 1"},
                {"id": "t2", "description": "task 2"},
                {"id": "t3", "description": "task 3"},
            ]
        }
        mock_run.budget_json = {
            "wall_clock_budget_s": 300,
            "compute_budget_units": 100,
        }
        mock_session.get = AsyncMock(return_value=mock_run)

        svc = EvalHarnessService(mock_sessions)
        result = await svc.run_blind_evaluation(mock_run.id, actor="test-eval")

        assert result.status == "SCORED"
        assert result.scores_json["blind"] is True
        for score in result.scores_json["tasks"]:
            assert score["blind"] is True
            assert score["agent_identity_hidden"] is True


class TestStatisticalGate:
    """P2-B: Statistical gate computes pass@k with confidence interval."""

    def test_gate_passes_with_high_pass_rate(self) -> None:
        """Gate passes when lower CI bound exceeds baseline."""
        mock_sessions = MagicMock()
        svc = EvalHarnessService(mock_sessions)

        scores = {
            "tasks": [
                {"task_id": f"t{i}", "pass@1": True}
                for i in range(10)
            ]
        }
        result = svc.statistical_gate(scores, baseline_rate=0.5)
        assert result["passed"] is True
        assert result["pass_at_k"] == 1.0
        assert result["n"] == 10

    def test_gate_fails_with_low_pass_rate(self) -> None:
        """Gate fails when pass rate is below baseline."""
        mock_sessions = MagicMock()
        svc = EvalHarnessService(mock_sessions)

        scores = {
            "tasks": [
                {"task_id": f"t{i}", "pass@1": False}
                for i in range(10)
            ]
        }
        result = svc.statistical_gate(scores, baseline_rate=0.5)
        assert result["passed"] is False
        assert result["pass_at_k"] == 0.0

    def test_gate_insufficient_tasks(self) -> None:
        """Gate fails with insufficient tasks."""
        mock_sessions = MagicMock()
        svc = EvalHarnessService(mock_sessions)

        scores = {"tasks": [{"task_id": "t1", "pass@1": True}]}
        result = svc.statistical_gate(scores, min_tasks=5)
        assert result["passed"] is False
        assert "insufficient" in result["rationale"]

    def test_gate_confidence_interval_is_valid(self) -> None:
        """CI bounds are within [0, 1] and lower <= upper."""
        mock_sessions = MagicMock()
        svc = EvalHarnessService(mock_sessions)

        scores = {
            "tasks": [
                {"task_id": f"t{i}", "pass@1": i % 2 == 0}
                for i in range(20)
            ]
        }
        result = svc.statistical_gate(scores, baseline_rate=0.3)
        lower, upper = result["confidence_interval"]
        assert 0.0 <= lower <= upper <= 1.0
        assert result["n"] == 20


class TestMemoryStageEnablement:
    """P2-B: Memory enablement requires DecisionRecord."""

    @pytest.mark.asyncio
    async def test_enable_memory_stage_promotes_candidates(self) -> None:
        """enable_memory_stage promotes CANDIDATE memories to VERIFIED."""
        mock_session = AsyncMock()
        mock_session.begin = MagicMock()
        mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_sessions = MagicMock()
        mock_sessions.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sessions.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_mem1 = MagicMock()
        mock_mem1.status = "CANDIDATE"
        mock_mem1.content_json = {"key": "value1"}

        mock_mem2 = MagicMock()
        mock_mem2.status = "CANDIDATE"
        mock_mem2.content_json = {"key": "value2"}

        # Mock scalars to return an iterable of memories
        async def mock_scalars(stmt):
            return [mock_mem1, mock_mem2]

        mock_session.scalars = mock_scalars

        svc = MemoryService(mock_sessions)
        decision_id = uuid.uuid4()
        result = await svc.enable_memory_stage(
            "test-org",
            decision_record_id=decision_id,
            stage="VERIFIED",
            actor="test-eval",
        )

        assert result["promoted_count"] == 2
        assert result["stage"] == "VERIFIED"
        assert result["decision_record_id"] == str(decision_id)
        assert mock_mem1.status == "VERIFIED"
        assert mock_mem2.status == "VERIFIED"
