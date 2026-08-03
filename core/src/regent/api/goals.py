import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.goal_execution_service import GoalExecutionReceipt, GoalExecutionService
from regent.application.goal_interpreter import GoalInterpreter
from regent.application.goal_service import CreateGoal, GoalService
from regent.application.organization_service import OrganizationReceipt, OrganizationService
from regent.application.planning_service import PlanningService, PlanReceipt
from regent.application.transition_service import TransitionContext, TransitionService
from regent.config import get_settings
from regent.domain.transitions import GoalCommand
from regent.model.factory import build_model_provider

router = APIRouter(prefix="/v1/goals", tags=["goals"])


class CreateGoalRequest(BaseModel):
    original_input: str = Field(min_length=1, max_length=20_000)
    created_by: str = Field(min_length=1, max_length=255)
    explicit_constraints: dict[str, Any] = Field(default_factory=dict)
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InterpretGoalRequest(BaseModel):
    original_input: str = Field(min_length=1, max_length=20_000)
    created_by: str = Field(min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TransitionGoalRequest(BaseModel):
    command: GoalCommand
    expected_version: int = Field(ge=0)
    actor: str = Field(min_length=1, max_length=255)
    causation_id: uuid.UUID | None = None

class StartGoalRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=1, max_length=255)



class GoalResponse(BaseModel):
    id: uuid.UUID
    original_input: str
    status: str
    version: int
    created_by: str
    correlation_id: uuid.UUID
    metadata: dict[str, Any]
    spec_version: int
    spec_status: str
    spec_hash: str


def goal_service(request: Request) -> GoalService:
    return GoalService(request.app.state.sessions)


def transition_service(request: Request) -> TransitionService:
    return TransitionService(request.app.state.sessions)


GoalServiceDep = Annotated[GoalService, Depends(goal_service)]
TransitionServiceDep = Annotated[TransitionService, Depends(transition_service)]


def _response(goal: Any) -> GoalResponse:
    specs = sorted(goal.specs, key=lambda spec: spec.version)
    return GoalResponse(
        id=goal.id,
        original_input=goal.original_input,
        status=goal.status,
        version=goal.version,
        created_by=goal.created_by,
        correlation_id=goal.correlation_id,
        metadata=goal.metadata_json,
        spec_version=specs[-1].version,
        spec_status=specs[-1].status,
        spec_hash=specs[-1].content_hash,
    )


@router.post("", response_model=GoalResponse, status_code=status.HTTP_201_CREATED)
async def create_goal(payload: CreateGoalRequest, service: GoalServiceDep) -> GoalResponse:
    goal = await service.create(CreateGoal(**payload.model_dump()))
    return _response(await service.get(goal.id))


@router.post("/interpret", response_model=GoalResponse, status_code=status.HTTP_201_CREATED)
async def interpret_goal(payload: InterpretGoalRequest, service: GoalServiceDep) -> GoalResponse:
    interpreted = await GoalInterpreter(build_model_provider(get_settings())).interpret(
        payload.original_input
    )
    result = interpreted.output
    goal = await service.create(
        CreateGoal(
            original_input=payload.original_input,
            created_by=payload.created_by,
            explicit_constraints=result.explicit_constraints,
            success_criteria=result.success_criteria,
            metadata={
                **payload.metadata,
                "interpretation_model": interpreted.model,
                "interpretation_usage": {
                    "input_tokens": interpreted.usage.input_tokens,
                    "output_tokens": interpreted.usage.output_tokens,
                },
                "interpreted_objective": result.objective or payload.original_input,
            },
            system_inferences=result.system_inferences,
            unknowns=[item.model_dump() for item in result.unknowns],
        )
    )
    return _response(await service.get(goal.id))


@router.get("/{goal_id}", response_model=GoalResponse)
async def get_goal(goal_id: uuid.UUID, service: GoalServiceDep) -> GoalResponse:
    return _response(await service.get(goal_id))


