"""ExecutionOrchestrator and event catalog unit tests."""

import uuid
from pathlib import Path

from regent.application.execution_events import (
    APP_BUILD_PASSED,
    APP_BUILD_REQUESTED,
    CAPABILITY_RESOLUTION_REQUESTED,
    CAPABILITY_RESOLUTION_SATISFIED,
    DEPENDENCY_RESOLUTION_REQUESTED,
    DISCOVERY_COMPLETED,
    DISCOVERY_ROUND_REQUESTED,
    GENERATION_RUN_REQUESTED,
    GOAL_EXECUTION_REQUESTED,
    P1_MAIN_CHAIN_EVENTS,
    PREVIEW_DEPLOYMENT_REQUESTED,
    PREVIEW_DEPLOYMENT_SUCCEEDED,
    REQUIREMENT_REQUESTED,
    REQUIREMENT_VALIDATED,
    WORKSPACE_SNAPSHOT_READY,
    EventEnvelope,
    make_idempotency_key,
    make_outbox_event,
)
from regent.application.execution_orchestrator import (
    ExecutionOrchestrator,
    get_p1_event_handlers,
)
from regent.infrastructure.models import OutboxEventModel


def test_p1_main_chain_events_has_14_events() -> None:
    """P1 main chain event catalog contains 14 event types."""
    assert len(P1_MAIN_CHAIN_EVENTS) == 14


def test_p1_main_chain_events_contains_all_expected_events() -> None:
    """P1 main chain event catalog contains all expected events."""
    expected = {
        GOAL_EXECUTION_REQUESTED,
        DISCOVERY_ROUND_REQUESTED,
        DISCOVERY_COMPLETED,
        REQUIREMENT_REQUESTED,
        REQUIREMENT_VALIDATED,
        CAPABILITY_RESOLUTION_REQUESTED,
        CAPABILITY_RESOLUTION_SATISFIED,
        GENERATION_RUN_REQUESTED,
        WORKSPACE_SNAPSHOT_READY,
        DEPENDENCY_RESOLUTION_REQUESTED,
        APP_BUILD_REQUESTED,
        APP_BUILD_PASSED,
        PREVIEW_DEPLOYMENT_REQUESTED,
        PREVIEW_DEPLOYMENT_SUCCEEDED,
    }
    assert set(P1_MAIN_CHAIN_EVENTS) == expected


def test_event_envelope_creation() -> None:
    """EventEnvelope can be created."""
    goal_id = uuid.uuid4()
    correlation_id = uuid.uuid4()
    envelope = EventEnvelope(
        event_type=DISCOVERY_ROUND_REQUESTED,
        aggregate_type="goal",
        aggregate_id=goal_id,
        aggregate_version=1,
        payload={"goal_id": str(goal_id), "round": 1},
        idempotency_key="discovery:test:123",
        correlation_id=correlation_id,
    )
    assert envelope.event_type == DISCOVERY_ROUND_REQUESTED
    assert envelope.aggregate_type == "goal"
    assert envelope.aggregate_id == goal_id
    assert envelope.aggregate_version == 1
    assert envelope.payload["round"] == 1
    assert envelope.idempotency_key == "discovery:test:123"
    assert envelope.correlation_id == correlation_id


def test_make_outbox_event_creates_model() -> None:
    """make_outbox_event factory creates OutboxEventModel."""
    goal_id = uuid.uuid4()
    correlation_id = uuid.uuid4()
    envelope = EventEnvelope(
        event_type=GOAL_EXECUTION_REQUESTED,
        aggregate_type="goal",
        aggregate_id=goal_id,
        aggregate_version=1,
        payload={"goal_id": str(goal_id)},
        correlation_id=correlation_id,
    )
    outbox_event = make_outbox_event(envelope)

    assert isinstance(outbox_event, OutboxEventModel)
    assert outbox_event.event_type == GOAL_EXECUTION_REQUESTED
    assert outbox_event.aggregate_type == "goal"
    assert outbox_event.aggregate_id == goal_id
    assert outbox_event.aggregate_version == 1
    assert outbox_event.payload == {"goal_id": str(goal_id)}
    assert outbox_event.status == "PENDING"
    assert outbox_event.correlation_id == correlation_id


