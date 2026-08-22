"""Regression: numbered boundary answers must count a clarification round
even when no blocking unknowns remain.

Previously the numbered-answer override required blocking unknowns, so a
draft whose unknowns were all advisory could never reach the two-round
confirmation gate — the goal deadlocked at confirm time.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.application.app_guidance_service import (
    AppGuidanceService,
    GuidanceInterpretation,
)
from regent.application.app_project_service import AppProjectService
from regent.application.p1_contracts import canonical_hash
from regent.infrastructure.models import (
    AppProjectModel,
    ConversationModel,
    GoalModel,
    GoalSpecModel,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def _seed_advisory_draft(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID]:
    project_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    content = {
        "explicit_constraints": {"budget": "10"},
        "system_inferences": {"app_name": "Town", "first_deliverable": "preview"},
        "unknowns": [{"question": "界面配色偏好", "blocking": False}],
        "success_criteria": {"preview": "works"},
        "source_refs": [],
    }
    async with sessions() as session, session.begin():
        session.add_all((
            AppProjectModel(
                id=project_id,
                name="TownDemo",
                product_intent="test boundary answers without blockers",
                status="DRAFT",
                created_by="tester",
            ),
            GoalModel(
                id=goal_id,
                app_project_id=project_id,
                original_input="build town demo",
                status="DRAFT",
                version=0,
                created_by="tester",
                correlation_id=uuid.uuid4(),
                metadata_json={
                    "budget_limit": 10,
                    "clarification_rounds": 1,
                    "feasibility_verdict": "FEASIBLE",
                    "execution_boundary_locked": False,
                    "runtime_plan": {"proposed_steps": ["confirm", "build"]},
                },
            ),
            GoalSpecModel(
                id=uuid.uuid4(),
                goal_id=goal_id,
                version=1,
                status="DRAFT",
                content_hash=canonical_hash(content),
                **content,
            ),
            ConversationModel(
                id=uuid.uuid4(),
                app_project_id=project_id,
                title="TownDemo",
                status="ACTIVE",
                created_by="tester",
                metadata_json={"type": "APP"},
            ),
        ))
    return project_id, goal_id


@pytest.mark.asyncio
async def test_numbered_boundary_answers_without_blockers_count_round(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    project_id, goal_id = await _seed_advisory_draft(db_sessions)
    provider = MagicMock()
    generated = MagicMock()
    # The LLM would misclassify the confirmation as a QUERY; the
    # deterministic numbered-answer override must win.
    generated.output = GuidanceInterpretation(command_type="QUERY", summary="状态询问")
    generated.model = "test-model"
    provider.generate_structured = AsyncMock(return_value=generated)

    service = AppGuidanceService(db_sessions, provider)
    receipt = await service.guide(
        project_id,
        message=(
            "边界与验收确认：\n"
            "1. 最小范围同意，不做登录和持久化；\n"
            "2. 验收标准同意；\n"
            "3. 预算同意，采用保守默认值。"
        ),
        actor="tester",
    )
    assert receipt.command_type == "CORRECT"
    assert receipt.requires_confirmation is True

    async with db_sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        assert goal is not None
        meta = dict(goal.metadata_json or {})
        assert meta.get("clarification_rounds") == 2
        assert meta.get("goal_phase") == "DRAFT_CONFIRMABLE"
        assert meta.get("confirmation_state") == "PENDING"
        latest = await session.scalar(
            select(GoalSpecModel)
            .where(GoalSpecModel.goal_id == goal_id)
            .order_by(GoalSpecModel.version.desc())
            .limit(1)
        )
        assert latest is not None
        current_hash = latest.content_hash

    # Full gate: confirm must succeed now (rounds=2, FEASIBLE, no blockers).
    confirmed = await AppProjectService(db_sessions, MagicMock()).confirm(
        project_id, actor="tester", expected_spec_hash=current_hash
    )
    assert confirmed.goal.status == "READY"
