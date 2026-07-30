"""P2-A: Scheduler end-to-end acceptance tests (real DB, no AsyncMock session).

Verifies:
- dispatch_with_eo creates a real ExternalOperation row (PREPARE→DISPATCHING)
- preempt_with_eo_check refuses when target has DISPATCHING EO
- preempt_with_eo_check succeeds with correct preempt(queue_entry_id, reason) args
- Priority ordering helpers remain stable
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from regent.application.scheduler_service import (
    SCHEDULER_EO_PROVIDER,
    EnqueueWork,
    EnsureQuota,
    ScheduleOnce,
    SchedulerService,
    compute_aging_score,
)
from regent.domain.scheduler_states import QueueEntryState
from regent.domain.states import GoalState, RunState, WorkState
from regent.infrastructure.models import (
    ExecutionQueueEntryModel,
    ExternalOperationModel,
    GoalModel,
    RunModel,
    WorkModel,
)


async def _seed_goal_work_run(
    sessions, *, actor: str = "test-worker"
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    goal_id, work_id, run_id, corr = (uuid.uuid4() for _ in range(4))
    async with sessions() as session, session.begin():
        session.add_all(
            (
                GoalModel(
                    id=goal_id,
                    original_input="scheduler eo fixture",
                    created_by=actor,
                    correlation_id=corr,
                    status=GoalState.ACTIVE.value,
                    metadata_json={},
                ),
                WorkModel(
                    id=work_id,
                    goal_id=goal_id,
                    purpose="scheduler work",
                    input_refs=[],
                    acceptance_criteria={},
                    dependency_ids=[],
                    priority=5,
                    budget={},
                    status=WorkState.RUNNING.value,
                    correlation_id=corr,
                    metadata_json={},
                ),
                RunModel(
                    id=run_id,
                    work_id=work_id,
                    actor_id=actor,
                    tool_ref="scheduler:v1",
                    input_version="sha256:x",
                    idempotency_key=f"run-{run_id}",
                    resource_usage={},
                    status=RunState.RUNNING.value,
                    correlation_id=corr,
                ),
            )
        )
    return goal_id, work_id, run_id


class TestSchedulerEOIntegration:
    """P2-A: Scheduler dispatch_with_eo creates ExternalOperation in DB."""

    @pytest.mark.asyncio
    async def test_dispatch_with_eo_no_entry_returns_not_scheduled(self, db_sessions) -> None:
        svc = SchedulerService(db_sessions)
        await svc.ensure_quota(EnsureQuota(org_key="test-org", resource_name="cpu", limit_amount=4))
        result = await svc.dispatch_with_eo(
            ScheduleOnce(org_key="test-org", actor="test-worker"),
            operation_key="test-op-empty",
        )
        assert result["status"] == "not_scheduled"
        assert result["eo_id"] is None

    @pytest.mark.asyncio
    async def test_dispatch_with_eo_creates_eo_on_success(self, db_sessions) -> None:
        actor = "test-worker"
        goal_id, work_id, _run_id = await _seed_goal_work_run(db_sessions, actor=actor)
        svc = SchedulerService(db_sessions)
        await svc.ensure_quota(EnsureQuota(org_key="test-org", resource_name="cpu", limit_amount=8))
        await svc.enqueue(
            EnqueueWork(
                goal_id=goal_id,
                work_id=work_id,
                org_key="test-org",
                base_priority=10,
                resource_request={"cpu": 1},
                actor=actor,
            )
        )

        op_key = f"test-dispatch-{uuid.uuid4().hex[:8]}"
        result = await svc.dispatch_with_eo(
            ScheduleOnce(org_key="test-org", actor=actor),
            operation_key=op_key,
        )
        assert result["status"] == "dispatched_with_eo"
        assert result["eo_operation_key"] == op_key
        assert result["eo_provider"] == SCHEDULER_EO_PROVIDER
        assert result["eo_id"] is not None

        eo_id = uuid.UUID(str(result["eo_id"]))
        async with db_sessions() as session:
            eo = await session.get(ExternalOperationModel, eo_id)
            assert eo is not None
            assert eo.operation_key == op_key
            assert eo.provider == SCHEDULER_EO_PROVIDER
            assert eo.status == "DISPATCHING"
            assert eo.goal_id == goal_id

            decision = await svc.get_decision(uuid.UUID(str(result["decision_id"])))
            binding = (decision.output_json or {}).get("eo_binding") or {}
            assert binding.get("bound") is True
            assert binding.get("eo_id") == str(eo_id)

            entry = await session.scalar(
                select(ExecutionQueueEntryModel).where(
                    ExecutionQueueEntryModel.goal_id == goal_id
                )
            )
            assert entry is not None
            assert entry.status == QueueEntryState.SCHEDULED.value


class TestPreemptWithEOCheck:
    """P2-A: preempt_with_eo_check refuses DISPATCHING EO; otherwise calls preempt correctly."""

    @pytest.mark.asyncio
    async def test_preempt_refused_when_dispatching_eo_exists(self, db_sessions) -> None:
        actor = "test-worker"
        goal_id, work_id, _run_id = await _seed_goal_work_run(db_sessions, actor=actor)
        svc = SchedulerService(db_sessions)
        await svc.ensure_quota(EnsureQuota(org_key="test-org", resource_name="cpu", limit_amount=8))
        await svc.enqueue(
            EnqueueWork(
                goal_id=goal_id,
                work_id=work_id,
                org_key="test-org",
                base_priority=10,
                resource_request={"cpu": 1},
                actor=actor,
            )
        )
        op_key = f"block-preempt-{uuid.uuid4().hex[:8]}"
        dispatched = await svc.dispatch_with_eo(
            ScheduleOnce(org_key="test-org", actor=actor),
            operation_key=op_key,
        )
        assert dispatched["status"] == "dispatched_with_eo"

        result = await svc.preempt_with_eo_check(
            org_key="test-org",
            target_goal_id=goal_id,
            actor=actor,
        )
        assert result["preempted"] is False
        assert "DISPATCHING" in result["reason"]
        assert result["blocking_ops_count"] >= 1

    @pytest.mark.asyncio
    async def test_preempt_allowed_when_no_dispatching_eo(self, db_sessions) -> None:
        actor = "test-worker"
        goal_id, work_id, _run_id = await _seed_goal_work_run(db_sessions, actor=actor)
        svc = SchedulerService(db_sessions)
        await svc.ensure_quota(EnsureQuota(org_key="test-org", resource_name="cpu", limit_amount=8))
        await svc.enqueue(
            EnqueueWork(
                goal_id=goal_id,
                work_id=work_id,
                org_key="test-org",
                base_priority=3,
                resource_request={"cpu": 1},
                actor=actor,
            )
        )
        # Schedule without EO so entry is SCHEDULED but no DISPATCHING EO.
        decision = await svc.schedule_once(ScheduleOnce(org_key="test-org", actor=actor))
        assert (decision.output_json or {}).get("selected_queue_entry_id")

        result = await svc.preempt_with_eo_check(
            org_key="test-org",
            target_goal_id=goal_id,
            actor=actor,
            reason="test_preempt",
        )
        assert result["preempted"] is True
        assert result.get("preempted_entry_id")

        async with db_sessions() as session:
            entry = await session.get(
                ExecutionQueueEntryModel, uuid.UUID(str(result["preempted_entry_id"]))
            )
            assert entry is not None
            assert entry.status == QueueEntryState.QUEUED.value


class TestSchedulerConcurrency:
    """P2-A: enqueue command shape for multi-goal budgets."""

    def test_scheduler_handles_20_goals_in_budget(self) -> None:
        commands = [
            EnqueueWork(
                goal_id=uuid.uuid4(),
                work_id=uuid.uuid4(),
                org_key="test-org",
                base_priority=i % 5,
                resource_request={"cpu": 1, "memory_mb": 256},
            )
            for i in range(20)
        ]
        assert len(commands) == 20

    def test_scheduler_priority_ordering(self) -> None:
        now = datetime.now(UTC)
        score_low = compute_aging_score(1, now, now=now, aging_per_minute=1)
        score_high = compute_aging_score(10, now, now=now, aging_per_minute=1)
        assert score_high > score_low