@router.post(
    "/{goal_id}/start",
    response_model=GoalExecutionReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_goal(
    goal_id: uuid.UUID, payload: StartGoalRequest, request: Request
) -> GoalExecutionReceipt:
    return await GoalExecutionService(request.app.state.sessions).start(
        goal_id, actor=payload.actor, idempotency_key=payload.idempotency_key
    )


@router.post("/{goal_id}/organize", response_model=OrganizationReceipt)
async def organize_goal(goal_id: uuid.UUID, request: Request) -> OrganizationReceipt:
    return await OrganizationService(request.app.state.sessions).organize(goal_id)


@router.get("/{goal_id}/organization", response_model=OrganizationReceipt)
async def get_goal_organization(goal_id: uuid.UUID, request: Request) -> OrganizationReceipt:
    return await OrganizationService(request.app.state.sessions).get_organization(goal_id)


@router.post("/{goal_id}/plan", response_model=PlanReceipt)
async def plan_goal(goal_id: uuid.UUID, request: Request) -> PlanReceipt:
    return await PlanningService(
        request.app.state.sessions,
        build_model_provider(get_settings()),
    ).plan(goal_id)


@router.post("/{goal_id}/transitions", response_model=GoalResponse)
async def transition_goal(
    goal_id: uuid.UUID,
    payload: TransitionGoalRequest,
    transitions: TransitionServiceDep,
    goals: GoalServiceDep,
) -> GoalResponse:
    goal = await goals.get(goal_id)
    await transitions.transition_goal(
        TransitionContext(
            aggregate_id=goal_id,
            expected_version=payload.expected_version,
            actor=payload.actor,
            correlation_id=goal.correlation_id,
            causation_id=payload.causation_id,
        ),
        payload.command,
    )
    return _response(await goals.get(goal_id))


class PrivacyActorBody(BaseModel):
    actor: str = Field(min_length=1, max_length=255)


class PrivacyConsentBody(BaseModel):
    actor: str = Field(min_length=1, max_length=255)
    scopes: list[str] | None = None


@router.get("/{goal_id}/privacy/notice")
async def privacy_notice(goal_id: uuid.UUID) -> dict[str, Any]:
    """PRD §7.1 — purpose notice shown before Observation/Evidence/conversation collection."""
    from regent.application.privacy_service import privacy_notice as notice

    payload = notice()
    payload["goal_id"] = str(goal_id)
    return payload


@router.get("/{goal_id}/privacy/consent")
async def get_privacy_consent(
    goal_id: uuid.UUID, actor: str, request: Request
) -> dict[str, Any]:
    from regent.application.privacy_service import PrivacyService

    record = await PrivacyService(request.app.state.sessions).get_consent(
        goal_id, subject=actor
    )
    if record is None:
        return {"goal_id": str(goal_id), "subject": actor, "status": "NONE"}
    return record.as_dict()


@router.post("/{goal_id}/privacy/consent")
async def grant_privacy_consent(
    goal_id: uuid.UUID, payload: PrivacyConsentBody, request: Request
) -> dict[str, Any]:
    """PRD §7.1 — grant (or re-grant) consent after notice."""
    from regent.application.privacy_service import PrivacyService

    record = await PrivacyService(request.app.state.sessions).grant_consent(
        goal_id, subject=payload.actor, scopes=payload.scopes
    )
    return record.as_dict()


@router.post("/{goal_id}/privacy/consent/withdraw")
async def withdraw_privacy_consent(
    goal_id: uuid.UUID, payload: PrivacyActorBody, request: Request
) -> dict[str, Any]:
    """PRD §7.1 — withdraw consent; further collection is denied while withdrawn."""
    from regent.application.privacy_service import PrivacyService

    record = await PrivacyService(request.app.state.sessions).withdraw_consent(
        goal_id, subject=payload.actor
    )
    return record.as_dict()


@router.get("/{goal_id}/export")
async def export_goal(
    goal_id: uuid.UUID, actor: str, request: Request
) -> dict[str, Any]:
    """PRD §7.4 Goal Owner export (PII-minimized)."""
    from regent.application.privacy_service import PrivacyService

    package = await PrivacyService(request.app.state.sessions).export_goal(
        goal_id, requester=actor
    )
    return package.as_dict()


@router.post("/{goal_id}/export")
async def export_goal_post(
    goal_id: uuid.UUID, payload: PrivacyActorBody, request: Request
) -> dict[str, Any]:
    from regent.application.privacy_service import PrivacyService

    package = await PrivacyService(request.app.state.sessions).export_goal(
        goal_id, requester=payload.actor
    )
    return package.as_dict()


@router.post("/{goal_id}/delete-request")
async def delete_goal_request(
    goal_id: uuid.UUID, payload: PrivacyActorBody, request: Request
) -> dict[str, Any]:
    """PRD §7.4 Goal Owner delete request; receipt is written to Audit."""
    from regent.application.privacy_service import PrivacyService

    receipt = await PrivacyService(request.app.state.sessions).request_delete(
        goal_id, requester=payload.actor
    )
    return receipt.as_dict()


@router.get("/{goal_id}/plan-items")
async def list_plan_items(goal_id: uuid.UUID, request: Request) -> list[dict[str, Any]]:
    """Durable execution plan items for console task checklist."""
    from regent.application.execution_plan import ExecutionPlanService

    items = await ExecutionPlanService(request.app.state.sessions).list_items(goal_id)
    out: list[dict[str, Any]] = []
    for item in items:
        payload = item.as_dict()
        out.append(
            {
                "id": payload["id"],
                "item_key": payload["item_key"],
                "content": payload["content"],
                "status": payload["status"],
                "owner_agent_id": payload.get("owner_agent_id"),
                "dependencies": payload.get("dependencies") or [],
                "updated_at": None,
            }
        )
    return out


@router.get("/{goal_id}/plan-timeline")
async def get_plan_timeline(goal_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """H2: read-only work-plan timeline / swimlanes (ExecutionPlan projection)."""
    from regent.application.execution_plan import ExecutionPlanService
    from regent.application.hive_policy import coding_default_is_primary, get_goal_kind
    from regent.infrastructure.models import GoalModel

    items = await ExecutionPlanService(request.app.state.sessions).list_items(goal_id)
    lanes: dict[str, list[dict[str, Any]]] = {}
    nodes: list[dict[str, Any]] = []
    for item in items:
        payload = item.as_dict()
        owner = str(payload.get("owner_agent_id") or "primary")
        node = {
            "id": payload["id"],
            "item_key": payload["item_key"],
            "content": payload["content"],
            "status": payload["status"],
            "owner_agent_id": payload.get("owner_agent_id"),
            "dependencies": payload.get("dependencies") or [],
            "lane": owner,
        }
        nodes.append(node)
        lanes.setdefault(owner, []).append(node)
    edges: list[dict[str, str]] = []
    keys = {n["item_key"] for n in nodes}
    for n in nodes:
        for dep in n["dependencies"]:
            dep_s = str(dep)
            if dep_s in keys:
                edges.append({"from": dep_s, "to": n["item_key"]})
    goal_kind = "coding"
    hive_primary = True
    async with request.app.state.sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        if goal is not None:
            meta = goal.metadata_json if isinstance(goal.metadata_json, dict) else {}
            goal_kind = get_goal_kind(meta)
            hive_primary = coding_default_is_primary(meta)
    return {
        "goal_id": str(goal_id),
        "nodes": nodes,
        "edges": edges,
        "lanes": [
            {"owner": owner, "items": lane_items}
            for owner, lane_items in lanes.items()
        ],
        "goal_kind": goal_kind,
        "coding_primary_default": hive_primary,
    }


@router.get("/{goal_id}/activity")
async def get_goal_activity(goal_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """TRANSITIONAL: read metadata ring buffers (activity_log / tool_events).

    Not the durable event truth source. Do not use for audit / gates / billing.
    See docs/decision-note-delivery-machine-invariants-2026-08-02.md §3.
    """
    from regent.infrastructure.models import GoalModel

    async with request.app.state.sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        if goal is None:
            return {
                "events": [],
                "tool_events": [],
                "live_action": None,
                "regent_events": [],
                "agent_loop_exit": None,
                "execution_mode": "ask",
                "pending_agent_loop_ask": None,
            }
        meta = goal.metadata_json if isinstance(goal.metadata_json, dict) else {}
        activity = meta.get("activity_log")
        tools = meta.get("tool_events")
        return {
            "events": list(activity) if isinstance(activity, list) else [],
            "tool_events": list(tools) if isinstance(tools, list) else [],
            "live_action": meta.get("live_action"),
            "regent_events": list(meta.get("regent_events") or [])
            if isinstance(meta.get("regent_events"), list)
            else [],
            "agent_loop_exit": meta.get("agent_loop_exit"),
            "execution_mode": meta.get("execution_mode") or "ask",
            "pending_agent_loop_ask": meta.get("pending_agent_loop_ask"),
        }


class AbortGoalRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=255)
    reason: str = Field(default="user_abort", max_length=200)


class ExecutionModeRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=255)
    mode: str = Field(pattern="^(ask|act)$")


@router.post("/{goal_id}/abort")
async def abort_goal_run(goal_id: uuid.UUID, payload: AbortGoalRequest, request: Request) -> dict[str, Any]:
    """H0.1: request mid-run abort → STOP with draft on next agent turn check."""
    from sqlalchemy.orm.attributes import flag_modified

    from regent.agent.events import RegentEvent, append_regent_event
    from regent.application.agent_control import apply_abort_to_goal_metadata
    from regent.application.agent_loop_exit import apply_exit_to_metadata, build_exit
    from regent.application.live_action import merge_live_action_into_metadata
    from regent.infrastructure.models import GoalModel

    async with request.app.state.sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id, with_for_update=True)
        if goal is None:
            return {"ok": False, "error": "goal not found"}
        meta = apply_abort_to_goal_metadata(
            dict(goal.metadata_json or {}),
            str(goal_id),
            actor=payload.actor,
            reason=payload.reason or "user_abort",
        )
        # Optimistic STOP stamp (runner also raises UserAbortError → recovery STOP).
        exit_payload = build_exit(
            exit_kind="STOP",
            stop_reason="user_abort",
            lease_id=meta.get("last_generation_run_id"),
            session_id=meta.get("project_agent_session_id"),
            epoch=meta.get("project_agent_session_epoch"),
            draft_uri=meta.get("last_good_draft_uri"),
        )
        meta = apply_exit_to_metadata(meta, exit_payload)
        meta = append_regent_event(
            meta,
            RegentEvent(
                type="abort_requested",
                summary="用户请求停止",
                goal_id=str(goal_id),
                payload={"actor": payload.actor, "reason": payload.reason},
            ),
        )
        meta = merge_live_action_into_metadata(
            meta,
            "已请求停止：本轮将结束并保留草稿",
            stage="DELIVERY_SOFT_PAUSE",
            event_type="AGENT_LOOP_STOP",
        )
        goal.metadata_json = meta
        flag_modified(goal, "metadata_json")
    return {"ok": True, "goal_id": str(goal_id), "abort": True}


