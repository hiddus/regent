"""M1: session workspace binding + SESSION_RESUME recovery path."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.agent.generator import AgenticCodeGenerator, _ensure_session_workspace
from regent.application.delivery_gap_recovery import DeliveryGapRecoveryService
from regent.application.p1_contracts import canonical_hash
from regent.application.project_agent_session import ProjectAgentSessionService
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.models import (
    AppProjectModel,
    GoalModel,
    GoalSpecModel,
    OutboxEventModel,
)


async def _seed_active_with_session(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    project_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    rev_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    content = {
        "explicit_constraints": {},
        "system_inferences": {},
        "unknowns": [],
        "success_criteria": {},
        "source_refs": [],
    }
    async with sessions() as session, session.begin():
        session.add_all(
            (
                AppProjectModel(
                    id=project_id,
                    name="m1-session",
                    product_intent="todo",
                    status="ACTIVE",
                    created_by="test",
                ),
                GoalModel(
                    id=goal_id,
                    app_project_id=project_id,
                    original_input="minimal todo",
                    status="ACTIVE",
                    version=1,
                    created_by="test",
                    correlation_id=uuid.uuid4(),
                    metadata_json={"execution_stage": "GENERATING"},
                ),
                GoalSpecModel(
                    id=uuid.uuid4(),
                    goal_id=goal_id,
                    version=1,
                    status="FROZEN",
                    content_hash=canonical_hash(content),
                    explicit_constraints={},
                    system_inferences={},
                    unknowns=[],
                    success_criteria={},
                    source_refs=[],
                ),
            )
        )
    svc = ProjectAgentSessionService(sessions, workspace_root=tmp_path)
    view = await svc.ensure_active_session(
        app_project_id=project_id, goal_id=goal_id, actor="test"
    )
    marker = Path(view.workspace_uri) / "keep_me.py"
    marker.write_text("print('keep')\n", encoding="utf-8")
    # Mirror GoalExecutionService.start stamping so recover prefers SESSION_RESUME.
    async with sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id)
        assert goal is not None
        meta = dict(goal.metadata_json or {})
        meta["project_agent_session_id"] = str(view.id)
        meta["project_agent_session_epoch"] = view.epoch
        meta["project_agent_session_workspace_uri"] = view.workspace_uri
        goal.metadata_json = meta
    return project_id, goal_id, rev_id, plan_id


@pytest.mark.asyncio
async def test_session_workspace_not_wiped(tmp_path: Path) -> None:
    session_root = tmp_path / "projects" / "p1" / "agent"
    session_root.mkdir(parents=True)
    (session_root / "app.py").write_text("x=1\n", encoding="utf-8")
    base = tmp_path / "base"
    base.mkdir()
    (base / "other.py").write_text("y=2\n", encoding="utf-8")
    _ensure_session_workspace(session_root, base)
    assert (session_root / "app.py").read_text(encoding="utf-8") == "x=1\n"
    assert not (session_root / "other.py").exists()


@pytest.mark.asyncio
async def test_generator_resolves_session_workspace(tmp_path: Path) -> None:
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    gen = AgenticCodeGenerator(
        MagicMock(),
        artifacts,
        workspace_root=tmp_path / "ws",
    )
    session_ws = tmp_path / "session_agent"
    session_ws.mkdir()
    plan = {
        "generation_run_id": "run-1",
        "acceptance_contract": {
            "project_agent_session_workspace_uri": str(session_ws),
        },
    }
    path, used = gen._resolve_sandbox(plan, "run-1")
    assert used is True
    assert path == session_ws


@pytest.mark.asyncio
async def test_recover_asks_human_instead_of_auto_resume(
    db_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A0: VerificationGap must ASK_HUMAN, not silent SESSION_RESUME."""
    monkeypatch.setattr(
        "regent.application.delivery_gap_recovery.get_settings",
        lambda: type(
            "S",
            (),
            {
                "agent_session_resume_enabled": True,
                "agent_loop_exit_enforced": True,
                "workspace_root": str(tmp_path),
                "delivery_profile": "balanced",
            },
        )(),
    )
    monkeypatch.setattr(
        "regent.application.project_agent_session.get_settings",
        lambda: type("S", (), {"workspace_root": str(tmp_path)})(),
    )

    async def _fake_cap(_sessions):
        return uuid.uuid4()

    monkeypatch.setattr(
        "regent.application.delivery_gap_recovery.ensure_product_surface_capability",
        _fake_cap,
    )
    monkeypatch.setattr(
        "regent.application.delivery_gap_recovery.ensure_delivery_review_capability",
        _fake_cap,
    )
    monkeypatch.setattr(
        "regent.application.delivery_gap_recovery.ensure_allowlisted_http_capability",
        _fake_cap,
    )

    project_id, goal_id, rev_id, plan_id = await _seed_active_with_session(
        db_sessions, tmp_path
    )
    result = await DeliveryGapRecoveryService(db_sessions).recover(
        goal_id=goal_id,
        project_id=project_id,
        requirement_revision_id=rev_id,
        capability_resolution_plan_id=plan_id,
        actor="test",
        gap_reasons=["TEST_FAILED: project tests failed"],
    )
    assert result.recovered is False
    assert result.method == "ASK_HUMAN"
    assert result.terminal_exhaust is True

    async with db_sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        assert goal is not None
        meta = dict(goal.metadata_json or {})
        exit_row = dict(meta.get("agent_loop_exit") or {})
        assert exit_row.get("exit_kind") == "ASK_HUMAN"
        assert exit_row.get("ask_envelope", {}).get("question")
        assert meta.get("awaiting_human_intervention") is True
        events = (
            await session.scalars(
                select(OutboxEventModel).where(
                    OutboxEventModel.aggregate_id == goal_id,
                    OutboxEventModel.event_type == "GenerationRunRequested",
                )
            )
        ).all()
        assert len(events) == 0


