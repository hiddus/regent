"""ExecutionOrchestrator and event catalog unit tests."""

import uuid
from pathlib import Path

import pytest
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
    QUALITY_APPROVAL_COMPLETED,
    QUALITY_APPROVAL_REQUESTED,
    RELEASE_APPROVAL_COMPLETED,
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


def test_p1_main_chain_events_has_17_events() -> None:
    """P1 main chain event catalog contains 17 event types (incl. release approval)."""
    assert len(P1_MAIN_CHAIN_EVENTS) == 17


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
        QUALITY_APPROVAL_REQUESTED,
        QUALITY_APPROVAL_COMPLETED,
        RELEASE_APPROVAL_COMPLETED,
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
    """ExecutionOrchestrator has handler methods for all R2-R6 events.

    Updated after simplification: handle_requirement_validated and
    handle_app_build_passed were removed (bypass pipeline).
    """
    orchestrator = ExecutionOrchestrator(sessions=None)
    assert hasattr(orchestrator, "handle_discovery_round_requested")
    assert hasattr(orchestrator, "handle_discovery_completed")
    assert hasattr(orchestrator, "handle_requirement_requested")
    assert hasattr(orchestrator, "handle_capability_resolution_requested")
    assert hasattr(orchestrator, "handle_capability_resolution_satisfied")
    assert hasattr(orchestrator, "handle_generation_run_requested")
    assert hasattr(orchestrator, "handle_workspace_snapshot_ready")
    assert hasattr(orchestrator, "handle_dependency_resolution_requested")
    assert hasattr(orchestrator, "handle_app_build_requested")
    assert hasattr(orchestrator, "handle_preview_deployment_requested")
    assert hasattr(orchestrator, "handle_preview_deployment_succeeded")
    assert hasattr(orchestrator, "handle_release_approval_completed")


def test_get_p1_event_handlers_maps_all_events() -> None:
    """get_p1_event_handlers returns a handler for every P1 main chain event.

    Updated after simplification: REQUIREMENT_VALIDATED and APP_BUILD_PASSED
    are bypassed and no longer have handlers.
    """
    orchestrator = ExecutionOrchestrator(sessions=None)
    handlers = get_p1_event_handlers(orchestrator)
    # Events that were bypassed (no handlers needed).
    bypassed_events = {"RequirementValidated", "AppBuildPassed"}
    for event_type in P1_MAIN_CHAIN_EVENTS:
        if event_type in bypassed_events:
            continue
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


def test_deploy_failure_does_not_fake_complete_or_achieve() -> None:
    """GAC-A4: deploy miss must recover / wait-human — never ACHIEVE or FAIL/EXHAUST halt."""
    source = Path(
        "core/src/regent/application/execution_orchestrator.py"
    ).read_text(encoding="utf-8")
    assert "_recover_or_wait_after_deploy_gap" in source
    assert "ATTAINMENT_RECOVERY_STARTED" in source
    # Deploy status miss routes through recovery helper, not terminal FAIL/EXHAUST.
    assert "Deployment status=" in source
    deploy_block_start = source.index("if result.status != \"SUCCEEDED\":")
    deploy_block = source[deploy_block_start : deploy_block_start + 600]
    assert "_recover_or_wait_after_deploy_gap" in deploy_block
    assert "GoalCommand.FAIL" not in deploy_block
    assert "GoalCommand.EXHAUST" not in deploy_block
    assert "GoalCommand.ACHIEVE" not in deploy_block
    # Helper itself must wait for human when ladder exhausted — not ACHIEVE.
    helper_start = source.index("async def _recover_or_wait_after_deploy_gap")
    helper = source[helper_start : helper_start + 2500]
    assert "GoalCommand.WAIT_FOR_HUMAN" in helper
    assert "GoalCommand.ACHIEVE" not in helper
    assert "DeliveryGapRecoveryService" in helper