@router.get("/{goal_id}/agent-loop-exit")
async def get_agent_loop_exit(goal_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """H0.4: COMPLETE/STOP/ASK result surface for console."""
    from regent.infrastructure.models import GoalModel

    async with request.app.state.sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        if goal is None:
            return {"exit": None, "execution_mode": "ask"}
        meta = goal.metadata_json if isinstance(goal.metadata_json, dict) else {}
        return {
            "exit": meta.get("agent_loop_exit"),
            "pending_ask": meta.get("pending_agent_loop_ask"),
            "execution_mode": meta.get("execution_mode") or "ask",
            "work_plan_approved": bool(meta.get("work_plan_approved")),
        }


@router.post("/{goal_id}/execution-mode")
async def set_goal_execution_mode(
    goal_id: uuid.UUID, payload: ExecutionModeRequest, request: Request
) -> dict[str, Any]:
    """H0/H1: Ask vs Act mode (default ask)."""
    from sqlalchemy.orm.attributes import flag_modified

    from regent.application.agent_control import set_execution_mode
    from regent.infrastructure.models import GoalModel

    async with request.app.state.sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id, with_for_update=True)
        if goal is None:
            return {"ok": False, "error": "goal not found"}
        mode = "act" if payload.mode == "act" else "ask"
        meta = set_execution_mode(dict(goal.metadata_json or {}), mode)  # type: ignore[arg-type]
        goal.metadata_json = meta
        flag_modified(goal, "metadata_json")
    return {"ok": True, "goal_id": str(goal_id), "execution_mode": mode}


class SideQuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    actor: str = Field(default="user", max_length=255)


class UndoTurnRequest(BaseModel):
    actor: str = Field(default="user", max_length=255)
    dry_run: bool = False


class WorkflowPresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    actor: str = Field(default="user", max_length=255)


@router.get("/{goal_id}/trust-posture")
async def get_trust_posture(goal_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """O1: read-only trust posture (sandbox × Ask/Act × quarantine)."""
    from regent.application.trust_posture import build_trust_posture
    from regent.infrastructure.models import GoalModel

    async with request.app.state.sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        if goal is None:
            return {"ok": False, "error": "goal not found"}
        meta = goal.metadata_json if isinstance(goal.metadata_json, dict) else {}
        posture = build_trust_posture(meta)
    return {"ok": True, "goal_id": str(goal_id), "posture": posture}


@router.post("/{goal_id}/side-question")
async def post_side_question(
    goal_id: uuid.UUID, payload: SideQuestionRequest, request: Request
) -> dict[str, Any]:
    """O2: structurally isolated side question (no tools / no Work Plan mutation)."""
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    from regent.agent.events import RegentEvent, append_regent_event
    from regent.application.side_question import run_side_question
    from regent.infrastructure.models import (
        ConversationMessageModel,
        ConversationModel,
        GoalModel,
    )

    async with request.app.state.sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id, with_for_update=True)
        if goal is None:
            return {"ok": False, "error": "goal not found"}
        conversation = await session.scalar(
            select(ConversationModel).where(ConversationModel.goal_id == goal_id)
        )
        messages: list[dict[str, Any]] = []
        if conversation is not None:
            rows = (
                await session.scalars(
                    select(ConversationMessageModel)
                    .where(ConversationMessageModel.conversation_id == conversation.id)
                    .order_by(ConversationMessageModel.ordinal.desc())
                    .limit(24)
                )
            ).all()
            messages = [
                {"role": m.role, "content": m.content}
                for m in reversed(list(rows))
            ]
        result = await run_side_question(
            question=payload.question,
            context_messages=messages,
        )
        meta = append_regent_event(
            dict(goal.metadata_json or {}),
            RegentEvent(
                type="side_question",
                summary="侧问（只读旁路）",
                goal_id=str(goal_id),
                payload={
                    "ok": result.get("ok"),
                    "mutated_work_plan": False,
                    "tools_invoked": False,
                },
            ),
        )
        meta["last_side_question"] = {
            "question": payload.question[:400],
            "answer": str(result.get("text") or "")[:4000],
            "context_summary": result.get("context_summary"),
            "actor": payload.actor,
        }
        goal.metadata_json = meta
        flag_modified(goal, "metadata_json")
    return {"ok": True, "goal_id": str(goal_id), **result}


