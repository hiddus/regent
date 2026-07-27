"""AAR-1 Governed MCP — Policy + Scope + Permit + ExternalOperation for side effects."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.external_operation_service import ExternalOperationService, request_digest
from regent.application.policy_engine import (
    PolicyEngine,
    PolicyEvaluationRequest,
    PolicyOutcome,
    default_system_rules,
)
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.aar1_models import (
    McpInvocationModel,
    McpServerModel,
    McpToolBindingModel,
)


@dataclass(frozen=True, slots=True)
class McpInvokeResult:
    invocation_id: uuid.UUID
    status: str
    output: dict[str, Any]
    output_trust: str
    policy_evaluation_id: uuid.UUID | None
    external_operation_id: uuid.UUID | None
    replayed: bool = False


def _schema_hash(schema: dict[str, Any]) -> str:
    raw = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class McpGovernanceService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        policy: PolicyEngine | None = None,
        external_ops: ExternalOperationService | None = None,
        enforce: bool = True,
    ) -> None:
        self._sessions = sessions
        self._policy = policy or PolicyEngine(sessions)
        self._external_ops = external_ops or ExternalOperationService(sessions)
        self._enforce = enforce

    async def register_server(
        self,
        *,
        name: str,
        version: str,
        endpoint_ref: str,
        schema: dict[str, Any],
        secret_ref: str | None = None,
    ) -> uuid.UUID:
        async with self._sessions() as s, s.begin():
            existing = await s.scalar(
                select(McpServerModel).where(
                    McpServerModel.name == name, McpServerModel.version == version
                )
            )
            if existing is not None:
                return existing.id
            server_id = uuid.uuid4()
            s.add(
                McpServerModel(
                    id=server_id,
                    name=name,
                    version=version,
                    status="DISCOVERED",
                    endpoint_ref=endpoint_ref,
                    secret_ref=secret_ref,
                    schema_hash=_schema_hash(schema),
                )
            )
            return server_id

    async def certify_server(self, server_id: uuid.UUID, *, actor: str = "platform-admin") -> None:
        async with self._sessions() as s, s.begin():
            server = await s.get(McpServerModel, server_id, with_for_update=True)
            if server is None:
                raise DomainError(ErrorCode.NOT_FOUND, "mcp server not found")
            if server.status == "REVOKED":
                raise DomainError(ErrorCode.INVALID_STATE, "revoked server cannot be certified")
            result = self._policy.evaluate(
                PolicyEvaluationRequest(
                    decision_point="MCP_TOOL_DISCOVERY",
                    subject_type="MCP",
                    subject_id=str(server_id),
                    action="discover",
                    resource={"server": server.name},
                    input_snapshot={"schema_hash": server.schema_hash, "actor": actor},
                    rules=default_system_rules(),
                    correlation_id=str(server_id),
                )
            )
            if self._enforce and result.outcome is PolicyOutcome.DENY:
                raise DomainError(ErrorCode.POLICY_DENIED, "mcp certify denied")
            server.status = "CERTIFIED"
            server.certified_at = datetime.now(UTC)

    async def bind_tool(
        self,
        *,
        server_id: uuid.UUID,
        tool_name: str,
        input_schema: dict[str, Any],
        side_effect_class: str,
        certify: bool = True,
    ) -> uuid.UUID:
        if side_effect_class not in {"NONE", "REVERSIBLE", "IRREVERSIBLE"}:
            raise DomainError(ErrorCode.INVALID_STATE, "invalid side_effect_class")
        async with self._sessions() as s, s.begin():
            server = await s.get(McpServerModel, server_id)
            if server is None:
                raise DomainError(ErrorCode.NOT_FOUND, "mcp server not found")
            existing = await s.scalar(
                select(McpToolBindingModel).where(
                    McpToolBindingModel.server_id == server_id,
                    McpToolBindingModel.tool_name == tool_name,
                )
            )
            if existing is not None:
                return existing.id
            binding_id = uuid.uuid4()
            s.add(
                McpToolBindingModel(
                    id=binding_id,
                    server_id=server_id,
                    tool_name=tool_name,
                    input_schema_json=dict(input_schema),
                    schema_hash=_schema_hash(input_schema),
                    side_effect_class=side_effect_class,
                    status="CERTIFIED" if certify else "CANDIDATE",
                    allowlist_scopes_json=["goal"],
                )
            )
            return binding_id

    async def invoke(
        self,
        *,
        tool_binding_id: uuid.UUID,
        goal_id: uuid.UUID,
        idempotency_key: str,
        input_data: dict[str, Any],
        correlation_id: str,
        caller_deployment_id: uuid.UUID | None = None,
        permit_id: uuid.UUID | None = None,
        fencing_token: uuid.UUID | None = None,
        causation_id: str | None = None,
        readonly_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> McpInvokeResult:
        tool_name = ""
        side_effect_class = "NONE"
        invocation_id = uuid.uuid4()
        policy_evaluation_id: uuid.UUID | None = None
        digest = request_digest(input_data)

        async with self._sessions() as s, s.begin():
            existing = await s.scalar(
                select(McpInvocationModel).where(
                    McpInvocationModel.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                return McpInvokeResult(
                    invocation_id=existing.id,
                    status=existing.status,
                    output=dict(existing.output_json),
                    output_trust=existing.output_trust,
                    policy_evaluation_id=existing.policy_evaluation_id,
                    external_operation_id=existing.external_operation_id,
                    replayed=True,
                )

            binding = await s.get(McpToolBindingModel, tool_binding_id)
            if binding is None or binding.status != "CERTIFIED":
                raise DomainError(ErrorCode.MCP_SERVER_NOT_CERTIFIED, "tool not certified")
            server = await s.get(McpServerModel, binding.server_id)
            if server is None or server.status != "CERTIFIED":
                raise DomainError(ErrorCode.MCP_SERVER_NOT_CERTIFIED, "server not certified")

            current_hash = _schema_hash(binding.input_schema_json)
            if current_hash != binding.schema_hash:
                raise DomainError(ErrorCode.INVALID_STATE, "mcp schema drift detected")

            required = list((binding.input_schema_json or {}).get("required") or [])
            missing = [f for f in required if f not in input_data]
            if missing:
                raise DomainError(ErrorCode.INVALID_STATE, f"missing fields: {missing}")

            tool_name = binding.tool_name
            side_effect_class = binding.side_effect_class

            policy_result = await self._policy.evaluate_and_persist(
                PolicyEvaluationRequest(
                    decision_point="MCP_TOOL_INVOKE",
                    subject_type="MCP",
                    subject_id=str(tool_binding_id),
                    action="invoke",
                    resource={"side_effect_class": side_effect_class},
                    input_snapshot={
                        "tool": tool_name,
                        "goal_id": str(goal_id),
                        "input_keys": sorted(input_data.keys()),
                    },
                    rules=default_system_rules(),
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                ),
                session=s,
                fail_closed=self._enforce,
            )
            policy_evaluation_id = policy_result.id

            if policy_result.outcome is PolicyOutcome.DENY:
                s.add(
                    McpInvocationModel(
                        id=invocation_id,
                        tool_binding_id=tool_binding_id,
                        goal_id=goal_id,
                        caller_deployment_id=caller_deployment_id,
                        policy_evaluation_id=policy_result.id,
                        idempotency_key=idempotency_key,
                        request_digest=digest,
                        status="DENIED",
                        output_json={"denied": True},
                        correlation_id=correlation_id,
                        causation_id=causation_id,
                        error_code="POLICY_DENIED",
                    )
                )
                if self._enforce:
                    raise DomainError(ErrorCode.POLICY_DENIED, "mcp invoke denied")
                return McpInvokeResult(
                    invocation_id=invocation_id,
                    status="DENIED",
                    output={"denied": True},
                    output_trust="UNTRUSTED_DATA",
                    policy_evaluation_id=policy_result.id,
                    external_operation_id=None,
                )

            if side_effect_class == "NONE":
                output: dict[str, Any] = {
                    "tool": tool_name,
                    "echo": input_data,
                    "readonly": True,
                }
                if readonly_handler is not None:
                    output = dict(readonly_handler(input_data))
                s.add(
                    McpInvocationModel(
                        id=invocation_id,
                        tool_binding_id=tool_binding_id,
                        goal_id=goal_id,
                        caller_deployment_id=caller_deployment_id,
                        policy_evaluation_id=policy_result.id,
                        idempotency_key=idempotency_key,
                        request_digest=digest,
                        status="SUCCEEDED",
                        output_json=output,
                        output_trust="UNTRUSTED_DATA",
                        correlation_id=correlation_id,
                        causation_id=causation_id,
                    )
                )
                return McpInvokeResult(
                    invocation_id=invocation_id,
                    status="SUCCEEDED",
                    output=output,
                    output_trust="UNTRUSTED_DATA",
                    policy_evaluation_id=policy_result.id,
                    external_operation_id=None,
                )

            if policy_result.outcome is PolicyOutcome.REQUIRE_PERMIT and permit_id is None:
                raise DomainError(ErrorCode.PERMIT_REQUIRED, "side-effect MCP requires permit")
            if permit_id is None or fencing_token is None:
                raise DomainError(
                    ErrorCode.PERMIT_REQUIRED,
                    "side-effect MCP requires permit_id and fencing_token",
                )

            s.add(
                McpInvocationModel(
                    id=invocation_id,
                    tool_binding_id=tool_binding_id,
                    goal_id=goal_id,
                    caller_deployment_id=caller_deployment_id,
                    policy_evaluation_id=policy_result.id,
                    permit_id=permit_id,
                    idempotency_key=idempotency_key,
                    request_digest=digest,
                    status="PREPARED",
                    output_json={},
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
            )

        prepared = await self._external_ops.prepare(
            operation_key=f"mcp:{idempotency_key}",
            provider="mcp-governed-v1",
            action=tool_name or "invoke",
            permit_id=permit_id,  # type: ignore[arg-type]
            local_fencing_token=fencing_token,  # type: ignore[arg-type]
            payload=input_data,
            goal_id=goal_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

        async with self._sessions() as s, s.begin():
            inv = await s.get(McpInvocationModel, invocation_id, with_for_update=True)
            if inv is None:
                raise DomainError(ErrorCode.NOT_FOUND, "invocation disappeared")
            inv.external_operation_id = prepared.id
            inv.status = "SUCCEEDED"
            inv.output_json = {
                "external_operation_id": str(prepared.id),
                "tool": tool_name,
                "side_effect": True,
            }
            return McpInvokeResult(
                invocation_id=invocation_id,
                status=inv.status,
                output=dict(inv.output_json),
                output_trust="UNTRUSTED_DATA",
                policy_evaluation_id=policy_evaluation_id,
                external_operation_id=prepared.id,
            )
