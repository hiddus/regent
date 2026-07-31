"""HumanTaskService.complete must emit RELEASE_APPROVAL_COMPLETED outbox events."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from regent.application.execution_events import RELEASE_APPROVAL_COMPLETED
from regent.application.human_task_service import HumanTaskService
from regent.domain.states import GoalState
from regent.infrastructure.models import GoalModel, HumanTaskModel, OutboxEventModel


async def _seed_goal_with_release_task(sessions) -> tuple[uuid.UUID, uuid.UUID]:
    goal_id = uuid.uuid4()
    task_id = uuid.uuid4()
    corr = uuid.uuid4()
    candidate_id = uuid.uuid4()
    project_id = uuid.uuid4()
    async with sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                original_input="release approval fixture",
                created_by="tester",
                correlation_id=corr,
                status=GoalState.WAITING_HUMAN.value,
                metadata_json={
                    "pending_release": {
                        "release_candidate_id": str(candidate_id),
                        "human_task_id": str(task_id),
                        "app_project_id": str(project_id),
                        "idempotency_key": f"release-{goal_id}",
                        "correlation_id": str(corr),
                    },
                    "live_action": {
                        "summary": "等待你确认后继续",
                        "event_type": "HUMAN_TASK_REQUIRED",
                    },
                },
            )
        )
        session.add(
            HumanTaskModel(
                id=task_id,
                goal_id=goal_id,
                work_id=None,
                run_id=None,
                task_type="RELEASE_APPROVAL",
                prompt="Approve preview release candidate",
                requested_by="regent-core",
                due_at=datetime.now(UTC) + timedelta(hours=24),
                status="OPEN",
            )
        )
    return goal_id, task_id


@pytest.mark.asyncio
async def test_complete_release_approval_emits_outbox(db_sessions) -> None:
    goal_id, task_id = await _seed_goal_with_release_task(db_sessions)
    svc = HumanTaskService(db_sessions)

    await svc.complete(
        task_id,
        assigned_to="console-user",
        response={"approved": True, "decision": "APPROVE", "message": "批准"},
    )

    async with db_sessions() as session:
        task = await session.get(HumanTaskModel, task_id)
        assert task is not None
        assert task.status == "COMPLETED"
        assert task.response and task.response.get("approved") is True

        events = (
            await session.execute(
                select(OutboxEventModel).where(
                    OutboxEventModel.event_type == RELEASE_APPROVAL_COMPLETED
                )
            )
        ).scalars().all()
        assert len(events) == 1
        payload = events[0].payload
        assert payload["approved"] is True
        assert payload["task_id"] == str(task_id)
        assert payload["goal_id"] == str(goal_id)
        assert payload["release_candidate_id"]

        goal = await session.get(GoalModel, goal_id)
        assert goal is not None
        live = (goal.metadata_json or {}).get("live_action") or {}
        assert "继续部署" in str(live.get("summary", ""))
        assert (goal.metadata_json or {}).get("awaiting_human_intervention") is False


@pytest.mark.asyncio
async def test_reemit_stuck_release_approval(db_sessions) -> None:
    goal_id, task_id = await _seed_goal_with_release_task(db_sessions)
    async with db_sessions() as session, session.begin():
        task = await session.get(HumanTaskModel, task_id)
        assert task is not None
        task.status = "COMPLETED"
        task.response = {"approved": True, "message": "批准"}
        task.assigned_to = "console-user"
        task.completed_at = datetime.now(UTC)

    svc = HumanTaskService(db_sessions)
    resumed = await svc.reemit_stuck_release_approval(goal_id, assigned_to="console-user")
    assert resumed is not None
    assert resumed["task_id"] == str(task_id)

    async with db_sessions() as session:
        events = (
            await session.execute(
                select(OutboxEventModel).where(
                    OutboxEventModel.event_type == RELEASE_APPROVAL_COMPLETED
                )
            )
        ).scalars().all()
        assert len(events) == 1
        task = await session.get(HumanTaskModel, task_id)
        assert task is not None
        assert task.response.get("decision") == "APPROVE"


@pytest.mark.asyncio
async def test_timeout_due_applies_default_deny_and_emits(db_sessions) -> None:
    """CON-2: past-due OPEN tasks get timeout default (deny under balanced)."""
    goal_id, task_id = await _seed_goal_with_release_task(db_sessions)
    async with db_sessions() as session, session.begin():
        task = await session.get(HumanTaskModel, task_id)
        assert task is not None
        task.due_at = datetime.now(UTC) - timedelta(minutes=1)

    svc = HumanTaskService(db_sessions)
    n = await svc.timeout_due()
    assert n == 1

    async with db_sessions() as session:
        task = await session.get(HumanTaskModel, task_id)
        assert task is not None
        assert task.status == "COMPLETED"
        assert task.response.get("decision") == "REJECT"
        assert task.response.get("reason") == "timeout_default"
        events = (
            await session.execute(
                select(OutboxEventModel).where(
                    OutboxEventModel.event_type == RELEASE_APPROVAL_COMPLETED
                )
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].payload.get("approved") is False