@router.get("/{goal_id}/session-export")
async def get_session_export(goal_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """O2: redacted Markdown + manifest digest."""
    from sqlalchemy import select

    from regent.application.session_export import build_session_export
    from regent.infrastructure.models import (
        ConversationMessageModel,
        ConversationModel,
        GoalModel,
    )

    async with request.app.state.sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        if goal is None:
            return {"ok": False, "error": "goal not found"}
        meta = goal.metadata_json if isinstance(goal.metadata_json, dict) else {}
        conversation = await session.scalar(
            select(ConversationModel).where(ConversationModel.goal_id == goal_id)
        )
        messages: list[dict[str, Any]] = []
        if conversation is not None:
            rows = (
                await session.scalars(
                    select(ConversationMessageModel)
                    .where(ConversationMessageModel.conversation_id == conversation.id)
                    .order_by(ConversationMessageModel.ordinal.asc())
                    .limit(200)
                )
            ).all()
            messages = [
                {"role": m.role, "message_type": m.message_type, "content": m.content}
                for m in rows
            ]
        export = build_session_export(
            goal_id=str(goal_id),
            metadata=meta,
            conversation=messages,
            project_id=str(goal.app_project_id) if goal.app_project_id else None,
        )
    return {"ok": True, **export}


@router.post("/{goal_id}/undo-turn")
async def undo_goal_turn(
    goal_id: uuid.UUID, payload: UndoTurnRequest, request: Request
) -> dict[str, Any]:
    """O3: dry-run or apply Primary turn undo (fail closed on divergence)."""
    from pathlib import Path

    from sqlalchemy.orm.attributes import flag_modified

    from regent.agent.events import RegentEvent, append_regent_event
    from regent.application.turn_checkpoint import apply_plan, format_turn_plan, plan_undo
    from regent.infrastructure.models import GoalModel

    async with request.app.state.sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id, with_for_update=True)
        if goal is None:
            return {"ok": False, "error": "goal not found"}
        meta = dict(goal.metadata_json or {})
        workspace = meta.get("workspace_root") or meta.get("sandbox_root")
        if not workspace:
            return {"ok": False, "error": "workspace_root missing on goal metadata"}
        plan = plan_undo(meta, workspace_root=Path(str(workspace)))
        preview = format_turn_plan(plan)
        if payload.dry_run or not plan.get("ok"):
            return {"ok": bool(plan.get("ok")), "dry_run": True, "plan": plan, "preview": preview}
        meta, receipt = apply_plan(meta, plan, workspace_root=Path(str(workspace)))
        meta = append_regent_event(
            meta,
            RegentEvent(
                type="turn_undo",
                summary=f"撤回回合 #{receipt.get('turn_index')}",
                goal_id=str(goal_id),
                payload=receipt,
            ),
        )
        goal.metadata_json = meta
        flag_modified(goal, "metadata_json")
    return {"ok": True, "dry_run": False, "receipt": receipt, "preview": preview}


