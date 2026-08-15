from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from regent.application.app_guidance_service import AppGuidanceService, GuidanceInterpretation
from regent.application.app_project_service import AppProjectService
from regent.application.p1_contracts import canonical_hash
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AppProjectModel,
    ConversationMessageModel,
    ConversationModel,
    GoalModel,
    GoalSpecModel,
    OutboxEventModel,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def _seed(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, str]:
    project_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    content = {
        "explicit_constraints": {"budget": "10"},
        "system_inferences": {"app_name": "Demo", "first_deliverable": "preview"},
        "unknowns": [{"question": "approved scope", "blocking": True}],
        "success_criteria": {"preview": "works"},
        "source_refs": [],
    }
    spec_hash = canonical_hash(content)
    async with sessions() as session, session.begin():
        session.add_all((
            AppProjectModel(
                id=project_id,
                name="Demo",
                product_intent="test confirmation transition",
                status="DRAFT",
                created_by="tester",
            ),
            GoalModel(
                id=goal_id,
                app_project_id=project_id,
                original_input="build demo",
                status="DRAFT",
                version=0,
                created_by="tester",
                correlation_id=uuid.uuid4(),
                metadata_json={
                    "budget_limit": 10,
                    "clarification_rounds": 1,
                    "feasibility_verdict": "REVISION_REQUIRED",
                    "execution_boundary_locked": False,
                    "runtime_plan": {"proposed_steps": ["confirm", "build"]},
                },
            ),
            GoalSpecModel(
                id=uuid.uuid4(),
                goal_id=goal_id,
                version=1,
                status="DRAFT",
                content_hash=spec_hash,
                **content,
            ),
            ConversationModel(
                id=uuid.uuid4(),
                app_project_id=project_id,
                title="Demo",
                status="ACTIVE",
                created_by="tester",
                metadata_json={"type": "APP"},
            ),
        ))
    return project_id, goal_id, spec_hash