@pytest.mark.asyncio
async def test_recover_or_wait_after_deploy_gap_schedules_recovery() -> None:
    """Deploy gap with generation ids schedules DeliveryGapRecovery — no ACHIEVE."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from regent.application.delivery_gap_recovery import DeliveryGapRecoveryResult
    from regent.domain.transitions import GoalCommand

    goal_id = uuid.uuid4()
    project_id = uuid.uuid4()
    req_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    orchestrator = ExecutionOrchestrator(sessions=MagicMock())
    orchestrator._halt_goal_stage = AsyncMock()
    orchestrator._record_delivery_state = AsyncMock()
    orchestrator._resolve_generation_ids = AsyncMock(return_value=(req_id, plan_id))

    recovery = DeliveryGapRecoveryResult(
        True, "REUSE", "scheduled", 1, "product_surface"
    )
    with patch(
        "regent.application.execution_orchestrator.DeliveryGapRecoveryService"
    ) as svc_cls:
        svc_cls.return_value.recover = AsyncMock(return_value=recovery)
        await orchestrator._recover_or_wait_after_deploy_gap(
            goal_id,
            project_id,
            actor="test",
            stage="DEPLOY_NOT_SUCCEEDED",
            message="Deployment status=FAILED (GAC-A4).",
            gap_reasons=["deployment-status: FAILED"],
            extra={"status": "FAILED"},
        )

    orchestrator._halt_goal_stage.assert_awaited_once()
    halt_kwargs = orchestrator._halt_goal_stage.await_args.kwargs
    assert halt_kwargs["terminal"] is None
    assert halt_kwargs["event_type"] == "ATTAINMENT_RECOVERY_STARTED"
    svc_cls.return_value.recover.assert_awaited_once()
    orchestrator._record_delivery_state.assert_awaited()
    # Must not transition to ACHIEVE / FAIL / EXHAUST on recoverable path.
    assert halt_kwargs["terminal"] not in {
        GoalCommand.ACHIEVE,
        GoalCommand.FAIL,
        GoalCommand.EXHAUST,
    }


@pytest.mark.asyncio
async def test_recover_or_wait_after_deploy_gap_waits_human_when_exhausted() -> None:
    """When recovery ladder is exhausted, enter WAITING_HUMAN — not ACHIEVED/伪完成."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from regent.application.delivery_gap_recovery import DeliveryGapRecoveryResult
    from regent.domain.transitions import GoalCommand

    goal_id = uuid.uuid4()
    project_id = uuid.uuid4()
    orchestrator = ExecutionOrchestrator(sessions=MagicMock())
    orchestrator._halt_goal_stage = AsyncMock()
    orchestrator._record_delivery_state = AsyncMock()
    orchestrator._resolve_generation_ids = AsyncMock(
        return_value=(uuid.uuid4(), uuid.uuid4())
    )
    recovery = DeliveryGapRecoveryResult(
        False,
        "STOP",
        "ladder exhausted",
        3,
        "product_surface",
        terminal_exhaust=True,
    )
    with patch(
        "regent.application.execution_orchestrator.DeliveryGapRecoveryService"
    ) as svc_cls:
        svc_cls.return_value.recover = AsyncMock(return_value=recovery)
        await orchestrator._recover_or_wait_after_deploy_gap(
            goal_id,
            project_id,
            actor="test",
            stage="DEPLOY_FAILED",
            message="Preview deployment failed (GAC-A4): RuntimeError",
            gap_reasons=["deployment-failed: RuntimeError"],
        )

    assert orchestrator._halt_goal_stage.await_count == 2
    final_kwargs = orchestrator._halt_goal_stage.await_args_list[-1].kwargs
    assert final_kwargs["terminal"] == GoalCommand.WAIT_FOR_HUMAN
    assert final_kwargs["event_type"] == "HUMAN_TASK_REQUIRED"
    assert final_kwargs["terminal"] != GoalCommand.ACHIEVE
    assert "NEEDS_HUMAN" in final_kwargs["stage"]
    orchestrator._record_delivery_state.assert_awaited()


