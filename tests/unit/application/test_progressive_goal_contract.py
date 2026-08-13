from pathlib import Path

ROOT = Path("core/src/regent")


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_draft_creation_waits_for_explicit_start() -> None:
    routes = source("api/app_projects.py")
    assert "GoalExecutionService(request.app.state.sessions).start" not in routes
    assert "auto_started=False" in routes


def test_explicit_start_freezes_draft_and_preserves_unknowns() -> None:
    service = source("application/goal_execution_service.py")
    assert 'if goal.status == "DRAFT"' in service
    assert 'action="SNAPSHOT_GOAL_SPEC_FOR_EXECUTION"' in service
    assert "spec.confirmed_by = actor" in service
    assert '"explicit_user_start": True' in service
    assert 'meta.get("budget_limit")' in service
    assert '"EXPLORING" if spec.unknowns else "PROVISIONAL"' in service


def test_draft_goal_can_be_planned_and_organized() -> None:
    planning = source("application/planning_service.py")
    organization = source("application/organization_service.py")
    assert '{"DRAFT", "READY", "ACTIVE"}' in planning
    assert '{"DRAFT", "READY", "ACTIVE"}' in organization
    assert "bounded discovery work instead of inventing" in planning


def test_correction_creates_new_goal_spec_version() -> None:
    guidance = source("application/app_guidance_service.py")
    assert "version=latest_spec.version + 1" in guidance
    assert 'latest_spec.status = "SUPERSEDED"' in guidance
    assert '"progressive_corrections"' in guidance
    assert '"latest_goal_spec_version"' in guidance


def test_console_describes_provisional_understanding_not_required_confirmation() -> None:
    app = Path("apps/regent-console/src/App.tsx").read_text(encoding="utf-8")
    messages = Path("apps/regent-console/src/components/MessageList.tsx").read_text(
        encoding="utf-8"
    )
    assert "Core 已基于当前理解开始探索" in app
    assert "只有确认后才开始执行" not in messages
    assert "GOAL_UNDERSTANDING_READY" in messages
