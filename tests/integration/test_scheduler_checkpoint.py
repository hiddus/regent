"""P2-A: Scheduler checkpoint/resume tests.

Verifies:
- Worker crash recovery: scheduling state can be resumed from checkpoint
- Checkpoint contains enough info to re-queue the selected entry
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from regent.application.scheduler_service import (
    SchedulerService,
)


def _make_mock_sessions():
    """Create a mock session factory."""
    mock_session = AsyncMock()
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_sessions = MagicMock()
    mock_sessions.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_sessions.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_sessions, mock_session


class TestSchedulerCheckpointResume:
    """P2-A: checkpoint/resume after worker crash."""

    @pytest.mark.asyncio
    async def test_resume_from_checkpoint_requeues_entry(self) -> None:
        """After crash, resume_from_checkpoint re-queues the selected entry."""
        mock_sessions, mock_session = _make_mock_sessions()

        entry_id = uuid.uuid4()
        decision_id = uuid.uuid4()
        checkpoint_id = uuid.uuid4()

        # Mock checkpoint
        mock_checkpoint = MagicMock()
        mock_checkpoint.id = checkpoint_id
        mock_checkpoint.scheduling_decision_id = decision_id

        # Mock decision with selected entry
        mock_decision = MagicMock()
        mock_decision.id = decision_id
        mock_decision.output_json = {
            "selected_queue_entry_id": str(entry_id),
            "goal_id": str(uuid.uuid4()),
        }
        mock_decision.input_snapshot_json = {"org_key": "test-org"}

        # Mock entry
        mock_entry = MagicMock()
        mock_entry.id = entry_id
        mock_entry.status = "SCHEDULED"
        mock_entry.aging_score = 5
        mock_entry.base_priority = 3
        mock_entry.metadata_json = {}

        # Setup session.get to return different objects
        async def mock_get(model, id_val, **kwargs):
            if hasattr(model, "__tablename__"):
                if model.__tablename__ == "scheduler_checkpoints":
                    return mock_checkpoint
                elif model.__tablename__ == "scheduling_decisions":
                    return mock_decision
                elif model.__tablename__ == "execution_queue_entries":
                    return mock_entry
            return None

        mock_session.get = mock_get

        svc = SchedulerService(mock_sessions)
        result = await svc.resume_from_checkpoint(checkpoint_id, actor="test-worker")

        assert result.id == entry_id
        assert result.status == "QUEUED"
        assert result.aging_score >= 103  # base_priority + 100
        assert result.metadata_json.get("resumed_from_checkpoint") == str(checkpoint_id)

    @pytest.mark.asyncio
    async def test_checkpoint_not_found_raises(self) -> None:
        """Resuming from non-existent checkpoint raises NOT_FOUND."""
        mock_sessions, mock_session = _make_mock_sessions()
        mock_session.get = AsyncMock(return_value=None)

        svc = SchedulerService(mock_sessions)
        with pytest.raises(Exception):
            await svc.resume_from_checkpoint(uuid.uuid4(), actor="test-worker")
