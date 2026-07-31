"""CD-3.5: always_allow persistence + default handoff_options injection."""

from __future__ import annotations

import types
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from regent.api.human_tasks import CompleteHumanTaskBody, complete_human_task
from regent.application.confirmation_present import confirmation_for_human_task
from regent.domain.states import GoalState
from regent.infrastructure.models import GoalModel, HumanTaskModel


class _FakeApp:
    def __init__(self, sessions) -> None:
        self.state = types.SimpleNamespace(sessions=sessions)


class _FakeRequest:
    def __init__(self, sessions) -> None:
        self.app = _FakeApp(sessions)


async def _seed_goal_with_task(sessions, *, task_type: str = "RELEASE_APPROVAL") -> tuple[uuid.UUID, uuid.UUID]:
    goal_id = uuid.uuid4()
    task_id = uuid.uuid4()
    async with sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                original_input="cd-3.5 fixture",
                created_by="tester",
                correlation_id=uuid.uuid4(),
                status=GoalState.WAITING_HUMAN.value,
                metadata_json={},
            )
        )
        session.add(
            HumanTaskModel(
                id=task_id,
                goal_id=goal_id,
                work_id=None,
                run_id=None,
                task_type=task_type,
                prompt="approve?",
                requested_by="regent-core",
                due_at=datetime.now(UTC) + timedelta(hours=1),
                status="OPEN",
            )
        )
    return goal_id, task_id


@pytest.mark.asyncio
async def test_always_allow_persists_to_goal_decision_allow_actions(db_sessions) -> None:
    goal_id, task_id = await _seed_goal_with_task(db_sessions, task_type="RELEASE_APPROVAL")
    payload = CompleteHumanTaskBody(
        assigned_to="console-user",
        response={"approved": True, "decision": "APPROVE", "always_allow": True},
    )
    request = _FakeRequest(db_sessions)

    response = await complete_human_task(task_id, payload, request)
    assert response.status == "COMPLETED"

    async with db_sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        assert goal is not None
        allow_actions = (goal.metadata_json or {}).get("decision_allow_actions") or []
        assert "release_approval" in allow_actions


@pytest.mark.asyncio
async def test_always_flag_alias_also_persists(db_sessions) -> None:
    goal_id, task_id = await _seed_goal_with_task(db_sessions, task_type="QUALITY_APPROVAL")
    payload = CompleteHumanTaskBody(
        assigned_to="console-user",
        response={"approved": True, "decision": "APPROVE", "always": True},
    )
    request = _FakeRequest(db_sessions)

    await complete_human_task(task_id, payload, request)

    async with db_sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        assert "quality_approval" in (goal.metadata_json or {}).get("decision_allow_actions", [])


@pytest.mark.asyncio
async def test_without_always_allow_metadata_untouched(db_sessions) -> None:
    goal_id, task_id = await _seed_goal_with_task(db_sessions)
    payload = CompleteHumanTaskBody(
        assigned_to="console-user",
        response={"approved": True, "decision": "APPROVE"},
    )
    request = _FakeRequest(db_sessions)

    await complete_human_task(task_id, payload, request)

    async with db_sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        assert "decision_allow_actions" not in (goal.metadata_json or {})


@pytest.mark.asyncio
async def test_repeated_always_allow_does_not_duplicate(db_sessions) -> None:
    goal_id, task_id = await _seed_goal_with_task(db_sessions, task_type="RELEASE_APPROVAL")
    payload = CompleteHumanTaskBody(
        assigned_to="console-user",
        response={"approved": True, "decision": "APPROVE", "always_allow": True},
    )
    request = _FakeRequest(db_sessions)
    await complete_human_task(task_id, payload, request)

    # Simulate a second, independent task for the same goal + action, also always_allow.
    async with db_sessions() as session, session.begin():
        second_task_id = uuid.uuid4()
        session.add(
            HumanTaskModel(
                id=second_task_id,
                goal_id=goal_id,
                work_id=None,
                run_id=None,
                task_type="RELEASE_APPROVAL",
                prompt="approve again?",
                requested_by="regent-core",
                due_at=datetime.now(UTC) + timedelta(hours=1),
                status="OPEN",
            )
        )
    await complete_human_task(second_task_id, payload, request)

    async with db_sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        allow_actions = (goal.metadata_json or {}).get("decision_allow_actions") or []
        assert allow_actions.count("release_approval") == 1


def test_confirmation_for_human_task_includes_default_handoff_options() -> None:
    confirmation = confirmation_for_human_task(
        task_type="DELIVERY_GAP_INTERVENE",
        summary="需要你介入",
    )
    options = confirmation.get("handoff_options")
    assert isinstance(options, list) and len(options) == 3
    ids = {opt["id"] for opt in options}
    assert ids == {"narrow_scope", "keep_trying", "stop"}
    for opt in options:
        assert opt.get("label")
        assert opt.get("cost_hint")
