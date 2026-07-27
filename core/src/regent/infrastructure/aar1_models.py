"""AAR-1 Foundation ORM models (Expand + Contract).

Imported by ``regent.infrastructure.models`` so Alembic metadata includes these tables.
Contract stops writing legacy mutable org fields as truth; historical columns remain readable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from regent.infrastructure.models import Base, Timestamped


class ConstitutionModel(Base):
    __tablename__ = "constitutions"
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('SYSTEM','ORG','PROJECT','GOAL')",
            name="ck_constitutions_scope_type",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','SUSPENDED','REVOKED')",
            name="ck_constitutions_status",
        ),
        UniqueConstraint("scope_type", "scope_id", "name", name="uq_constitutions_scope_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ConstitutionVersionModel(Base):
    __tablename__ = "constitution_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','ACTIVE','SUPERSEDED','REVOKED')",
            name="ck_constitution_versions_status",
        ),
        UniqueConstraint("constitution_id", "version", name="uq_constitution_versions_id_ver"),
        UniqueConstraint(
            "constitution_id", "content_hash", name="uq_constitution_versions_id_hash"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    constitution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("constitutions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    rules_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PolicyBindingModel(Base):
    __tablename__ = "policy_bindings"
    __table_args__ = (
        CheckConstraint(
            "subject_type IN ('ORG','PROJECT','GOAL','AGENT','TOOL','MCP')",
            name="ck_policy_bindings_subject_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    constitution_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("constitution_versions.id", ondelete="CASCADE"), nullable=False
    )
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    goal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PolicyEvaluationModel(Base):
    __tablename__ = "policy_evaluations"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('ALLOW','DENY','REQUIRE_PERMIT','REQUIRE_HUMAN')",
            name="ck_policy_evaluations_outcome",
        ),
        Index("ix_policy_evaluations_correlation", "correlation_id"),
        Index("ix_policy_evaluations_decision_point", "decision_point", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    constitution_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("constitution_versions.id", ondelete="SET NULL")
    )
    decision_point: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    resource: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    input_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    matched_rule_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    obligations_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reason_codes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    evaluator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    causation_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OrganizationTemplateModel(Base):
    __tablename__ = "organization_templates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','CERTIFIED','REVOKED')",
            name="ck_organization_templates_status",
        ),
        UniqueConstraint("name", "semantic_version", name="uq_organization_templates_name_ver"),
        UniqueConstraint("content_hash", name="uq_organization_templates_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    semantic_version: Mapped[str] = mapped_column(String(32), nullable=False)
    topology_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OrganizationSnapshotModel(Base):
    """Immutable Goal/Constraint/Governance/Resource/State snapshot for a decision."""

    __tablename__ = "organization_snapshots"
    __table_args__ = (
        CheckConstraint(
            "snapshot_type IN ('GOAL','CONSTRAINT','GOVERNANCE','RESOURCE','STATE')",
            name="ck_organization_snapshots_type",
        ),
        UniqueConstraint(
            "goal_id", "snapshot_type", "content_hash", name="uq_organization_snapshots_dedupe"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OrganizationDecisionModel(Base):
    __tablename__ = "organization_decisions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PROPOSED','ACCEPTED','REJECTED','SUPERSEDED')",
            name="ck_organization_decisions_status",
        ),
        CheckConstraint(
            "trigger IN ('INITIAL','CAPABILITY_GAP','RESOURCE_CHANGE','POLICY_CHANGE',"
            "'ATTRIBUTABLE_FAILURE','KPI_DEVIATION','MANUAL','ROLLBACK')",
            name="ck_organization_decisions_trigger",
        ),
        Index("ix_organization_decisions_goal", "goal_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    previous_organization_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_versions.id", ondelete="SET NULL")
    )
    goal_spec_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("goal_specs.id", ondelete="SET NULL")
    )
    constitution_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("constitution_versions.id", ondelete="SET NULL")
    )
    resource_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_snapshots.id", ondelete="SET NULL")
    )
    state_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_snapshots.id", ondelete="SET NULL")
    )
    utility_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    trigger: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OrganizationCandidateRecordModel(Base):
    __tablename__ = "organization_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('GENERATED','FEASIBLE','INFEASIBLE','SELECTED','REJECTED')",
            name="ck_organization_candidates_status",
        ),
        CheckConstraint(
            "generation_method IN ('CERTIFIED_TEMPLATE','LLM_DRAFT')",
            name="ck_organization_candidates_gen_method",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization_decisions.id", ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_templates.id", ondelete="SET NULL")
    )
    topology_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    required_resources_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    generation_method: Mapped[str] = mapped_column(String(32), nullable=False)
    generator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    predicted_utility: Mapped[float | None] = mapped_column(Float)
    utility_components_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )


class OrganizationCandidateCheckModel(Base):
    __tablename__ = "organization_candidate_checks"
    __table_args__ = (
        CheckConstraint(
            "check_type IN ('C','V','R')",
            name="ck_organization_candidate_checks_type",
        ),
        CheckConstraint(
            "result IN ('PASS','FAIL','UNKNOWN')",
            name="ck_organization_candidate_checks_result",
        ),
        UniqueConstraint(
            "candidate_id", "check_type", name="uq_organization_candidate_checks_cand_type"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization_candidates.id", ondelete="CASCADE"), nullable=False
    )
    check_type: Mapped[str] = mapped_column(String(8), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_evaluations.id", ondelete="SET NULL")
    )
    reason_codes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_refs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class OrganizationVersionModel(Base):
    __tablename__ = "organization_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','ACTIVE','RETIRED','SUPERSEDED')",
            name="ck_organization_versions_status",
        ),
        UniqueConstraint("organization_id", "version", name="uq_organization_versions_org_ver"),
        Index("ix_organization_versions_org_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    predecessor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_versions.id", ondelete="SET NULL")
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_decisions.id", ondelete="SET NULL")
    )
    topology_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentSpecVersionModel(Base):
    __tablename__ = "agent_spec_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','CERTIFIED','SUPERSEDED','REVOKED')",
            name="ck_agent_spec_versions_status",
        ),
        UniqueConstraint(
            "agent_spec_id", "version", name="uq_agent_spec_versions_spec_ver"
        ),
        UniqueConstraint("manifest_hash", name="uq_agent_spec_versions_manifest_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    agent_spec_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_specs.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    constitution_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("constitution_versions.id", ondelete="SET NULL")
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentDeploymentModel(Base):
    __tablename__ = "agent_deployments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','DEPLOYED','OPERATING','SUSPENDED',"
            "'UPGRADING','RETIRED','FAILED')",
            name="ck_agent_deployments_status",
        ),
        Index("ix_agent_deployments_org_version", "organization_version_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    agent_spec_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_spec_versions.id", ondelete="RESTRICT"), nullable=False
    )
    organization_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization_versions.id", ondelete="CASCADE"), nullable=False
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    runtime_profile_id: Mapped[str | None] = mapped_column(String(128))
    effective_permissions_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentLifecycleEventModel(Base):
    __tablename__ = "agent_lifecycle_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentRelationshipModel(Base):
    __tablename__ = "agent_relationships"
    __table_args__ = (
        CheckConstraint(
            "relationship_type IN ('SUPERVISES','DELEGATES_TO','DEPENDS_ON','REVIEWS',"
            "'APPROVES','ESCALATES_TO','SHARES_MEMORY_WITH')",
            name="ck_agent_relationships_type",
        ),
        UniqueConstraint(
            "organization_version_id",
            "source_deployment_id",
            "target_deployment_id",
            "relationship_type",
            name="uq_agent_relationships_edge",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization_versions.id", ondelete="CASCADE"), nullable=False
    )
    source_deployment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="CASCADE"), nullable=False
    )
    target_deployment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentTaskModel(Timestamped, Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED','OFFERED','ACCEPTED','RUNNING','SUCCEEDED',"
            "'FAILED_RETRYABLE','FAILED_TERMINAL','UNKNOWN','RECONCILING',"
            "'MANUAL_REVIEW','TIMED_OUT','CANCELLED')",
            name="ck_agent_tasks_status",
        ),
        UniqueConstraint(
            "target_deployment_id", "idempotency_key", name="uq_agent_tasks_target_idem"
        ),
        Index("ix_agent_tasks_claimable", "status", "not_before", "lease_expires_at"),
        Index("ix_agent_tasks_goal", "goal_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    protocol_version: Mapped[str] = mapped_column(String(32), nullable=False, default="a2a/v1")
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    work_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("works.id", ondelete="SET NULL"))
    organization_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization_versions.id", ondelete="RESTRICT"), nullable=False
    )
    source_deployment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="RESTRICT"), nullable=False
    )
    target_deployment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="RESTRICT"), nullable=False
    )
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_tasks.id", ondelete="SET NULL")
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_scope: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    permit_refs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    payload_ref: Mapped[str | None] = mapped_column(String(512))
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    causation_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_token: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_ref: Mapped[str | None] = mapped_column(String(512))
    error_code: Mapped[str | None] = mapped_column(String(128))


class EnvelopeNonceModel(Base):
    __tablename__ = "envelope_nonces"
    __table_args__ = (
        UniqueConstraint("nonce", "signing_key_id", name="uq_envelope_nonces_nonce_key"),
        Index("ix_envelope_nonces_expires", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    signing_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EnvelopeSigningKeyModel(Base):
    __tablename__ = "envelope_signing_keys"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','ROTATING','RETIRED')",
            name="ck_envelope_signing_keys_status",
        ),
        UniqueConstraint("key_id", name="uq_envelope_signing_keys_key_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False, default="HMAC-SHA256")
    secret_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class McpServerModel(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DISCOVERED','CERTIFIED','SUSPENDED','REVOKED')",
            name="ck_mcp_servers_status",
        ),
        UniqueConstraint("name", "version", name="uq_mcp_servers_name_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    secret_ref: Mapped[str | None] = mapped_column(String(255))
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    certified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class McpToolBindingModel(Base):
    __tablename__ = "mcp_tool_bindings"
    __table_args__ = (
        CheckConstraint(
            "side_effect_class IN ('NONE','REVERSIBLE','IRREVERSIBLE')",
            name="ck_mcp_tool_bindings_side_effect",
        ),
        CheckConstraint(
            "status IN ('CANDIDATE','CERTIFIED','SUSPENDED','REVOKED')",
            name="ck_mcp_tool_bindings_status",
        ),
        UniqueConstraint("server_id", "tool_name", name="uq_mcp_tool_bindings_server_tool"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    server_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    input_schema_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    side_effect_class: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    allowlist_scopes_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class McpInvocationModel(Timestamped, Base):
    __tablename__ = "mcp_invocations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PREPARED','DISPATCHING','SUCCEEDED','FAILED',"
            "'UNKNOWN','RECONCILING','DENIED')",
            name="ck_mcp_invocations_status",
        ),
        UniqueConstraint("idempotency_key", name="uq_mcp_invocations_idempotency"),
        Index("ix_mcp_invocations_goal", "goal_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tool_binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mcp_tool_bindings.id", ondelete="RESTRICT"), nullable=False
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    caller_deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="SET NULL")
    )
    policy_evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_evaluations.id", ondelete="SET NULL")
    )
    external_operation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("external_operations.id", ondelete="SET NULL")
    )
    permit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("execution_permits.id", ondelete="SET NULL")
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_trust: Mapped[str] = mapped_column(
        String(32), nullable=False, default="UNTRUSTED_DATA"
    )
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    causation_id: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(128))


class Aar1BackfillReportModel(Base):
    """Idempotent count/hash report for OrganizationVersion v1 backfill."""

    __tablename__ = "aar1_backfill_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    report_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    organizations_count: Mapped[int] = mapped_column(Integer, nullable=False)
    versions_created: Mapped[int] = mapped_column(Integer, nullable=False)
    versions_skipped: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
