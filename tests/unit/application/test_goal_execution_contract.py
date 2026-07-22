from pathlib import Path

from regent.api.main import create_app
from regent.application.execution_events import P1_MAIN_CHAIN_EVENTS
from regent.infrastructure.models import AppPreviewReleaseModel, OutboxEventModel
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable


def test_start_goal_route_is_registered() -> None:
    paths = set(create_app().openapi()["paths"])
    assert "/v1/goals/{goal_id}/start" in paths


def test_worker_does_not_import_preview_service() -> None:
    worker_source = Path("core/src/regent/worker/main.py").read_text(encoding="utf-8")
    assert "AppPreviewService" not in worker_source
    assert "previews.generate" not in worker_source


def test_worker_registers_discovery_round_handler() -> None:
    worker_source = Path("core/src/regent/worker/main.py").read_text(encoding="utf-8")
    assert "ExecutionOrchestrator" in worker_source
    assert "get_p1_event_handlers" in worker_source


def test_worker_registers_all_p1_main_chain_events() -> None:
    """Event catalog has a handler for every P1 main chain event type."""
    orchestrator_source = Path(
        "core/src/regent/application/execution_orchestrator.py"
    ).read_text(encoding="utf-8")
    assert "get_p1_event_handlers" in orchestrator_source
    for event_type in P1_MAIN_CHAIN_EVENTS:
        assert event_type in orchestrator_source, f"missing handler for {event_type}"


def test_event_catalog_contains_all_p1_events() -> None:
    """Event catalog contains all 14 P1 main chain event types."""
    assert len(P1_MAIN_CHAIN_EVENTS) == 14
    expected = {
        "GoalExecutionRequested",
        "DiscoveryRoundRequested",
        "DiscoveryCompleted",
        "RequirementRequested",
        "RequirementValidated",
        "CapabilityResolutionRequested",
        "CapabilityResolutionSatisfied",
        "GenerationRunRequested",
        "WorkspaceSnapshotReady",
        "DependencyResolutionRequested",
        "AppBuildRequested",
        "AppBuildPassed",
        "PreviewDeploymentRequested",
        "PreviewDeploymentSucceeded",
    }
    assert set(P1_MAIN_CHAIN_EVENTS) == expected


def test_orchestrator_does_not_call_preview_generate() -> None:
    """ExecutionOrchestrator.handle_goal_execution has no previews.generate call."""
    source = Path(
        "core/src/regent/application/execution_orchestrator.py"
    ).read_text(encoding="utf-8")
    assert "previews.generate" not in source
    assert "AppPreviewService" not in source


def test_orchestrator_creates_discovery_round() -> None:
    """ExecutionOrchestrator creates DiscoveryRound."""
    source = Path(
        "core/src/regent/application/execution_orchestrator.py"
    ).read_text(encoding="utf-8")
    assert "DiscoveryRoundModel" in source
    assert "DISCOVERY_ROUND_REQUESTED" in source
    assert "idempotency_key" in source


def test_goal_execution_service_does_not_store_stage_as_fact_source() -> None:
    """GoalExecutionService no longer stores execution_stage as fact source."""
    source = Path(
        "core/src/regent/application/goal_execution_service.py"
    ).read_text(encoding="utf-8")
    assert '"execution_stage": "QUEUED"' not in source
    assert "execution_event_id" in source


def test_app_guidance_service_projects_execution_stage() -> None:
    """AppGuidanceService projects execution_stage from underlying objects."""
    source = Path(
        "core/src/regent/application/app_guidance_service.py"
    ).read_text(encoding="utf-8")
    assert "_project_execution_stage" in source
    assert "DiscoveryRoundModel" in source
    assert "DEPLOYED" in source
    assert "DISCOVERING" in source
    assert "NOT_STARTED" in source


def test_confirmation_message_carries_frozen_version_token() -> None:
    source = Path("core/src/regent/application/app_project_service.py").read_text(
        encoding="utf-8"
    )
    assert '"goal_spec_hash": spec.content_hash' in source
    assert '"goal_spec_version": spec.version' in source


def test_outbox_dead_letter_and_preview_failure_summary_are_persistent() -> None:
    outbox_ddl = str(
        CreateTable(OutboxEventModel.__table__).compile(dialect=postgresql.dialect())
    )
    preview_ddl = str(
        CreateTable(AppPreviewReleaseModel.__table__).compile(dialect=postgresql.dialect())
    )
    assert "DEAD_LETTER" in outbox_ddl
    assert "failure_summary" in preview_ddl


def test_console_starts_goal_and_polls_persistent_progress() -> None:
    console = Path("apps/regent-console/index.html").read_text(encoding="utf-8")
    assert "`/v1/goals/${goalId}/start`" in console
    assert "execution_stage" in console
    assert "await refresh()" in console
    assert "},3000)" in console
