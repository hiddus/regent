"""AAR-1 Agent SpecVersion / Deployment lifecycle and relationships."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.policy_engine import (
    PolicyEngine,
    PolicyEvaluationRequest,
    PolicyOutcome,
    default_system_rules,
)
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.aar1_models import (
    AgentDeploymentModel,
    AgentLifecycleEventModel,
    AgentRelationshipModel,
    AgentSpecVersionModel,
)
from regent.infrastructure.models import AgentSpecModel

RELATIONSHIP_TYPES = frozenset(
    {
        "SUPERVISES",
        "DELEGATES_TO",
        "DEPENDS_ON",
        "REVIEWS",
        "APPROVES",
        "ESCALATES_TO",
        "SHARES_MEMORY_WITH",
    }
)

SPEC_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"CERTIFIED", "REVOKED"}),
    "CERTIFIED": frozenset({"SUPERSEDED", "REVOKED"}),
    "SUPERSEDED": frozenset(),
    "REVOKED": frozenset(),
}

DEPLOY_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"DEPLOYED", "FAILED"}),
    "DEPLOYED": frozenset({"OPERATING", "SUSPENDED", "FAILED", "RETIRED"}),
    "OPERATING": frozenset({"SUSPENDED", "UPGRADING", "RETIRED", "FAILED"}),
    "SUSPENDED": frozenset({"OPERATING", "RETIRED", "FAILED"}),
    "UPGRADING": frozenset({"OPERATING", "FAILED", "RETIRED"}),
    "RETIRED": frozenset(),  # terminal — must create new deployment
    "FAILED": frozenset({"RETIRED"}),
}


def manifest_hash(manifest: dict[str, Any]) -> str:
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def intersect_permissions(
    parent: dict[str, Any],
    org_granted: dict[str, Any],
    goal_allowed: dict[str, Any],
    task_required: dict[str, Any],
) -> dict[str, Any]:
    """FR-AAR-010: child = parent ∩ org ∩ goal ∩ task."""

    def _set(d: dict[str, Any], key: str) -> set[str]:
        return set(d.get(key) or [])

    allow = (
        _set(parent, "allow")
        & _set(org_granted, "allow")
        & _set(goal_allowed, "allow")
        & _set(task_required, "allow")
    )
    require_permit = (
        _set(parent, "require_permit")
        | _set(org_granted, "require_permit")
        | _set(goal_allowed, "require_permit")
    ) & (_set(task_required, "require_permit") | allow)
    deny = (
        _set(parent, "deny")
        | _set(org_granted, "deny")
        | _set(goal_allowed, "deny")
        | _set(task_required, "deny")
    )
    allow -= deny
    require_permit -= deny
    return {
        "allow": sorted(allow),
        "require_permit": sorted(require_permit),
        "deny": sorted(deny),
    }


class AgentLifecycleService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        policy: PolicyEngine | None = None,
        enforce: bool = True,
    ) -> None:
        self._sessions = sessions
        self._policy = policy or PolicyEngine(sessions)
        self._enforce = enforce

    async def create_spec_version(
        self,
        session: AsyncSession,
        *,
        agent_spec_id: uuid.UUID,
        manifest: dict[str, Any],
        actor: str,
        constitution_version_id: uuid.UUID | None = None,
        certify: bool = False,
    ) -> AgentSpecVersionModel:
        spec = await session.get(AgentSpecModel, agent_spec_id)
        if spec is None:
            raise DomainError(ErrorCode.NOT_FOUND, "agent spec not found")
        existing = list(
            await session.scalars(
                select(AgentSpecVersionModel).where(
                    AgentSpecVersionModel.agent_spec_id == agent_spec_id
                )
            )
        )
        version_no = max((v.version for v in existing), default=0) + 1
        status = "DRAFT"
        row = AgentSpecVersionModel(
            id=uuid.uuid4(),
            agent_spec_id=agent_spec_id,
            version=version_no,
            status=status,
            manifest_json=dict(manifest),
            manifest_hash=manifest_hash(manifest),
            constitution_version_id=constitution_version_id,
            created_by=actor,
        )
        session.add(row)
        await session.flush()
        if certify:
            await self.transition_spec_version(
                session, row.id, to_status="CERTIFIED", actor=actor, reason="certified"
            )
            await session.refresh(row)
        return row

    async def transition_spec_version(
        self,
        session: AsyncSession,
        spec_version_id: uuid.UUID,
        *,
        to_status: str,
        actor: str,
        reason: str,
    ) -> AgentSpecVersionModel:
        row = await session.get(AgentSpecVersionModel, spec_version_id, with_for_update=True)
        if row is None:
            raise DomainError(ErrorCode.NOT_FOUND, "spec version not found")
        allowed = SPEC_TRANSITIONS.get(row.status, frozenset())
        if to_status not in allowed:
            raise DomainError(
                ErrorCode.INVALID_AGENT_LIFECYCLE_TRANSITION,
                f"{row.status} -> {to_status} not allowed",
            )
        if to_status == "CERTIFIED":
            result = self._policy.evaluate(
                PolicyEvaluationRequest(
                    decision_point="AGENT_CERTIFICATION",
                    subject_type="AGENT",
                    subject_id=str(row.id),
                    action="certify",
                    resource={},
                    input_snapshot={"manifest_hash": row.manifest_hash},
                    rules=default_system_rules(),
                    correlation_id=str(row.id),
                )
            )
            if self._enforce and result.outcome is PolicyOutcome.DENY:
                raise DomainError(ErrorCode.POLICY_DENIED, "certification denied")
        row.status = to_status
        return row

    async def create_deployment(
        self,
        session: AsyncSession,
        *,
        agent_spec_version_id: uuid.UUID,
        organization_version_id: uuid.UUID,
        goal_id: uuid.UUID,
        role: str,
        effective_permissions: dict[str, Any] | None = None,
        actor: str = "regent-core",
    ) -> AgentDeploymentModel:
        spec_ver = await session.get(AgentSpecVersionModel, agent_spec_version_id)
        if spec_ver is None or spec_ver.status != "CERTIFIED":
            raise DomainError(ErrorCode.INVALID_STATE, "spec version must be CERTIFIED")
        result = self._policy.evaluate(
            PolicyEvaluationRequest(
                decision_point="AGENT_DEPLOYMENT",
                subject_type="AGENT",
                subject_id=str(agent_spec_version_id),
                action="deploy",
                resource={"goal_id": str(goal_id)},
                input_snapshot={"role": role},
                rules=default_system_rules(),
                correlation_id=str(organization_version_id),
            )
        )
        if self._enforce and result.outcome is PolicyOutcome.DENY:
            raise DomainError(ErrorCode.POLICY_DENIED, "deployment denied")

        dep = AgentDeploymentModel(
            id=uuid.uuid4(),
            agent_spec_version_id=agent_spec_version_id,
            organization_version_id=organization_version_id,
            goal_id=goal_id,
            role=role,
            status="PENDING",
            effective_permissions_json=dict(effective_permissions or {}),
        )
        session.add(dep)
        session.add(
            AgentLifecycleEventModel(
                id=uuid.uuid4(),
                deployment_id=dep.id,
                from_status=None,
                to_status="PENDING",
                reason="created",
                actor=actor,
            )
        )
        await session.flush()
        return dep

    async def transition_deployment(
        self,
        session: AsyncSession,
        deployment_id: uuid.UUID,
        *,
        to_status: str,
        actor: str,
        reason: str,
    ) -> AgentDeploymentModel:
        dep = await session.get(AgentDeploymentModel, deployment_id, with_for_update=True)
        if dep is None:
            raise DomainError(ErrorCode.NOT_FOUND, "deployment not found")
        allowed = DEPLOY_TRANSITIONS.get(dep.status, frozenset())
        if to_status not in allowed:
            raise DomainError(
                ErrorCode.INVALID_AGENT_LIFECYCLE_TRANSITION,
                f"{dep.status} -> {to_status} not allowed",
            )
        if dep.status == "RETIRED":
            raise DomainError(
                ErrorCode.INVALID_AGENT_LIFECYCLE_TRANSITION,
                "RETIRED is terminal; create a new deployment",
            )
        prev = dep.status
        dep.status = to_status
        dep.updated_at = datetime.now(UTC)
        session.add(
            AgentLifecycleEventModel(
                id=uuid.uuid4(),
                deployment_id=dep.id,
                from_status=prev,
                to_status=to_status,
                reason=reason,
                actor=actor,
            )
        )
        return dep

    async def add_relationship(
        self,
        session: AsyncSession,
        *,
        organization_version_id: uuid.UUID,
        source_deployment_id: uuid.UUID,
        target_deployment_id: uuid.UUID,
        relationship_type: str,
    ) -> AgentRelationshipModel:
        if relationship_type not in RELATIONSHIP_TYPES:
            raise DomainError(ErrorCode.INVALID_STATE, f"unknown relationship {relationship_type}")
        source = await session.get(AgentDeploymentModel, source_deployment_id)
        target = await session.get(AgentDeploymentModel, target_deployment_id)
        if source is None or target is None:
            raise DomainError(ErrorCode.NOT_FOUND, "deployment not found")
        if (
            source.organization_version_id != organization_version_id
            or target.organization_version_id != organization_version_id
        ):
            raise DomainError(ErrorCode.INVALID_STATE, "relationship must share org version")
        # APPROVES does not grant execution capability (invariant documented in permissions)
        rel = AgentRelationshipModel(
            id=uuid.uuid4(),
            organization_version_id=organization_version_id,
            source_deployment_id=source_deployment_id,
            target_deployment_id=target_deployment_id,
            relationship_type=relationship_type,
            valid_from=datetime.now(UTC),
        )
        session.add(rel)
        await session.flush()
        return rel

    @staticmethod
    def assert_producer_reviewer_separation(
        producer_deployment_id: uuid.UUID,
        reviewer_deployment_id: uuid.UUID,
    ) -> None:
        if producer_deployment_id == reviewer_deployment_id:
            raise DomainError(
                ErrorCode.POLICY_DENIED,
                "producer and final reviewer/approver must differ",
            )