def test_make_idempotency_key_format() -> None:
    """make_idempotency_key generates correct format with hash suffix."""
    import hashlib

    goal_id = uuid.uuid4()
    execution_event_id = "abc123"
    key = make_idempotency_key("discovery", goal_id, execution_event_id)
    expected_hash = hashlib.sha256(execution_event_id.encode()).hexdigest()[:16]
    assert key == f"discovery:{goal_id}:{expected_hash}"
    assert key.startswith("discovery:")
    assert str(goal_id) in key
    assert len(key) <= 255


def test_make_idempotency_key_is_deterministic() -> None:
    """Same inputs produce same idempotency key."""
    goal_id = uuid.uuid4()
    key1 = make_idempotency_key("discovery", goal_id, "event-1")
    key2 = make_idempotency_key("discovery", goal_id, "event-1")
    assert key1 == key2


def test_make_idempotency_key_differs_for_different_inputs() -> None:
    """Different inputs produce different idempotency keys."""
    goal_id_1 = uuid.uuid4()
    goal_id_2 = uuid.uuid4()
    key1 = make_idempotency_key("discovery", goal_id_1, "event-1")
    key2 = make_idempotency_key("discovery", goal_id_2, "event-1")
    key3 = make_idempotency_key("discovery", goal_id_1, "event-2")
    assert key1 != key2
    assert key1 != key3


def test_orchestrator_has_all_r2_to_r6_handlers() -> None:
    """ExecutionOrchestrator has handler methods for all R2-R6 events."""
    orchestrator = ExecutionOrchestrator(sessions=None)
    assert hasattr(orchestrator, "handle_discovery_round_requested")
    assert hasattr(orchestrator, "handle_discovery_completed")
    assert hasattr(orchestrator, "handle_requirement_requested")
    assert hasattr(orchestrator, "handle_requirement_validated")
    assert hasattr(orchestrator, "handle_capability_resolution_requested")
    assert hasattr(orchestrator, "handle_capability_resolution_satisfied")
    assert hasattr(orchestrator, "handle_generation_run_requested")
    assert hasattr(orchestrator, "handle_workspace_snapshot_ready")
    assert hasattr(orchestrator, "handle_dependency_resolution_requested")
    assert hasattr(orchestrator, "handle_app_build_requested")
    assert hasattr(orchestrator, "handle_app_build_passed")
    assert hasattr(orchestrator, "handle_preview_deployment_requested")
    assert hasattr(orchestrator, "handle_preview_deployment_succeeded")


def test_get_p1_event_handlers_maps_all_events() -> None:
    """get_p1_event_handlers returns a handler for every P1 main chain event."""
    orchestrator = ExecutionOrchestrator(sessions=None)
    handlers = get_p1_event_handlers(orchestrator)
    for event_type in P1_MAIN_CHAIN_EVENTS:
        assert event_type in handlers, f"missing handler for {event_type}"
        assert callable(handlers[event_type])


def test_orchestrator_accepts_optional_dependencies() -> None:
    """ExecutionOrchestrator can be created with optional P1 dependencies."""
    orchestrator = ExecutionOrchestrator(
        sessions=None,
        evidence_connector=None,
        model_provider=None,
        generator=None,
        workspace_writer=None,
        sandbox=None,
        materializer=None,
        deployment_provider=None,
        permits=None,
    )
    assert orchestrator._evidence_connector is None
    assert orchestrator._model_provider is None
    assert orchestrator._generator is None
    assert orchestrator._deployment_provider is None


def test_worker_creates_orchestrator_with_dependencies() -> None:
    """Worker source creates ExecutionOrchestrator with P1 dependencies."""
    worker_source = Path("core/src/regent/worker/main.py").read_text(encoding="utf-8")
    assert "evidence_connector=" in worker_source
    assert "model_provider=" in worker_source
    assert "deployment_provider=" in worker_source
    assert "permits=" in worker_source


def test_orchestrator_imports_all_services() -> None:
    """Orchestrator imports all R2-R8 service classes."""
    source = Path(
        "core/src/regent/application/execution_orchestrator.py"
    ).read_text(encoding="utf-8")
    assert "DiscoveryWorker" in source
    assert "ProductDiscoveryService" in source
    assert "RequirementRevisionService" in source
    assert "GenerationService" in source
    assert "BuildService" in source
    assert "ReleaseService" in source
    assert "IterationLoopService" in source
    assert "DeploymentSmokeTestService" in source