@pytest.mark.asyncio
async def test_recover_legacy_session_resume_when_exit_not_enforced(
    db_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "regent.application.delivery_gap_recovery.get_settings",
        lambda: type(
            "S",
            (),
            {
                "agent_session_resume_enabled": True,
                "agent_loop_exit_enforced": False,
                "workspace_root": str(tmp_path),
                "delivery_profile": "balanced",
            },
        )(),
    )
    monkeypatch.setattr(
        "regent.application.project_agent_session.get_settings",
        lambda: type("S", (), {"workspace_root": str(tmp_path)})(),
    )

    async def _fake_cap(_sessions):
        return uuid.uuid4()

    monkeypatch.setattr(
        "regent.application.delivery_gap_recovery.ensure_product_surface_capability",
        _fake_cap,
    )
    monkeypatch.setattr(
        "regent.application.delivery_gap_recovery.ensure_delivery_review_capability",
        _fake_cap,
    )
    monkeypatch.setattr(
        "regent.application.delivery_gap_recovery.ensure_allowlisted_http_capability",
        _fake_cap,
    )

    project_id, goal_id, rev_id, plan_id = await _seed_active_with_session(
        db_sessions, tmp_path
    )
    result = await DeliveryGapRecoveryService(db_sessions).recover(
        goal_id=goal_id,
        project_id=project_id,
        requirement_revision_id=rev_id,
        capability_resolution_plan_id=plan_id,
        actor="test",
        gap_reasons=["TEST_FAILED: project tests failed"],
    )
    assert result.recovered is True
    assert result.method == "SESSION_RESUME"


@pytest.mark.asyncio
async def test_gate_reorg_bypassed_when_session_active(
    db_sessions: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch
) -> None:
    """I-E: prepare_gate_reorganization must not ATTRIBUTE_3 while Session ACTIVE."""

    async def _fake_cap(_sessions):
        return uuid.uuid4()

    monkeypatch.setattr(
        "regent.application.delivery_gap_recovery.ensure_product_surface_capability",
        _fake_cap,
    )
    monkeypatch.setattr(
        "regent.application.delivery_gap_recovery.ensure_delivery_review_capability",
        _fake_cap,
    )
    monkeypatch.setattr(
        "regent.application.delivery_gap_recovery.ensure_allowlisted_http_capability",
        _fake_cap,
    )

    project_id, goal_id, _rev_id, _plan_id = await _seed_active_with_session(
        db_sessions, tmp_path
    )
    result = await DeliveryGapRecoveryService(db_sessions).prepare_gate_reorganization(
        goal_id=goal_id,
        project_id=project_id,
        actor="test",
        gate_status="FAIL",
    )
    assert result.recovered is False
    assert result.method == "SESSION_ACTIVE"
    svc = ProjectAgentSessionService(db_sessions, workspace_root=tmp_path)
    view = await svc.require_active(project_id)
    assert (Path(view.workspace_uri) / "keep_me.py").exists()
    assert view.epoch >= 0
