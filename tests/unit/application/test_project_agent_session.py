"""M0: ProjectAgentSession ensure / require / single-active invariants."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.goal_execution_service import GoalExecutionService
from regent.application.p1_contracts import canonical_hash
from regent.application.project_agent_session import (
    ProjectAgentSessionService,
    default_session_workspace_uri,
)
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AppProjectModel,
    GoalModel,
    GoalSpecModel,
    ProjectAgentSessionModel,
)


async def _seed_ready_goal(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID]:
    project_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    spec_id = uuid.uuid4()
    content = {
        "explicit_constraints": {},
        "system_inferences": {"first_deliverable": "preview"},
        "unknowns": [],
        "success_criteria": {"preview": "ok"},
        "source_refs": [],
    }
    async with sessions() as session, session.begin():
        session.add_all(
            (
                AppProjectModel(
                    id=project_id,
                    name="session-test",
                    product_intent="test session chassis",
                    status="ACTIVE",
                    created_by="test",
                ),
                GoalModel(
                    id=goal_id,
                    app_project_id=project_id,
                    original_input="build a tiny app",
                    status="READY",
                    version=1,
                    created_by="test",
                    correlation_id=uuid.uuid4(),
                    metadata_json={
                        "execution_stage": "NOT_STARTED",
                        "budget_limit": 10.0,
                        "execution_boundary_locked": True,
                        "locked_spec_hash": canonical_hash(content),
                        "locked_spec_version": 1,
                    },
                ),
                GoalSpecModel(
                    id=spec_id,
                    goal_id=goal_id,
                    version=1,
                    status="FROZEN",
                    confirmed_by="test",
                    content_hash=canonical_hash(content),
                    explicit_constraints=content["explicit_constraints"],
                    system_inferences=content["system_inferences"],
                    unknowns=content["unknowns"],
                    success_criteria=content["success_criteria"],
                    source_refs=content["source_refs"],
                ),
            )
        )
    return project_id, goal_id


@pytest.mark.asyncio
async def test_ensure_active_session_creates_one(
    db_sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    project_id, goal_id = await _seed_ready_goal(db_sessions)
    svc = ProjectAgentSessionService(db_sessions, workspace_root=tmp_path)
    view = await svc.ensure_active_session(
        app_project_id=project_id, goal_id=goal_id, actor="test"
    )
    assert view.status == "ACTIVE"
    assert view.app_project_id == project_id
    assert view.goal_id == goal_id
    assert Path(view.workspace_uri).is_dir()
    again = await svc.ensure_active_session(
        app_project_id=project_id, goal_id=goal_id, actor="test"
    )
    assert again.id == view.id
    assert again.epoch == view.epoch


@pytest.mark.asyncio
async def test_require_active_fails_without_session(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    project_id, _goal_id = await _seed_ready_goal(db_sessions)
    svc = ProjectAgentSessionService(db_sessions)
    with pytest.raises(DomainError) as exc:
        await svc.require_active(project_id)
    assert exc.value.code == ErrorCode.INVALID_STATE


@pytest.mark.asyncio
async def test_cannot_have_two_active_sessions_same_project(
    db_sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    project_id, goal_id = await _seed_ready_goal(db_sessions)
    svc = ProjectAgentSessionService(db_sessions, workspace_root=tmp_path)
    first = await svc.ensure_active_session(
        app_project_id=project_id, goal_id=goal_id, actor="test"
    )
    # Second ensure must rebind, not create a second ACTIVE row.
    second = await svc.ensure_active_session(
        app_project_id=project_id, goal_id=goal_id, actor="test"
    )
    assert first.id == second.id
    async with db_sessions() as session:
        from sqlalchemy import func, select

        count = await session.scalar(
            select(func.count())
            .select_from(ProjectAgentSessionModel)
            .where(
                ProjectAgentSessionModel.app_project_id == project_id,
                ProjectAgentSessionModel.status == "ACTIVE",
            )
        )
    assert count == 1


@pytest.mark.asyncio
async def test_goal_start_ensures_session(
    db_sessions: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "regent.application.project_agent_session.get_settings",
        lambda: type("S", (), {"workspace_root": str(tmp_path)})(),
    )
    project_id, goal_id = await _seed_ready_goal(db_sessions)
    receipt = await GoalExecutionService(db_sessions).start(
        goal_id, actor="test", idempotency_key="session-m0-start"
    )
    assert receipt.status == "ACTIVE"
    svc = ProjectAgentSessionService(db_sessions, workspace_root=tmp_path)
    view = await svc.require_active(project_id)
    assert view.goal_id == goal_id
    async with db_sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        assert goal is not None
        assert goal.metadata_json.get("project_agent_session_id") == str(view.id)
        assert goal.metadata_json.get("project_agent_session_workspace_uri") == view.workspace_uri


@pytest.mark.asyncio
async def test_assert_resume_epoch_rejects_stale(
    db_sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    project_id, goal_id = await _seed_ready_goal(db_sessions)
    svc = ProjectAgentSessionService(db_sessions, workspace_root=tmp_path)
    view = await svc.ensure_active_session(
        app_project_id=project_id, goal_id=goal_id, actor="test"
    )
    bumped = await svc.bump_epoch(project_id)
    assert bumped.epoch == view.epoch + 1
    with pytest.raises(DomainError) as exc:
        await svc.assert_resume_epoch(
            project_id, session_id=view.id, epoch=view.epoch
        )
    assert exc.value.code == ErrorCode.INVALID_STATE
    ok = await svc.assert_resume_epoch(
        project_id, session_id=view.id, epoch=bumped.epoch
    )
    assert ok.epoch == bumped.epoch


@pytest.mark.asyncio
async def test_bind_generation_run_sets_last_run(
    db_sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    project_id, goal_id = await _seed_ready_goal(db_sessions)
    svc = ProjectAgentSessionService(db_sessions, workspace_root=tmp_path)
    await svc.ensure_active_session(
        app_project_id=project_id, goal_id=goal_id, actor="test"
    )
    run_id = uuid.uuid4()
    view = await svc.bind_generation_run(project_id, generation_run_id=run_id)
    assert view is not None
    assert view.last_generation_run_id == run_id
    assert view.epoch == 0


def test_failures_segment_includes_session_resume_brief() -> None:
    from regent.agent.context_assembler import ContextAssembler
    from regent.agent.tools import WorkspaceToolkit
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        toolkit = WorkspaceToolkit(root)
        plan = {
            "acceptance_contract": {
                "session_resume_brief": "Continue session abc epoch=2",
                "project_agent_session_workspace_uri": str(root),
                "delivery_gap_reasons": ["TEST_FAILED: boom"],
            }
        }
        assembler = ContextAssembler(plan=plan, toolkit=toolkit, gaps=None)
        text = assembler._failures_segment()
        assert "Session resume:" in text
        assert "Durable session workspace:" in text
        assert "TEST_FAILED" in text


def test_seed_session_conversation_from_transcript(tmp_path: Path) -> None:
    from regent.agent.agent_runner import _seed_session_conversation
    import json

    (tmp_path / ".regent_agent_transcript.json").write_text(
        json.dumps(
            [
                {"role": "user", "content": "build the app"},
                {"role": "assistant", "content": "I will edit app.py"},
                {"role": "tool", "content": "ok", "tool_name": "edit_file"},
            ]
        ),
        encoding="utf-8",
    )
    msgs = _seed_session_conversation(
        {
            "project_agent_session_id": "sess-1",
            "session_resume_brief": "Continue sess-1 epoch=3",
        },
        toolkit_root=tmp_path,
    )
    assert msgs
    assert msgs[0].role == "user"
    assert "Session resume" in (msgs[0].content or "")
    assert any(m.role == "assistant" for m in msgs)


@pytest.mark.asyncio
async def test_pause_and_resume_session_status(
    db_sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    project_id, goal_id = await _seed_ready_goal(db_sessions)
    svc = ProjectAgentSessionService(db_sessions, workspace_root=tmp_path)
    await svc.ensure_active_session(
        app_project_id=project_id, goal_id=goal_id, actor="test"
    )
    paused = await svc.pause(project_id, actor="test")
    assert paused.status == "PAUSED"
    with pytest.raises(DomainError):
        await svc.require_active(project_id)
    resumed = await svc.resume_from_paused(project_id, goal_id=goal_id)
    assert resumed.status == "ACTIVE"
    assert resumed.epoch >= 1