def test_progress_nodes_do_not_title_failed_outcome_as_complete() -> None:
    """Console progressNodes: failed/waiting halt must not keep the static title「完成」."""
    source = Path("apps/regent-console/src/lib/progressNodes.ts").read_text(
        encoding="utf-8"
    )
    assert "ATTAINMENT_RECOVERY_STARTED" in source
    assert "node.title = '未达成'" in source
    assert "node.title = '需要处理'" in source
    assert "node.title = '完成'" in source
    # Recovery events belong on generate, not outcome-as-complete.
    assert "目标未达成，正在重新规划并继续生成" in source
    # Exhausted / halted must be waiting (needs human), not calm「完成」.
    assert "GOAL_EXHAUSTED: { status: 'waiting'" in source
    assert "GOAL_EXECUTION_STAGE_HALTED: { status: 'waiting'" in source
    assert "自动路径已用尽，需要你介入后继续" in source


def test_orchestrator_has_no_unmet_goal_exhaust_commands() -> None:
    """Unmet delivery/goal paths must not call GoalCommand.EXHAUST."""
    source = Path(
        "core/src/regent/application/execution_orchestrator.py"
    ).read_text(encoding="utf-8")
    assert "GoalCommand.EXHAUST" not in source
    assert "DISCOVERY_NO_SELECT_NEEDS_HUMAN" in source
    assert "RESEARCH_MORE_NEEDS_HUMAN" in source
    assert "quality_rejected_needs_human" in source
    assert "gate_insufficient_timeout_needs_human" in source
    # Discovery non-SELECT recovers before human handoff.
    discovery_start = source.index("async def handle_discovery_completed")
    discovery = source[discovery_start : discovery_start + 3500]
    assert "ResearchMoreRecoveryService" in discovery
    assert "WAIT_FOR_HUMAN" in discovery
    assert "ATTAINMENT_RECOVERY_STARTED" in discovery


@pytest.mark.asyncio
async def test_discovery_no_select_waits_human_not_exhaust() -> None:
    """Non-SELECT discovery: try research-more, then WAITING_HUMAN — never EXHAUST."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from regent.application.research_more_recovery import ResearchMoreRecoveryResult
    from regent.domain.transitions import GoalCommand

    goal_id = uuid.uuid4()
    project_id = uuid.uuid4()
    round_id = uuid.uuid4()
    orchestrator = ExecutionOrchestrator(sessions=MagicMock())
    orchestrator._halt_goal_stage = AsyncMock()

    research = ResearchMoreRecoveryResult(
        False, "STOP", None, (), "need human sources"
    )
    with patch(
        "regent.application.execution_orchestrator.ResearchMoreRecoveryService"
    ) as svc_cls:
        svc_cls.return_value.recover = AsyncMock(return_value=research)
        await orchestrator.handle_discovery_completed(
            {
                "goal_id": str(goal_id),
                "app_project_id": str(project_id),
                "discovery_round_id": str(round_id),
                "decision": "REJECT",
                "selected_hypothesis_id": None,
                "actor": "test",
                "idempotency_key": "k1",
            }
        )

    assert orchestrator._halt_goal_stage.await_count == 2
    first = orchestrator._halt_goal_stage.await_args_list[0].kwargs
    assert first["terminal"] is None
    assert first["event_type"] == "ATTAINMENT_RECOVERY_STARTED"
    final = orchestrator._halt_goal_stage.await_args_list[-1].kwargs
    assert final["terminal"] == GoalCommand.WAIT_FOR_HUMAN
    assert final["event_type"] == "HUMAN_TASK_REQUIRED"
    assert "NEEDS_HUMAN" in final["stage"]
    assert final["terminal"] != GoalCommand.EXHAUST


@pytest.mark.asyncio
async def test_timer_insufficient_waits_human_not_exhaust() -> None:
    """Gate insufficient timeout → WAITING_HUMAN after recovery, never EXHAUST."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from regent.application.delivery_gap_recovery import DeliveryGapRecoveryResult
    from regent.domain.transitions import GoalCommand
    from regent.infrastructure.models import GoalModel

    goal_id = uuid.uuid4()
    project_id = uuid.uuid4()
    goal = GoalModel(
        id=goal_id,
        original_input="app",
        status="ACTIVE",
        version=3,
        created_by="test",
        correlation_id=uuid.uuid4(),
        app_project_id=project_id,
        metadata_json={"execution_stage": "GATE_INSUFFICIENT_EVIDENCE"},
    )

    session = AsyncMock()
    session.get = AsyncMock(return_value=goal)
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_context.__aexit__.return_value = None
    tx = AsyncMock()
    tx.__aenter__.return_value = None
    tx.__aexit__.return_value = None
    session.begin = MagicMock(return_value=tx)
    sessions = MagicMock(return_value=session_context)

    orchestrator = ExecutionOrchestrator(sessions=sessions)
    orchestrator._append_conversation_event = AsyncMock()
    orchestrator._resolve_generation_ids = AsyncMock(return_value=(None, None))

    reorg = DeliveryGapRecoveryResult(
        False, "STOP", "gate reorg exhausted", 6, "gate_failed", True
    )
    with (
        patch(
            "regent.application.execution_orchestrator.DeliveryGapRecoveryService"
        ) as svc_cls,
        patch(
            "regent.application.execution_orchestrator.TransitionService"
        ) as transition_cls,
    ):
        svc_cls.return_value.prepare_gate_reorganization = AsyncMock(return_value=reorg)
        transition_cls.return_value.transition_goal = AsyncMock()
        await orchestrator.handle_timer_fired(
            {
                "command": "goal.exhaust_insufficient",
                "goal_id": str(goal_id),
                "app_project_id": str(project_id),
                "actor": "test",
            }
        )

    transition_cls.return_value.transition_goal.assert_awaited_once()
    cmd = transition_cls.return_value.transition_goal.await_args.args[1]
    assert cmd == GoalCommand.WAIT_FOR_HUMAN
    assert cmd != GoalCommand.EXHAUST
    assert goal.metadata_json.get("execution_stage") == "WAITING_HUMAN"
    assert goal.metadata_json.get("termination", {}).get("handoff") == "WAITING_HUMAN"