@pytest.mark.asyncio
async def test_correction_that_becomes_ready_persists_latest_confirmation_gate(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    project_id, goal_id, _ = await _seed(db_sessions)
    receipt = await AppGuidanceService(db_sessions, MagicMock())._handle_correct(
        project_id,
        "scope approved",
        "tester",
        GuidanceInterpretation(
            command_type="CORRECT",
            summary="scope approved",
            correction_target="requirements",
            correction_detail="scope approved",
            unknowns=[],
            feasibility_verdict="FEASIBLE",
            feasibility_reasons=["scope, budget and acceptance are testable"],
        ),
        "regent-core:test",
    )
    assert receipt.requires_confirmation is True
    assert receipt.resulting_goal_id == goal_id

    async with db_sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        latest = await session.scalar(
            select(GoalSpecModel)
            .where(GoalSpecModel.goal_id == goal_id)
            .order_by(GoalSpecModel.version.desc())
            .limit(1)
        )
        gate = await session.scalar(
            select(ConversationMessageModel)
            .where(ConversationMessageModel.message_type == "APP_CONFIRMATION_REQUIRED")
        )
        execution = await session.scalar(
            select(OutboxEventModel).where(
                OutboxEventModel.aggregate_id == goal_id,
                OutboxEventModel.event_type == "GoalExecutionRequested",
            )
        )
        assert goal is not None and latest is not None and gate is not None
        assert goal.status == "DRAFT"
        assert goal.metadata_json["goal_phase"] == "DRAFT_CONFIRMABLE"
        assert goal.metadata_json["confirmation_state"] == "PENDING"
        assert gate.metadata_json["goal_spec_version"] == latest.version == 2
        assert gate.metadata_json["goal_spec_hash"] == latest.content_hash
        assert gate.metadata_json["gate_key"] == f"goal:{goal_id}:spec:2:confirm"
        assert execution is None


@pytest.mark.asyncio
async def test_correction_with_blocker_only_persists_clarification(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    project_id, goal_id, _ = await _seed(db_sessions)
    receipt = await AppGuidanceService(db_sessions, MagicMock())._handle_correct(
        project_id,
        "still unknown",
        "tester",
        GuidanceInterpretation(
            command_type="CORRECT",
            summary="budget is unresolved",
            correction_target="requirements",
            correction_detail="still unknown",
            unknowns=["approved budget"],
            feasibility_verdict="REVISION_REQUIRED",
        ),
        "regent-core:test",
    )
    assert receipt.requires_confirmation is False
    async with db_sessions() as session:
        gate = await session.scalar(
            select(ConversationMessageModel).where(
                ConversationMessageModel.message_type == "APP_CONFIRMATION_REQUIRED"
            )
        )
        clarification = await session.scalar(
            select(ConversationMessageModel).where(
                ConversationMessageModel.message_type == "CLARIFICATION_REQUIRED"
            )
        )
        goal = await session.get(GoalModel, goal_id)
        assert gate is None
        assert clarification is not None and "approved budget" in clarification.content
        assert goal is not None and goal.metadata_json["goal_phase"] == "DRAFT_CLARIFYING"


@pytest.mark.asyncio
async def test_confirm_rejects_old_hash_with_current_version_details(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    project_id, _, old_hash = await _seed(db_sessions)
    await AppGuidanceService(db_sessions, MagicMock())._handle_correct(
        project_id,
        "scope approved",
        "tester",
        GuidanceInterpretation(
            command_type="CORRECT",
            summary="ready",
            correction_target="requirements",
            correction_detail="scope approved",
            unknowns=[],
            feasibility_verdict="FEASIBLE",
        ),
        "regent-core:test",
    )
    with pytest.raises(DomainError) as exc:
        await AppProjectService(db_sessions, MagicMock()).confirm(
            project_id, actor="tester", expected_spec_hash=old_hash
        )
    assert exc.value.code == ErrorCode.VERSION_CONFLICT
    assert exc.value.details is not None
    assert exc.value.details["current_spec_version"] == 2
    assert exc.value.details["current_spec_hash"] != old_hash


@pytest.mark.asyncio
async def test_confirm_current_hash_is_idempotent_and_locks_only_latest_spec(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    project_id, goal_id, _ = await _seed(db_sessions)
    await AppGuidanceService(db_sessions, MagicMock())._handle_correct(
        project_id,
        "scope approved",
        "tester",
        GuidanceInterpretation(
            command_type="CORRECT",
            summary="ready",
            correction_target="requirements",
            correction_detail="scope approved",
            unknowns=[],
            feasibility_verdict="FEASIBLE",
        ),
        "regent-core:test",
    )
    async with db_sessions() as session:
        latest = await session.scalar(
            select(GoalSpecModel)
            .where(GoalSpecModel.goal_id == goal_id)
            .order_by(GoalSpecModel.version.desc())
            .limit(1)
        )
        assert latest is not None
        current_hash = latest.content_hash

    service = AppProjectService(db_sessions, MagicMock())
    first = await service.confirm(project_id, actor="tester", expected_spec_hash=current_hash)
    second = await service.confirm(project_id, actor="tester", expected_spec_hash=current_hash)
    assert first.spec.id == second.spec.id

    async with db_sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        messages = list((await session.scalars(
            select(ConversationMessageModel).where(
                ConversationMessageModel.message_type == "GOAL_CONFIRMED"
            )
        )).all())
        specs = list((await session.scalars(
            select(GoalSpecModel)
            .where(GoalSpecModel.goal_id == goal_id)
            .order_by(GoalSpecModel.version)
        )).all())
        assert goal is not None
        assert goal.status == "READY"
        assert goal.metadata_json["execution_boundary_locked"] is True
        assert goal.metadata_json["confirmation_state"] == "USED"
        assert goal.metadata_json["locked_spec_hash"] == current_hash
        assert [spec.status for spec in specs] == ["SUPERSEDED", "FROZEN"]
        assert len(messages) == 1
