"""Fork gate: refuse start while needs_user_fork; SELECT_OPTION clears then starts."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from regent.application.app_guidance_service import (
    AppGuidanceService,
    GuidanceInterpretation,
)
from regent.application.goal_execution_service import GoalExecutionService
from regent.application.p1_contracts import canonical_hash
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AppProjectModel,
    ConversationModel,
    GoalModel,
    GoalSpecModel,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _spec_content() -> dict:
    return {
        "explicit_constraints": {},
        "system_inferences": {"first_deliverable": "preview"},
        "unknowns": [{"question": "A or B?", "blocking": False}],
        "success_criteria": {"preview": "ok"},
        "source_refs": [],
    }


async def _seed_fork_draft(
    sessions: async_sessionmaker[AsyncSession],
    *,
    needs_user_fork: bool = True,
) -> tuple[uuid.UUID, uuid.UUID]:
    project_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    spec_id = uuid.uuid4()
    conv_id = uuid.uuid4()
    content = _spec_content()
    options = [
        {"id": "a", "label": "方向A", "description": "工具向"},
        {"id": "b", "label": "方向B", "description": "社区向"},
    ]
    async with sessions() as session, session.begin():
        session.add_all(
            (
                AppProjectModel(
                    id=project_id,
                    name="ForkDemo",
                    product_intent="demo",
                    status="DRAFT",
                    created_by="tester",
                ),
                GoalModel(
                    id=goal_id,
                    app_project_id=project_id,
                    original_input="做个东西但方向不清",
                    status="DRAFT",
                    version=0,
                    created_by="tester",
                    correlation_id=uuid.uuid4(),
                    metadata_json={
                        "budget_limit": 10.0,
                        "needs_user_fork": needs_user_fork,
                        "pending_fork_options": options if needs_user_fork else [],
                        "runtime_plan": {
                            "needs_user_fork": needs_user_fork,
                            "fork_options": options if needs_user_fork else [],
                            "proposed_steps": ["一步", "二步"],
                        },
                    },
                ),
                GoalSpecModel(
                    id=spec_id,
                    goal_id=goal_id,
                    version=1,
                    status="DRAFT",
                    content_hash=canonical_hash(content),
                    **content,
                ),
                ConversationModel(
                    id=conv_id,
                    app_project_id=project_id,
                    title="ForkDemo",
                    status="ACTIVE",
                    created_by="tester",
                    metadata_json={"type": "APP"},
                ),
            )
        )
    return project_id, goal_id


@pytest.mark.asyncio
async def test_start_refuses_while_needs_user_fork(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    _, goal_id = await _seed_fork_draft(db_sessions, needs_user_fork=True)
    with pytest.raises(DomainError) as exc:
        await GoalExecutionService(db_sessions).start(
            goal_id,
            actor="tester",
            idempotency_key=f"test-start:{goal_id}",
        )
    assert exc.value.code == ErrorCode.INVALID_STATE
    assert "fork" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_select_option_clears_fork_but_does_not_start_before_confirmation(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    project_id, goal_id = await _seed_fork_draft(db_sessions, needs_user_fork=True)
    provider = MagicMock()
    service = AppGuidanceService(db_sessions, provider)
    receipt = await service._handle_select_option(
        project_id,
        "option:a 方向A",
        "tester",
        GuidanceInterpretation(
            command_type="SELECT_OPTION",
            summary="选方向A",
            selected_option_id="a",
        ),
        "regent-core:test",
    )
    assert receipt.command_type == "SELECT_OPTION"
    assert receipt.resulting_goal_id == goal_id

    async with db_sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        assert goal is not None
        meta = dict(goal.metadata_json or {})
        assert meta.get("needs_user_fork") is False
        assert meta.get("pending_fork_options") == []
        assert (meta.get("selected_fork") or {}).get("id") == "a"
        assert goal.status == "DRAFT"
        latest = await session.scalar(
            select(GoalSpecModel)
            .where(GoalSpecModel.goal_id == goal_id)
            .order_by(GoalSpecModel.version.desc())
            .limit(1)
        )
        assert latest is not None
        assert latest.status == "DRAFT"
        assert latest.explicit_constraints.get("selected_fork_id") == "a"


@pytest.mark.asyncio
async def test_start_refuses_goal_without_budget_limit(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    _, goal_id = await _seed_fork_draft(db_sessions, needs_user_fork=False)
    async with db_sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id, with_for_update=True)
        assert goal is not None
        goal.metadata_json = {
            key: value for key, value in dict(goal.metadata_json or {}).items()
            if key != "budget_limit"
        }
    with pytest.raises(DomainError) as exc:
        await GoalExecutionService(db_sessions).start(
            goal_id, actor="tester", idempotency_key=f"no-budget:{goal_id}"
        )
    assert exc.value.code == ErrorCode.POLICY_DENIED
    assert "budget_limit" in str(exc.value)


@pytest.mark.asyncio
async def test_core_cannot_auto_confirm_draft_start(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    _, goal_id = await _seed_fork_draft(db_sessions, needs_user_fork=False)
    with pytest.raises(DomainError) as exc:
        await GoalExecutionService(db_sessions).start(
            goal_id, actor="regent-core:auto-snapshot", idempotency_key=f"auto:{goal_id}"
        )
    assert exc.value.code == ErrorCode.POLICY_DENIED
    assert "confirmed" in str(exc.value)


@pytest.mark.asyncio
async def test_start_refuses_unlocked_boundary_even_with_budget(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    _, goal_id = await _seed_fork_draft(db_sessions, needs_user_fork=False)
    with pytest.raises(DomainError) as exc:
        await GoalExecutionService(db_sessions).start(
            goal_id, actor="tester", idempotency_key=f"unlocked:{goal_id}"
        )
    assert exc.value.code == ErrorCode.POLICY_DENIED
    assert "confirmed" in str(exc.value)