def test_sidebar_exhausted_is_not_complete_label() -> None:
    """Sidebar must not present EXHAUSTED as completed work."""
    source = Path("apps/regent-console/src/components/Sidebar.tsx").read_text(
        encoding="utf-8"
    )
    assert "EXHAUSTED: '需要介入'" in source
    assert "EXHAUSTED: '已完成'" not in source
    assert "继续尝试" in source
    assert "DEPLOY_NOT_SUCCEEDED: '部署未成功，正在重试'" in source
    assert "DEPLOY_NOT_SUCCEEDED_NEEDS_HUMAN: '部署失败，需要你介入'" in source


@pytest.mark.asyncio
async def test_quality_approval_resolves_waiting_goal_before_achieve(db_sessions) -> None:
    from regent.infrastructure.models import AppProjectModel, GoalModel

    project_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    async with db_sessions() as session, session.begin():
        session.add(
            AppProjectModel(
                id=project_id,
                name="review-project",
                product_intent="review delivered preview",
                status="ACTIVE",
                created_by="test",
            )
        )
        session.add(
            GoalModel(
                id=goal_id,
                app_project_id=project_id,
                original_input="build a reviewed product",
                status="WAITING_HUMAN",
                version=2,
                created_by="test",
                correlation_id=uuid.uuid4(),
                metadata_json={
                    "execution_stage": "DELIVERED_AWAITING_REVIEW",
                    "pending_quality_task_id": str(uuid.uuid4()),
                },
            )
        )

    await ExecutionOrchestrator(db_sessions).handle_quality_approval_completed(
        {
            "goal_id": str(goal_id),
            "actor": "owner",
            "approved": True,
            "feedback": "accepted",
        }
    )

    async with db_sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        assert goal is not None
        assert goal.status == "ACHIEVED"
        assert goal.metadata_json["quality_approved_by"] == "owner"