@router.post("/{goal_id}/redo-turn")
async def redo_goal_turn(
    goal_id: uuid.UUID, payload: UndoTurnRequest, request: Request
) -> dict[str, Any]:
    """O3: redo previously undone Primary turn."""
    from pathlib import Path

    from sqlalchemy.orm.attributes import flag_modified

    from regent.agent.events import RegentEvent, append_regent_event
    from regent.application.turn_checkpoint import apply_plan, format_turn_plan, plan_redo
    from regent.infrastructure.models import GoalModel

    async with request.app.state.sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id, with_for_update=True)
        if goal is None:
            return {"ok": False, "error": "goal not found"}
        meta = dict(goal.metadata_json or {})
        workspace = meta.get("workspace_root") or meta.get("sandbox_root")
        if not workspace:
            return {"ok": False, "error": "workspace_root missing on goal metadata"}
        plan = plan_redo(meta, workspace_root=Path(str(workspace)))
        preview = format_turn_plan(plan)
        if payload.dry_run or not plan.get("ok"):
            return {"ok": bool(plan.get("ok")), "dry_run": True, "plan": plan, "preview": preview}
        meta, receipt = apply_plan(meta, plan, workspace_root=Path(str(workspace)))
        meta = append_regent_event(
            meta,
            RegentEvent(
                type="turn_undo",
                summary=f"重做回合 #{receipt.get('turn_index')}",
                goal_id=str(goal_id),
                payload=receipt,
            ),
        )
        goal.metadata_json = meta
        flag_modified(goal, "metadata_json")
    return {"ok": True, "dry_run": False, "receipt": receipt, "preview": preview}


@router.get("/{goal_id}/evidence-bundle")
async def get_evidence_bundle(goal_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """O4: portable COMPLETE/STOP evidence with digest."""
    from regent.application.evidence_bundle import build_evidence_bundle, verify_evidence_bundle
    from regent.infrastructure.models import GoalModel

    async with request.app.state.sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        if goal is None:
            return {"ok": False, "error": "goal not found"}
        meta = goal.metadata_json if isinstance(goal.metadata_json, dict) else {}
        bundle = build_evidence_bundle(meta, goal_id=str(goal_id))
    return {"ok": True, "bundle": bundle, "verify": verify_evidence_bundle(bundle)}


@router.post("/{goal_id}/workflow-preset")
async def set_workflow_preset(
    goal_id: uuid.UUID, payload: WorkflowPresetRequest, request: Request
) -> dict[str, Any]:
    """O4: attach a named preset to Goal metadata (Work Plan hint)."""
    from sqlalchemy.orm.attributes import flag_modified

    from regent.application.workflow_presets import apply_workflow_preset
    from regent.infrastructure.models import GoalModel

    async with request.app.state.sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id, with_for_update=True)
        if goal is None:
            return {"ok": False, "error": "goal not found"}
        try:
            meta = apply_workflow_preset(dict(goal.metadata_json or {}), payload.name)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        goal.metadata_json = meta
        flag_modified(goal, "metadata_json")
    return {"ok": True, "goal_id": str(goal_id), "preset": meta.get("workflow_preset")}


@router.get("/{goal_id}/agents")
async def get_goal_agents(goal_id: uuid.UUID, request: Request) -> list[dict[str, Any]]:
    """TRANSITIONAL: in-process subagent roster (+ optional metadata snapshot).

    Not durable across workers. See decision-note-delivery-machine-invariants §3.
    """
    from regent.application.subagent_runtime import list_subagent_runtime
    from regent.infrastructure.models import GoalModel

    runtime = list_subagent_runtime(str(goal_id))
    async with request.app.state.sessions() as session:
        goal = await session.get(GoalModel, goal_id)
        meta = goal.metadata_json if goal and isinstance(goal.metadata_json, dict) else {}
    persisted = meta.get("subagent_runtime")
    if isinstance(persisted, list):
        by_id = {str(r.get("id")): r for r in persisted if isinstance(r, dict)}
        for row in runtime:
            by_id[str(row["id"])] = row
        return list(by_id.values())
    return runtime
