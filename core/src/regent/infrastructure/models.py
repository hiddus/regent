import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from regent.domain.states import GoalState, RunState, WorkState


class Base(DeclarativeBase):
    pass


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppProjectModel(Timestamped, Base):
    __tablename__ = "app_projects"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','ACTIVE','PAUSED','STOPPED','ARCHIVED')",
            name="ck_app_projects_status",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_intent: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)


class GoalModel(Timestamped, Base):
    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','READY','ACTIVE','PAUSED','WAITING_HUMAN',"
            "'BLOCKED','ACHIEVED','EXHAUSTED','FAILED','CANCELLED')",
            name="ck_goals_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    app_project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_projects.id", ondelete="SET NULL"), index=True
    )
    original_input: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=GoalState.DRAFT.value, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )

    specs: Mapped[list["GoalSpecModel"]] = relationship(
        back_populates="goal", cascade="all, delete-orphan"
    )
    works: Mapped[list["WorkModel"]] = relationship(back_populates="goal")


class GoalSpecModel(Base):
    __tablename__ = "goal_specs"
    __table_args__ = (
        UniqueConstraint("goal_id", "version", name="uq_goal_specs_goal_version"),
        CheckConstraint("version > 0", name="ck_goal_specs_positive_version"),
        CheckConstraint("status IN ('DRAFT','FROZEN','SUPERSEDED')", name="ck_goal_specs_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(255))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    explicit_constraints: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    system_inferences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    unknowns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    success_criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    goal: Mapped[GoalModel] = relationship(back_populates="specs")


class WorkModel(Timestamped, Base):
    __tablename__ = "works"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PLANNED','READY','RUNNING','EVALUATING','ACCEPTED',"
            "'REJECTED','WAITING_HUMAN','BLOCKED','UNKNOWN','CANCELLED')",
            name="ck_works_status",
        ),
        CheckConstraint("version >= 0", name="ck_works_nonnegative_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    input_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    acceptance_criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    dependency_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    budget: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=WorkState.PLANNED.value, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )

    goal: Mapped[GoalModel] = relationship(back_populates="works")
    runs: Mapped[list["RunModel"]] = relationship(back_populates="work")


class RunModel(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED','PERMIT_PENDING','QUEUED','RUNNING','EXECUTED',"
            "'FAILED','UNKNOWN','DENIED','EXPIRED','CANCELLED')",
            name="ck_runs_status",
        ),
        CheckConstraint("version >= 0", name="ck_runs_nonnegative_version"),
        Index(
            "uq_runs_one_active_per_work",
            "work_id",
            unique=True,
            postgresql_where=text("status IN ('CREATED','PERMIT_PENDING','QUEUED','RUNNING')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default=RunState.CREATED.value, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(255))
    agent_spec_ref: Mapped[str | None] = mapped_column(String(255))
    model_ref: Mapped[str | None] = mapped_column(String(255))
    tool_ref: Mapped[str | None] = mapped_column(String(255))
    input_version: Mapped[str] = mapped_column(String(255), nullable=False)
    permit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    resource_usage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    work: Mapped[WorkModel] = relationship(back_populates="runs")


class AuditRecordModel(Base):
    __tablename__ = "audit_records"
    __table_args__ = (
        Index(
            "ix_audit_aggregate_timeline",
            "aggregate_type",
            "aggregate_id",
            "occurred_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    causation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OutboxEventModel(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','DISPATCHING','DISPATCHED','FAILED','DEAD_LETTER')",
            name="ck_outbox_events_status",
        ),
        Index("ix_outbox_pending", "status", "available_at"),
        Index("ix_outbox_claimable", "status", "available_at", "lease_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    causation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class WorkerLeaseModel(Base):
    __tablename__ = "worker_leases"
    __table_args__ = (
        UniqueConstraint("lease_token", name="uq_worker_leases_token"),
        Index("ix_worker_leases_expires_at", "expires_at"),
    )

    worker_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    lease_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )


class ArtifactModel(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index(
            "uq_artifacts_work_type_version",
            "work_id",
            "artifact_type",
            "version",
            unique=True,
            postgresql_where=text("work_id IS NOT NULL"),
        ),
        Index(
            "uq_artifacts_goal_type_version_unscoped",
            "goal_id",
            "artifact_type",
            "version",
            unique=True,
            postgresql_where=text("work_id IS NULL"),
        ),
        CheckConstraint("version > 0", name="ck_artifacts_positive_version"),
        Index("ix_artifacts_content_hash", "content_hash"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    work_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="RESTRICT"), index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_ref: Mapped[str | None] = mapped_column(String(512))
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    producer_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EvidenceModel(Base):
    __tablename__ = "evidence"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    work_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="RESTRICT"), index=True
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT")
    )
    evidence_type: Mapped[str] = mapped_column(String(128), nullable=False)
    uri: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    producer_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    quality_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DurableTimerModel(Base):
    __tablename__ = "durable_timers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','CLAIMED','FIRED','CANCELLED','FAILED')",
            name="ck_durable_timers_status",
        ),
        Index("ix_durable_timers_due", "status", "due_at", "lease_expires_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    command: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExecutionPermitModel(Base):
    __tablename__ = "execution_permits"
    __table_args__ = (
        CheckConstraint(
            "status IN ('REQUESTED','APPROVED','CLAIMED','CONSUMED','DENIED','EXPIRED','REVOKED')",
            name="ck_execution_permits_status",
        ),
        UniqueConstraint("nonce", name="uq_execution_permits_nonce"),
        UniqueConstraint("idempotency_key", name="uq_execution_permits_idempotency_key"),
        Index("ix_execution_permits_valid_until", "status", "valid_until"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="RESTRICT"), nullable=False
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    parameter_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    data_scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    network_scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    resource_limit: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="REQUESTED")
    nonce: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HumanTaskModel(Base):
    __tablename__ = "human_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN','COMPLETED','TIMED_OUT','CANCELLED')",
            name="ck_human_tasks_status",
        ),
        Index("ix_human_tasks_due", "status", "due_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="RESTRICT"), nullable=False
    )
    work_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("works.id", ondelete="RESTRICT"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"))
    task_type: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    assigned_to: Mapped[str | None] = mapped_column(String(255))
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CapabilityModel(Base):
    __tablename__ = "capabilities"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CANDIDATE','GOAL_CERTIFIED','VERIFIED','REVOKED')",
            name="ck_capabilities_status",
        ),
        UniqueConstraint("name", "scope_goal_id", name="uq_capabilities_name_scope"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_goal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE")
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    verification: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentSpecModel(Base):
    __tablename__ = "agent_specs"
    __table_args__ = (
        CheckConstraint("status IN ('CANDIDATE','ACTIVE','REVOKED')", name="ck_agent_specs_status"),
        UniqueConstraint(
            "name", "version", "scope_goal_id", name="uq_agent_specs_name_version_scope"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_goal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE")
    )
    capability_names: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    model_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OrganizationModel(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','DISSOLVED')", name="ck_organizations_status"),
        UniqueConstraint("goal_id", name="uq_organizations_goal"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    max_agents: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AssignmentModel(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("organization_id", "work_id", name="uq_assignments_org_work"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"), nullable=False
    )
    agent_spec_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_specs.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(128), nullable=False)
    delegated_capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ToolSpecModel(Base):
    __tablename__ = "tool_specs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CANDIDATE','CERTIFIED','REVOKED')", name="ck_tool_specs_status"
        ),
        UniqueConstraint(
            "name", "version", "scope_goal_id", name="uq_tool_specs_name_version_scope"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    capability_name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope_goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    entrypoint: Mapped[str] = mapped_column(String(512), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ToolCertificationModel(Base):
    __tablename__ = "tool_certifications"
    __table_args__ = (UniqueConstraint("tool_spec_id", name="uq_tool_certifications_tool"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tool_spec_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tool_specs.id", ondelete="CASCADE"), nullable=False
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    public_passed: Mapped[bool] = mapped_column(nullable=False)
    hidden_passed: Mapped[bool] = mapped_column(nullable=False)
    public_evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hidden_evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    security_checks: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    certified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ObservationModel(Base):
    __tablename__ = "observations"
    __table_args__ = (UniqueConstraint("event_id", name="uq_observations_event_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    goal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("goals.id", ondelete="SET NULL"))
    metric_name: Mapped[str] = mapped_column(String(255), nullable=False)
    metric_value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    definition_version: Mapped[str] = mapped_column(String(128), nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    is_bot: Mapped[bool] = mapped_column(nullable=False, default=False)
    is_internal: Mapped[bool] = mapped_column(nullable=False, default=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExperienceRecordModel(Base):
    __tablename__ = "experience_records"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    observation_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    lesson: Mapped[str] = mapped_column(Text, nullable=False)
    replan_triggered: Mapped[bool] = mapped_column(nullable=False, default=False)
    attribution: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SideEffectAttemptModel(Base):
    __tablename__ = "side_effect_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('STARTED','SUCCEEDED','FAILED','UNKNOWN','RECONCILED')",
            name="ck_side_effect_attempts_status",
        ),
        UniqueConstraint("permit_id", name="uq_side_effect_attempts_permit"),
        UniqueConstraint("idempotency_key", name="uq_side_effect_attempts_idempotency"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    permit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("execution_permits.id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    external_request_id: Mapped[str | None] = mapped_column(String(255))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reconciliation_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentManifestModel(Base):
    __tablename__ = "experiment_manifests"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_experiment_manifests_name_version"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExperimentRunModel(Base):
    __tablename__ = "experiment_runs"
    __table_args__ = (
        UniqueConstraint(
            "manifest_id", "task_id", "mode", "repetition", name="uq_experiment_runs_cell"
        ),
        CheckConstraint("mode IN ('A','B','C')", name="ck_experiment_runs_mode"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_manifests.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_class: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(1), nullable=False)
    repetition: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_count: Mapped[int] = mapped_column(Integer, nullable=False)
    success: Mapped[bool] = mapped_column(nullable=False)
    quality_score: Mapped[float] = mapped_column(nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_cost: Mapped[float] = mapped_column(nullable=False)
    human_minutes: Mapped[float] = mapped_column(nullable=False)
    human_task_count: Mapped[int] = mapped_column(Integer, nullable=False)
    safety_incidents: Mapped[int] = mapped_column(Integer, nullable=False)
    coordination_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    predicted_gap: Mapped[str] = mapped_column(String(64), nullable=False)
    true_gap: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_reused: Mapped[bool] = mapped_column(nullable=False)
    recovery_correct: Mapped[bool] = mapped_column(nullable=False)
    failure_class: Mapped[str | None] = mapped_column(String(128))
    raw_evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProductDecisionRecordModel(Base):
    __tablename__ = "product_decision_records"
    __table_args__ = (UniqueConstraint("manifest_id", name="uq_product_decision_manifest"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_manifests.id", ondelete="RESTRICT"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DiscoveryRoundModel(Timestamped, Base):
    __tablename__ = "discovery_rounds"
    __table_args__ = (
        CheckConstraint(
            "status IN ('REQUESTED','RESEARCHING','READY','DECIDED',"
            "'BLOCKED','FAILED','EXHAUSTED')",
            name="ck_discovery_rounds_status",
        ),
        CheckConstraint("version >= 0", name="ck_discovery_rounds_version"),
        UniqueConstraint("goal_id", "round", name="uq_discovery_rounds_goal_round"),
        UniqueConstraint("idempotency_key", name="uq_discovery_rounds_idempotency"),
        Index("ix_discovery_rounds_correlation_id", "correlation_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="RESTRICT"), nullable=False
    )
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    budget: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(128))


class ProductHypothesisModel(Base):
    __tablename__ = "product_hypotheses"
    __table_args__ = (
        CheckConstraint(
            "eligibility IN ('ELIGIBLE','INVALID')", name="ck_product_hypotheses_eligibility"
        ),
        UniqueConstraint("round_id", "candidate_key", name="uq_product_hypotheses_round_candidate"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    round_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discovery_rounds.id", ondelete="CASCADE"), nullable=False
    )
    candidate_key: Mapped[str] = mapped_column(String(80), nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    eligibility: Mapped[str] = mapped_column(String(16), nullable=False)
    invalid_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    generator_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HypothesisEvidenceRefModel(Base):
    __tablename__ = "hypothesis_evidence_refs"
    __table_args__ = (
        UniqueConstraint(
            "hypothesis_id", "evidence_id", "claim_key", name="uq_hypothesis_evidence_claim"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    hypothesis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_hypotheses.id", ondelete="CASCADE"), nullable=False
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="RESTRICT"), nullable=False
    )
    claim_key: Mapped[str] = mapped_column(String(120), nullable=False)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)


class HypothesisDecisionModel(Base):
    __tablename__ = "hypothesis_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('SELECT','RESEARCH_MORE','STOP')", name="ck_hypothesis_decisions_decision"
        ),
        CheckConstraint(
            "(decision = 'SELECT' AND selected_hypothesis_id IS NOT NULL) OR "
            "(decision <> 'SELECT' AND selected_hypothesis_id IS NULL)",
            name="ck_hypothesis_decisions_selection",
        ),
        UniqueConstraint("round_id", name="uq_hypothesis_decisions_round"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    round_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discovery_rounds.id", ondelete="CASCADE"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    selected_hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_hypotheses.id", ondelete="RESTRICT")
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RequirementRevisionModel(Base):
    __tablename__ = "requirement_revisions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','VALIDATED','SUPERSEDED','WITHDRAWN')",
            name="ck_requirement_revisions_status",
        ),
        CheckConstraint("version >= 0", name="ck_requirement_revisions_version"),
        UniqueConstraint(
            "goal_id",
            "requirement_key",
            "revision",
            name="uq_requirement_revisions_goal_key_revision",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="RESTRICT"), nullable=False
    )
    hypothesis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product_hypotheses.id", ondelete="RESTRICT"), nullable=False
    )
    requirement_key: Mapped[str] = mapped_column(String(120), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    predecessor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("requirement_revisions.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generator_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CapabilityResolutionPlanModel(Base):
    __tablename__ = "capability_resolution_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','FROZEN','SATISFIED','WAITING_HUMAN','BLOCKED','FAILED')",
            name="ck_capability_resolution_plans_status",
        ),
        CheckConstraint("version >= 0", name="ck_capability_resolution_plans_version"),
        UniqueConstraint(
            "requirement_revision_id", name="uq_capability_resolution_plans_requirement"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    requirement_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirement_revisions.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CapabilityResolutionItemModel(Base):
    __tablename__ = "capability_resolution_items"
    __table_args__ = (
        CheckConstraint(
            "resolution_method IN ('REUSE','CONFIGURE','COMPOSE','BUILD','REQUEST_HUMAN','BLOCK')",
            name="ck_capability_resolution_items_method",
        ),
        UniqueConstraint(
            "plan_id", "requirement_key", name="uq_capability_resolution_items_requirement"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("capability_resolution_plans.id", ondelete="CASCADE"), nullable=False
    )
    requirement_key: Mapped[str] = mapped_column(String(120), nullable=False)
    capability_name: Mapped[str] = mapped_column(String(255), nullable=False)
    gap_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resolution_method: Mapped[str] = mapped_column(String(32), nullable=False)
    capability_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("capabilities.id", ondelete="RESTRICT")
    )
    tool_spec_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tool_specs.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)


class GenerationPlanModel(Base):
    __tablename__ = "generation_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','FROZEN','EXECUTING','COMPLETED','FAILED','CANCELLED')",
            name="ck_generation_plans_status",
        ),
        CheckConstraint("version >= 0", name="ck_generation_plans_version"),
        UniqueConstraint("input_digest", name="uq_generation_plans_input_digest"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    requirement_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirement_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    capability_resolution_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("capability_resolution_plans.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    architecture_summary: Mapped[str] = mapped_column(Text, nullable=False)
    component_plan: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GenerationRunModel(Base):
    __tablename__ = "generation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('REQUESTED','PLANNING','GENERATING','VALIDATING',"
            "'COMMITTING','COMPLETED','FAILED','CANCELLED')",
            name="ck_generation_runs_status",
        ),
        CheckConstraint("version >= 0", name="ck_generation_runs_version"),
        UniqueConstraint("plan_id", "attempt", name="uq_generation_runs_plan_attempt"),
        UniqueConstraint("idempotency_key", name="uq_generation_runs_idempotency"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_plans.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    model_ref: Mapped[str | None] = mapped_column(String(255))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    change_set_digest: Mapped[str | None] = mapped_column(String(64))
    failure_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FileChangeSetModel(Base):
    __tablename__ = "file_change_sets"
    __table_args__ = (UniqueConstraint("generation_run_id", name="uq_file_change_sets_run"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    generation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generator_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkspaceSnapshotModel(Base):
    __tablename__ = "workspace_snapshots"
    __table_args__ = (UniqueConstraint("generation_run_id", name="uq_workspace_snapshots_run"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    generation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="RESTRICT"), nullable=False
    )
    manifest_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_archive_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_locator: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DependencyResolutionModel(Base):
    __tablename__ = "dependency_resolutions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('REQUESTED','RESOLVING','MATERIALIZED','REJECTED','FAILED','UNKNOWN')",
            name="ck_dependency_resolutions_status",
        ),
        UniqueConstraint("workspace_snapshot_id", name="uq_dependency_resolutions_snapshot"),
        UniqueConstraint("idempotency_key", name="uq_dependency_resolutions_idempotency"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    dependency_intents: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    lockfile_uri: Mapped[str | None] = mapped_column(String(1024))
    bundle_uri: Mapped[str | None] = mapped_column(String(1024))
    bundle_hash: Mapped[str | None] = mapped_column(String(64))
    sbom_uri: Mapped[str | None] = mapped_column(String(1024))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(128))
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppBuildModel(Base):
    __tablename__ = "app_builds"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','PASSED','FAILED','UNKNOWN')",
            name="ck_app_builds_status",
        ),
        UniqueConstraint("idempotency_key", name="uq_app_builds_idempotency"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    dependency_resolution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dependency_resolutions.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    external_operation_id: Mapped[str | None] = mapped_column(String(255))
    build_artifact_uri: Mapped[str | None] = mapped_column(String(1024))
    build_artifact_hash: Mapped[str | None] = mapped_column(String(64))
    log_uri: Mapped[str | None] = mapped_column(String(1024))
    failure_code: Mapped[str | None] = mapped_column(String(128))
    reconciliation_required: Mapped[bool] = mapped_column(nullable=False, default=False)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class VerificationReportModel(Base):
    __tablename__ = "verification_reports"
    __table_args__ = (UniqueConstraint("app_build_id", name="uq_verification_reports_build"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    app_build_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_builds.id", ondelete="CASCADE"), nullable=False
    )
    passed: Mapped[bool] = mapped_column(nullable=False)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    evidence_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReleaseCandidateModel(Base):
    __tablename__ = "release_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','READY','APPROVED','REJECTED')",
            name="ck_release_candidates_status",
        ),
        UniqueConstraint("app_build_id", name="uq_release_candidates_build"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    app_build_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_builds.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    human_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("human_tasks.id", ondelete="RESTRICT")
    )
    approved_by: Mapped[str | None] = mapped_column(String(255))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DeploymentModel(Base):
    __tablename__ = "deployments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('REQUESTED','DEPLOYING','SUCCEEDED','FAILED','UNKNOWN',"
            "'SUPERSEDED','ROLLED_BACK')",
            name="ck_deployments_status",
        ),
        UniqueConstraint("idempotency_key", name="uq_deployments_idempotency"),
        UniqueConstraint("external_deployment_id", name="uq_deployments_external_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    release_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("release_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    permit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("execution_permits.id", ondelete="RESTRICT"), nullable=False
    )
    rollback_permit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("execution_permits.id", ondelete="RESTRICT")
    )
    environment: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    external_deployment_id: Mapped[str | None] = mapped_column(String(255))
    endpoint: Mapped[str | None] = mapped_column(String(1024))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(128))
    reconciliation_required: Mapped[bool] = mapped_column(nullable=False, default=False)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MetricDefinitionBindingModel(Base):
    __tablename__ = "metric_definition_bindings"
    __table_args__ = (
        UniqueConstraint(
            "goal_id",
            "metric_key",
            "definition_version",
            name="uq_metric_bindings_goal_key_version",
        ),
        CheckConstraint(
            "(deployment_id IS NOT NULL) <> (preview_release_id IS NOT NULL)",
            name="ck_metric_bindings_one_target",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("deployments.id", ondelete="RESTRICT")
    )
    preview_release_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_preview_releases.id", ondelete="RESTRICT")
    )
    metric_key: Mapped[str] = mapped_column(String(255), nullable=False)
    definition_version: Mapped[str] = mapped_column(String(128), nullable=False)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GateEvaluationModel(Base):
    __tablename__ = "gate_evaluations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PASSED','FAILED','INSUFFICIENT_EVIDENCE')",
            name="ck_gate_evaluations_status",
        ),
        UniqueConstraint("input_digest", name="uq_gate_evaluations_input_digest"),
        CheckConstraint(
            "(deployment_id IS NOT NULL) <> (preview_release_id IS NOT NULL)",
            name="ck_gate_evaluations_one_target",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("deployments.id", ondelete="RESTRICT")
    )
    preview_release_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_preview_releases.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    observation_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IterationDecisionModel(Base):
    __tablename__ = "iteration_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('CONTINUE','REVISE','STOP')",
            name="ck_iteration_decisions_decision",
        ),
        CheckConstraint(
            "(decision = 'REVISE' AND primary_hypothesis IS NOT NULL AND new_work_id IS NOT NULL) OR "  # noqa: E501
            "(decision <> 'REVISE' AND primary_hypothesis IS NULL AND new_work_id IS NULL)",
            name="ck_iteration_decisions_revise_fields",
        ),
        UniqueConstraint("gate_evaluation_id", name="uq_iteration_decisions_gate"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    gate_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gate_evaluations.id", ondelete="RESTRICT"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    primary_hypothesis: Mapped[str | None] = mapped_column(Text)
    new_work_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT")
    )
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ConversationModel(Timestamped, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="ck_conversations_status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    app_project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_projects.id", ondelete="CASCADE"), unique=True
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("goals.id", ondelete="SET NULL"), unique=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class ConversationMessageModel(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('USER','ASSISTANT','SYSTEM','EVENT')",
            name="ck_conversation_messages_role",
        ),
        UniqueConstraint("conversation_id", "ordinal", name="uq_conversation_messages_ordinal"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False, default="TEXT")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ConversationCommandModel(Base):
    __tablename__ = "conversation_commands"
    __table_args__ = (
        CheckConstraint(
            "command_type IN ('QUERY','MODIFY','CONTINUE')",
            name="ck_conversation_commands_type",
        ),
        CheckConstraint(
            "status IN ('INTERPRETED','APPLIED','FAILED')",
            name="ck_conversation_commands_status",
        ),
        UniqueConstraint("user_message_id", name="uq_conversation_commands_message"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    app_project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_projects.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="CASCADE"), nullable=False
    )
    command_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    interpretation_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    interpretation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resulting_goal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("goals.id", ondelete="SET NULL")
    )
    model_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppPreviewReleaseModel(Base):
    __tablename__ = "app_preview_releases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('GENERATING','PREVIEW_READY','FAILED')",
            name="ck_app_preview_releases_status",
        ),
        UniqueConstraint("goal_id", name="uq_app_preview_releases_goal"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    app_project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_projects.id", ondelete="CASCADE"), nullable=False
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_hash: Mapped[str | None] = mapped_column(String(64))
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    workspace_locator: Mapped[str | None] = mapped_column(String(1024))
    preview_endpoint: Mapped[str | None] = mapped_column(String(1024))
    verification_checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    model_ref: Mapped[str | None] = mapped_column(String(255))
    failure_code: Mapped[str | None] = mapped_column(String(128))
    failure_summary: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SelfImprovementRunModel(Base):
    __tablename__ = "self_improvement_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PROPOSED','CANDIDATE_READY','AWAITING_APPROVAL',"
            "'APPROVED','REJECTED','FAILED')",
            name="ck_self_improvement_runs_status",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    primary_problem: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    target_file: Mapped[str] = mapped_column(String(1024), nullable=False)
    baseline_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_hash: Mapped[str | None] = mapped_column(String(64))
    candidate_workspace: Mapped[str | None] = mapped_column(String(1024))
    expected_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    verification_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    model_ref: Mapped[str | None] = mapped_column(String(255))
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
