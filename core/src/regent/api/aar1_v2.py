"""AAR-1 Foundation /v2 API surface."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel, Field

from regent.application.agent_task_service import AgentTaskService
from regent.application.mcp_governance_service import McpGovernanceService
from regent.application.organization_engine import OrganizationEngine
from regent.application.policy_engine import (
    PolicyEngine,
    PolicyEvaluationRequest,
    default_system_rules,
    parse_rules,
)
from regent.domain.errors import DomainError, ErrorCode

router = APIRouter(prefix="/v2", tags=["aar1-foundation"])


def _idempotency(idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")]) -> str:
    if not idempotency_key or len(idempotency_key) < 8:
        raise DomainError(ErrorCode.INVALID_STATE, "Idempotency-Key required (min 8 chars)")
    return idempotency_key


IdemDep = Annotated[str, Depends(_idempotency)]


class ConstitutionVersionRequest(BaseModel):
    rules: list[dict[str, Any]]
    created_by: str = Field(min_length=1)
    approved_by: str | None = None
    version: int = Field(ge=1, default=1)


class PolicyEvalRequest(BaseModel):
    decision_point: str
    subject_type: str
    subject_id: str
    action: str
    resource: dict[str, Any] = Field(default_factory=dict)
    input_snapshot: dict[str, Any]
    rules: list[dict[str, Any]] | None = None
    role: str | None = None
    correlation_id: str = ""
    causation_id: str | None = None


class OrgDecisionRequest(BaseModel):
    organization_id: uuid.UUID
    trigger: str = "INITIAL"
    actor: str = "regent-core"
    available_capabilities: list[str] = Field(default_factory=list)
    activate: bool = True


class OrgRollbackRequest(BaseModel):
    target_version_id: uuid.UUID
    actor: str = "owner"


class AgentTaskCreateRequest(BaseModel):
    goal_id: uuid.UUID
    organization_version_id: uuid.UUID
    source_deployment_id: uuid.UUID
    target_deployment_id: uuid.UUID
    task_type: str
    payload_digest: str
    capability_scope: list[str] = Field(default_factory=list)
    correlation_id: str
    permit_refs: list[str] = Field(default_factory=list)
    payload_ref: str | None = None
    work_id: uuid.UUID | None = None
    causation_id: str | None = None


class ClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1)


class HeartbeatRequest(BaseModel):
    worker_id: str
    lease_token: str


class CompleteRequest(BaseModel):
    lease_token: str
    result_ref: str


class FailRequest(BaseModel):
    lease_token: str
    error_code: str
    retryable: bool = True


class ReconcileRequest(BaseModel):
    resolved_status: str
    result_ref: str | None = None
    error_code: str | None = None
    actor: str = "reconciler"


class McpCertifyRequest(BaseModel):
    actor: str = "platform-admin"


class McpInvokeRequest(BaseModel):
    goal_id: uuid.UUID
    input_data: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str
    caller_deployment_id: uuid.UUID | None = None
    permit_id: uuid.UUID | None = None
    fencing_token: uuid.UUID | None = None
    causation_id: str | None = None


def policy_engine(request: Request) -> PolicyEngine:
    return PolicyEngine(request.app.state.sessions)


def org_engine(request: Request) -> OrganizationEngine:
    return OrganizationEngine(request.app.state.sessions, enforce_cvr=True)


def task_service(request: Request) -> AgentTaskService:
    return AgentTaskService(request.app.state.sessions)


def mcp_service(request: Request) -> McpGovernanceService:
    return McpGovernanceService(request.app.state.sessions, enforce=True)


@router.post("/policy-evaluations", status_code=status.HTTP_201_CREATED)
async def evaluate_policy(
    payload: PolicyEvalRequest,
    engine: Annotated[PolicyEngine, Depends(policy_engine)],
    _idem: IdemDep,
) -> dict[str, Any]:
    rules = parse_rules(payload.rules) if payload.rules else default_system_rules()
    result = await engine.evaluate_and_persist(
        PolicyEvaluationRequest(
            decision_point=payload.decision_point,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            action=payload.action,
            resource=payload.resource,
            input_snapshot=payload.input_snapshot,
            rules=rules,
            role=payload.role,
            correlation_id=payload.correlation_id or _idem,
            causation_id=payload.causation_id,
        )
    )
    return {
        "id": result.id,
        "outcome": result.outcome.value,
        "matched_rule_ids": result.matched_rule_ids,
        "obligations": result.obligations,
        "reason_codes": result.reason_codes,
        "input_hash": result.input_hash,
        "evaluator_version": result.evaluator_version,
    }


@router.post("/organizations/{goal_id}/decisions", status_code=status.HTTP_201_CREATED)
async def create_org_decision(
    goal_id: uuid.UUID,
    payload: OrgDecisionRequest,
    engine: Annotated[OrganizationEngine, Depends(org_engine)],
    request: Request,
    _idem: IdemDep,
) -> dict[str, Any]:
    async with request.app.state.sessions() as session, session.begin():
        bundle = await engine.decide_and_persist(
            session,
            goal_id=goal_id,
            organization_id=payload.organization_id,
            trigger=payload.trigger,
            actor=payload.actor,
            available_capabilities=set(payload.available_capabilities),
            activate=payload.activate,
        )
    return {
        "decision_id": bundle.decision_id,
        "status": bundle.status,
        "selected_candidate_id": bundle.selected_candidate_id,
        "predicted_utility": bundle.predicted_utility,
        "feasible_count": bundle.feasible_count,
        "decision_json": bundle.decision_json,
        "infeasibility_report": bundle.infeasibility_report,
    }


@router.post("/organizations/{organization_id}/rollback", status_code=status.HTTP_201_CREATED)
async def rollback_organization(
    organization_id: uuid.UUID,
    payload: OrgRollbackRequest,
    engine: Annotated[OrganizationEngine, Depends(org_engine)],
    request: Request,
    _idem: IdemDep,
) -> dict[str, Any]:
    async with request.app.state.sessions() as session, session.begin():
        version = await engine.rollback(
            session,
            organization_id=organization_id,
            target_version_id=payload.target_version_id,
            actor=payload.actor,
        )
    return {
        "organization_version_id": version.id,
        "version": version.version,
        "status": version.status,
        "decision_id": version.decision_id,
    }


@router.post("/agent-tasks", status_code=status.HTTP_201_CREATED)
async def create_agent_task(
    payload: AgentTaskCreateRequest,
    service: Annotated[AgentTaskService, Depends(task_service)],
    _idem: IdemDep,
) -> dict[str, Any]:
    view = await service.offer_task(
        goal_id=payload.goal_id,
        organization_version_id=payload.organization_version_id,
        source_deployment_id=payload.source_deployment_id,
        target_deployment_id=payload.target_deployment_id,
        task_type=payload.task_type,
        idempotency_key=_idem,
        payload_digest=payload.payload_digest,
        capability_scope=payload.capability_scope,
        correlation_id=payload.correlation_id,
        work_id=payload.work_id,
        permit_refs=payload.permit_refs,
        payload_ref=payload.payload_ref,
        causation_id=payload.causation_id,
    )
    return {
        "id": view.id,
        "status": view.status,
        "attempt": view.attempt,
        "replayed": view.replayed,
    }


@router.post("/agent-tasks/{task_id}/claim")
async def claim_agent_task(
    task_id: uuid.UUID,
    payload: ClaimRequest,
    service: Annotated[AgentTaskService, Depends(task_service)],
) -> dict[str, Any]:
    view = await service.claim_task(task_id, worker_id=payload.worker_id)
    return {
        "id": view.id,
        "status": view.status,
        "attempt": view.attempt,
        "lease_token": view.lease_token,
    }


@router.post("/agent-tasks/{task_id}/heartbeat")
async def heartbeat_agent_task(
    task_id: uuid.UUID,
    payload: HeartbeatRequest,
    service: Annotated[AgentTaskService, Depends(task_service)],
) -> dict[str, Any]:
    view = await service.heartbeat(
        task_id, lease_token=payload.lease_token, worker_id=payload.worker_id
    )
    return {"id": view.id, "status": view.status, "lease_token": view.lease_token}


@router.post("/agent-tasks/{task_id}/complete")
async def complete_agent_task(
    task_id: uuid.UUID,
    payload: CompleteRequest,
    service: Annotated[AgentTaskService, Depends(task_service)],
) -> dict[str, Any]:
    view = await service.complete_task(
        task_id, lease_token=payload.lease_token, result_ref=payload.result_ref
    )
    return {
        "id": view.id,
        "status": view.status,
        "result_ref": view.result_ref,
        "replayed": view.replayed,
    }


@router.post("/agent-tasks/{task_id}/fail")
async def fail_agent_task(
    task_id: uuid.UUID,
    payload: FailRequest,
    service: Annotated[AgentTaskService, Depends(task_service)],
) -> dict[str, Any]:
    view = await service.fail_task(
        task_id,
        lease_token=payload.lease_token,
        error_code=payload.error_code,
        retryable=payload.retryable,
    )
    return {"id": view.id, "status": view.status, "error_code": view.error_code}


@router.post("/agent-tasks/{task_id}/reconcile")
async def reconcile_agent_task(
    task_id: uuid.UUID,
    payload: ReconcileRequest,
    service: Annotated[AgentTaskService, Depends(task_service)],
) -> dict[str, Any]:
    view = await service.reconcile_task(
        task_id,
        resolved_status=payload.resolved_status,
        result_ref=payload.result_ref,
        error_code=payload.error_code,
        actor=payload.actor,
    )
    return {"id": view.id, "status": view.status, "result_ref": view.result_ref}


@router.get("/agent-tasks/{task_id}")
async def get_agent_task(
    task_id: uuid.UUID,
    service: Annotated[AgentTaskService, Depends(task_service)],
) -> dict[str, Any]:
    view = await service.get(task_id)
    return {
        "id": view.id,
        "status": view.status,
        "attempt": view.attempt,
        "result_ref": view.result_ref,
        "error_code": view.error_code,
    }


@router.post("/mcp/servers/{server_id}/certify")
async def certify_mcp_server(
    server_id: uuid.UUID,
    payload: McpCertifyRequest,
    service: Annotated[McpGovernanceService, Depends(mcp_service)],
    _idem: IdemDep,
) -> dict[str, str]:
    await service.certify_server(server_id, actor=payload.actor)
    return {"status": "CERTIFIED"}


@router.post("/mcp/tools/{tool_id}/invoke", status_code=status.HTTP_201_CREATED)
async def invoke_mcp_tool(
    tool_id: uuid.UUID,
    payload: McpInvokeRequest,
    service: Annotated[McpGovernanceService, Depends(mcp_service)],
    _idem: IdemDep,
) -> dict[str, Any]:
    result = await service.invoke(
        tool_binding_id=tool_id,
        goal_id=payload.goal_id,
        idempotency_key=_idem,
        input_data=payload.input_data,
        correlation_id=payload.correlation_id,
        caller_deployment_id=payload.caller_deployment_id,
        permit_id=payload.permit_id,
        fencing_token=payload.fencing_token,
        causation_id=payload.causation_id,
    )
    return {
        "invocation_id": result.invocation_id,
        "status": result.status,
        "output": result.output,
        "output_trust": result.output_trust,
        "policy_evaluation_id": result.policy_evaluation_id,
        "external_operation_id": result.external_operation_id,
        "replayed": result.replayed,
    }
