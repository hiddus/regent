"""Unit tests for DeliveryReviewQueryService (CD-3.1)."""

from __future__ import annotations

import uuid

import pytest

from regent.application.delivery_review_api import (
    DeliveryReviewQueryService,
    assemble_delivery_review_payload,
    resolve_generation_run_id,
)
from regent.domain.states import GoalState
from regent.infrastructure.models import GoalModel


@pytest.mark.asyncio
async def test_get_for_project_empty_when_no_goal(db_sessions) -> None:
    svc = DeliveryReviewQueryService(db_sessions)
    payload = await svc.get_for_project(uuid.uuid4())
    assert payload == {
        "plan": None,
        "transcript": None,
        "verification": None,
        "budget": None,
    }


@pytest.mark.asyncio
async def test_get_for_project_with_metadata_shape(db_sessions) -> None:
    project_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    run_id = uuid.uuid4()
    async with db_sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                app_project_id=project_id,
                original_input="review fixture",
                created_by="tester",
                correlation_id=uuid.uuid4(),
                status=GoalState.ACTIVE.value,
                metadata_json={
                    "generation_run_id": str(run_id),
                    "delivery_verification": {"verdict": "PASS", "summary": "ok"},
                    "agent_budget": {
                        "turns": 3,
                        "max_turns": 40,
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "max_tokens": 200_000,
                    },
                    "live_action": {"turn": 3, "summary": "generating"},
                },
            )
        )

    payload = await DeliveryReviewQueryService(db_sessions).get_for_project(project_id)
    assert payload["plan"] is None
    assert payload["transcript"] is None
    assert payload["verification"] == {"verdict": "PASS", "summary": "ok"}
    assert payload["budget"] == {
        "turns": 3,
        "max_turns": 40,
        "input_tokens": 100,
        "output_tokens": 50,
        "max_tokens": 200_000,
    }


def test_resolve_generation_run_id_from_common_keys() -> None:
    run_id = uuid.uuid4()
    meta = {"last_generation_run_id": str(run_id)}
    assert resolve_generation_run_id(meta) == run_id

    meta = {"halt": {"generation_run_id": str(run_id)}}
    assert resolve_generation_run_id(meta) == run_id


def test_assemble_delivery_review_payload_plan_wrapper() -> None:
    payload = assemble_delivery_review_payload(
        metadata={"delivery_review": {"passed": True}},
        plan_items=[{"item_key": "a", "status": "pending"}],
        transcript=[{"turn": 1, "role": "assistant", "content": "hi"}],
    )
    assert payload["plan"] == {"items": [{"item_key": "a", "status": "pending"}]}
    assert payload["transcript"] == [{"turn": 1, "role": "assistant", "content": "hi"}]
    assert payload["verification"] == {"passed": True}
