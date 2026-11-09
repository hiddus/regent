"""AAR-1 Foundation Expand: constitution/policy/org version/agent task/MCP tables.

Revision ID: 20260727_0032
Revises: 20260727_0031

Expand-only: no Contract (no drops of legacy org fields). Backfills OrganizationVersion v1
with a repeatable count/hash report.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_0032"
down_revision: str | None = "20260727_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _canonical_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    op.create_table(
        "constitutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope_type", sa.String(32), nullable=False),
        sa.Column("scope_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "scope_type IN ('SYSTEM','ORG','PROJECT','GOAL')",
            name="ck_constitutions_scope_type",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','SUSPENDED','REVOKED')", name="ck_constitutions_status"
        ),
        sa.UniqueConstraint("scope_type", "scope_id", "name", name="uq_constitutions_scope_name"),
    )

    op.create_table(
        "constitution_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "constitution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("constitutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("rules_json", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("approved_by", sa.String(255)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','ACTIVE','SUPERSEDED','REVOKED')",
            name="ck_constitution_versions_status",
        ),
        sa.UniqueConstraint(
            "constitution_id", "version", name="uq_constitution_versions_id_ver"
        ),
        sa.UniqueConstraint(
            "constitution_id", "content_hash", name="uq_constitution_versions_id_hash"
        ),
    )

    op.create_table(
        "policy_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "constitution_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("constitution_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(128), nullable=False),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "subject_type IN ('ORG','PROJECT','GOAL','AGENT','TOOL','MCP')",
            name="ck_policy_bindings_subject_type",
        ),
    )

    op.create_table(
        "policy_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "constitution_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("constitution_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("decision_point", sa.String(64), nullable=False),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(255), nullable=False),
        sa.Column("resource", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("input_snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column(
            "matched_rule_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "obligations_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "reason_codes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("evaluator_version", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("causation_id", sa.String(255)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "outcome IN ('ALLOW','DENY','REQUIRE_PERMIT','REQUIRE_HUMAN')",
            name="ck_policy_evaluations_outcome",
        ),
    )
    op.create_index(
        "ix_policy_evaluations_correlation", "policy_evaluations", ["correlation_id"]
    )
    op.create_index(
        "ix_policy_evaluations_decision_point",
        "policy_evaluations",
        ["decision_point", "created_at"],
    )

    op.create_table(
        "organization_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("semantic_version", sa.String(32), nullable=False),
        sa.Column("topology_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','CERTIFIED','REVOKED')",
            name="ck_organization_templates_status",
        ),
        sa.UniqueConstraint("name", "semantic_version", name="uq_organization_templates_name_ver"),
        sa.UniqueConstraint("content_hash", name="uq_organization_templates_hash"),
    )

    op.create_table(
        "organization_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_type", sa.String(32), nullable=False),
        sa.Column("content_json", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "snapshot_type IN ('GOAL','CONSTRAINT','GOVERNANCE','RESOURCE','STATE')",
            name="ck_organization_snapshots_type",
        ),
        sa.UniqueConstraint(
            "goal_id",
            "snapshot_type",
            "content_hash",
            name="uq_organization_snapshots_dedupe",
        ),
    )

    # Decisions before versions (nullable previous_version FK added after versions exist)
    op.create_table(
        "organization_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("previous_organization_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "goal_spec_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goal_specs.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "constitution_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("constitution_versions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "resource_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_snapshots.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "state_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_snapshots.id", ondelete="SET NULL"),
        ),
        sa.Column("utility_policy_version", sa.String(64), nullable=False),
        sa.Column("selected_candidate_id", postgresql.UUID(as_uuid=True)),
        sa.Column("trigger", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("decision_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('PROPOSED','ACCEPTED','REJECTED','SUPERSEDED')",
            name="ck_organization_decisions_status",
        ),
        sa.CheckConstraint(
            "trigger IN ('INITIAL','CAPABILITY_GAP','RESOURCE_CHANGE','POLICY_CHANGE',"
            "'ATTRIBUTABLE_FAILURE','KPI_DEVIATION','MANUAL','ROLLBACK')",
            name="ck_organization_decisions_trigger",
        ),
    )
    op.create_index(
        "ix_organization_decisions_goal", "organization_decisions", ["goal_id", "created_at"]
    )

    op.create_table(
        "organization_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "predecessor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_versions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_decisions.id", ondelete="SET NULL"),
        ),
        sa.Column("topology_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','ACTIVE','RETIRED','SUPERSEDED')",
            name="ck_organization_versions_status",
        ),
        sa.UniqueConstraint(
            "organization_id", "version", name="uq_organization_versions_org_ver"
        ),
    )
    op.create_index(
        "ix_organization_versions_org_status",
        "organization_versions",
        ["organization_id", "status"],
    )

    op.create_foreign_key(
        "fk_organization_decisions_prev_version",
        "organization_decisions",
        "organization_versions",
        ["previous_organization_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_organizations_current_version",
        "organizations",
        "organization_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "organization_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_decisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_templates.id", ondelete="SET NULL"),
        ),
        sa.Column("topology_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "required_resources_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("generation_method", sa.String(32), nullable=False),
        sa.Column("generator_version", sa.String(64), nullable=False),
        sa.Column("predicted_utility", sa.Float()),
        sa.Column(
            "utility_components_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "status IN ('GENERATED','FEASIBLE','INFEASIBLE','SELECTED','REJECTED')",
            name="ck_organization_candidates_status",
        ),
        sa.CheckConstraint(
            "generation_method IN ('CERTIFIED_TEMPLATE','LLM_DRAFT')",
            name="ck_organization_candidates_gen_method",
        ),
    )

    op.create_table(
        "organization_candidate_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("check_type", sa.String(8), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column(
            "policy_evaluation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("policy_evaluations.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "reason_codes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "check_type IN ('C','V','R')", name="ck_organization_candidate_checks_type"
        ),
        sa.CheckConstraint(
            "result IN ('PASS','FAIL','UNKNOWN')",
            name="ck_organization_candidate_checks_result",
        ),
        sa.UniqueConstraint(
            "candidate_id", "check_type", name="uq_organization_candidate_checks_cand_type"
        ),
    )

    op.create_table(
        "agent_spec_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_spec_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_specs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("manifest_json", postgresql.JSONB(), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column(
            "constitution_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("constitution_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','CERTIFIED','SUPERSEDED','REVOKED')",
            name="ck_agent_spec_versions_status",
        ),
        sa.UniqueConstraint(
            "agent_spec_id", "version", name="uq_agent_spec_versions_spec_ver"
        ),
        sa.UniqueConstraint("manifest_hash", name="uq_agent_spec_versions_manifest_hash"),
    )

    op.create_table(
        "agent_deployments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_spec_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_spec_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "organization_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("runtime_profile_id", sa.String(128)),
        sa.Column(
            "effective_permissions_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','DEPLOYED','OPERATING','SUSPENDED',"
            "'UPGRADING','RETIRED','FAILED')",
            name="ck_agent_deployments_status",
        ),
    )
    op.create_index(
        "ix_agent_deployments_org_version",
        "agent_deployments",
        ["organization_version_id", "status"],
    )

    op.create_table(
        "agent_lifecycle_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "deployment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_deployments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(32)),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("correlation_id", sa.String(255)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "agent_relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_deployment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_deployments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_deployment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_deployments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relationship_type", sa.String(32), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "relationship_type IN ('SUPERVISES','DELEGATES_TO','DEPENDS_ON','REVIEWS',"
            "'APPROVES','ESCALATES_TO','SHARES_MEMORY_WITH')",
            name="ck_agent_relationships_type",
        ),
        sa.UniqueConstraint(
            "organization_version_id",
            "source_deployment_id",
            "target_deployment_id",
            "relationship_type",
            name="uq_agent_relationships_edge",
        ),
    )

    op.create_table(
        "agent_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("protocol_version", sa.String(32), nullable=False, server_default="a2a/v1"),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "work_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("works.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "organization_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_deployment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_deployments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "target_deployment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_deployments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "parent_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_tasks.id", ondelete="SET NULL"),
        ),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column(
            "capability_scope",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "permit_refs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("payload_ref", sa.String(512)),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("causation_id", sa.String(255)),
        sa.Column("status", sa.String(32), nullable=False, server_default="CREATED"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("not_before", sa.DateTime(timezone=True)),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String(255)),
        sa.Column("lease_token", sa.String(255)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("result_ref", sa.String(512)),
        sa.Column("error_code", sa.String(128)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('CREATED','OFFERED','ACCEPTED','RUNNING','SUCCEEDED',"
            "'FAILED_RETRYABLE','FAILED_TERMINAL','UNKNOWN','RECONCILING',"
            "'MANUAL_REVIEW','TIMED_OUT','CANCELLED')",
            name="ck_agent_tasks_status",
        ),
        sa.UniqueConstraint(
            "target_deployment_id", "idempotency_key", name="uq_agent_tasks_target_idem"
        ),
    )
    op.create_index(
        "ix_agent_tasks_claimable",
        "agent_tasks",
        ["status", "not_before", "lease_expires_at"],
    )
    op.create_index("ix_agent_tasks_goal", "agent_tasks", ["goal_id", "status"])

    op.create_table(
        "envelope_signing_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key_id", sa.String(64), nullable=False),
        sa.Column("algorithm", sa.String(32), nullable=False, server_default="HMAC-SHA256"),
        sa.Column("secret_ref", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('ACTIVE','ROTATING','RETIRED')",
            name="ck_envelope_signing_keys_status",
        ),
        sa.UniqueConstraint("key_id", name="uq_envelope_signing_keys_key_id"),
    )

    op.create_table(
        "envelope_nonces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nonce", sa.String(128), nullable=False),
        sa.Column("signing_key_id", sa.String(64), nullable=False),
        sa.Column("message_id", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("nonce", "signing_key_id", name="uq_envelope_nonces_nonce_key"),
    )
    op.create_index("ix_envelope_nonces_expires", "envelope_nonces", ["expires_at"])

    op.create_table(
        "mcp_servers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("endpoint_ref", sa.String(512), nullable=False),
        sa.Column("secret_ref", sa.String(255)),
        sa.Column("schema_hash", sa.String(64), nullable=False),
        sa.Column("certified_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('DISCOVERED','CERTIFIED','SUSPENDED','REVOKED')",
            name="ck_mcp_servers_status",
        ),
        sa.UniqueConstraint("name", "version", name="uq_mcp_servers_name_version"),
    )

    op.create_table(
        "mcp_tool_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "server_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("input_schema_json", postgresql.JSONB(), nullable=False),
        sa.Column("schema_hash", sa.String(64), nullable=False),
        sa.Column("side_effect_class", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "allowlist_scopes_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "side_effect_class IN ('NONE','REVERSIBLE','IRREVERSIBLE')",
            name="ck_mcp_tool_bindings_side_effect",
        ),
        sa.CheckConstraint(
            "status IN ('CANDIDATE','CERTIFIED','SUSPENDED','REVOKED')",
            name="ck_mcp_tool_bindings_status",
        ),
        sa.UniqueConstraint("server_id", "tool_name", name="uq_mcp_tool_bindings_server_tool"),
    )

    op.create_table(
        "mcp_invocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tool_binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mcp_tool_bindings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "caller_deployment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_deployments.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "policy_evaluation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("policy_evaluations.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "external_operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("external_operations.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "permit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_permits.id", ondelete="SET NULL"),
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "output_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("output_trust", sa.String(32), nullable=False, server_default="UNTRUSTED_DATA"),
        sa.Column("correlation_id", sa.String(255), nullable=False),
        sa.Column("causation_id", sa.String(255)),
        sa.Column("error_code", sa.String(128)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('PREPARED','DISPATCHING','SUCCEEDED','FAILED',"
            "'UNKNOWN','RECONCILING','DENIED')",
            name="ck_mcp_invocations_status",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_mcp_invocations_idempotency"),
    )
    op.create_index("ix_mcp_invocations_goal", "mcp_invocations", ["goal_id", "status"])

    op.create_table(
        "aar1_backfill_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("report_key", sa.String(128), nullable=False, unique=True),
        sa.Column("organizations_count", sa.Integer(), nullable=False),
        sa.Column("versions_created", sa.Integer(), nullable=False),
        sa.Column("versions_skipped", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "details_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    _seed_certified_templates()
    _backfill_organization_versions_v1()


def _seed_certified_templates() -> None:
    conn = op.get_bind()
    now = datetime.now(UTC)
    templates = [
        {
            "name": "single-agent-v1",
            "semantic_version": "1.0.0",
            "topology": {
                "template_id": "single-agent-v1",
                "strategy": "SINGLE_AGENT",
                "roles": [{"role": "executor", "capabilities": [], "max_delegation_depth": 0}],
            },
        },
        {
            "name": "pm-dev-independent-qa-v1",
            "semantic_version": "1.0.0",
            "topology": {
                "template_id": "pm-dev-independent-qa-v1",
                "strategy": "FIXED_TEMPLATE",
                "roles": [
                    {"role": "pm", "capabilities": ["delivery-review-v1"], "max_delegation_depth": 1},
                    {
                        "role": "dev",
                        "capabilities": ["product-surface-v1"],
                        "max_delegation_depth": 1,
                    },
                    {
                        "role": "qa",
                        "capabilities": ["delivery-review-v1"],
                        "max_delegation_depth": 1,
                        "independent_reviewer": True,
                    },
                ],
                "invariants": ["producer_reviewer_separation"],
            },
        },
    ]
    for tmpl in templates:
        content_hash = _canonical_hash(tmpl["topology"])
        existing = conn.execute(
            sa.text(
                "SELECT id FROM organization_templates WHERE name=:n AND semantic_version=:v"
            ),
            {"n": tmpl["name"], "v": tmpl["semantic_version"]},
        ).fetchone()
        if existing:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO organization_templates "
                "(id, name, semantic_version, topology_json, status, content_hash, created_at) "
                "VALUES (:id, :name, :ver, CAST(:topo AS jsonb), 'CERTIFIED', :hash, :now)"
            ),
            {
                "id": str(uuid.uuid4()),
                "name": tmpl["name"],
                "ver": tmpl["semantic_version"],
                "topo": json.dumps(tmpl["topology"], sort_keys=True),
                "hash": content_hash,
                "now": now,
            },
        )


def _backfill_organization_versions_v1() -> None:
    """Idempotent Version 1 backfill with repeatable count/hash report."""
    conn = op.get_bind()
    report_key = "organization_version_v1"
    existing_report = conn.execute(
        sa.text("SELECT content_hash FROM aar1_backfill_reports WHERE report_key=:k"),
        {"k": report_key},
    ).fetchone()

    orgs = conn.execute(
        sa.text(
            "SELECT id, goal_id, strategy, rationale, status, max_agents, current_version_id "
            "FROM organizations ORDER BY id"
        )
    ).fetchall()

    created = 0
    skipped = 0
    version_ids: list[str] = []
    now = datetime.now(UTC)

    for org in orgs:
        org_id, goal_id, strategy, rationale, status, max_agents, current_version_id = org
        if current_version_id is not None:
            skipped += 1
            version_ids.append(str(current_version_id))
            continue
        existing_v = conn.execute(
            sa.text(
                "SELECT id FROM organization_versions "
                "WHERE organization_id=:oid AND version=1"
            ),
            {"oid": str(org_id)},
        ).fetchone()
        if existing_v is not None:
            conn.execute(
                sa.text(
                    "UPDATE organizations SET current_version_id=:vid WHERE id=:oid"
                ),
                {"vid": str(existing_v[0]), "oid": str(org_id)},
            )
            skipped += 1
            version_ids.append(str(existing_v[0]))
            continue

        version_id = uuid.uuid4()
        topology = {
            "legacy_strategy": strategy,
            "rationale": rationale,
            "max_agents": max_agents,
            "status": status,
            "backfill": "organization_version_v1",
            "template_id": (
                "single-agent-v1" if strategy == "SINGLE_AGENT" else "legacy-strategy"
            ),
        }
        conn.execute(
            sa.text(
                "INSERT INTO organization_versions "
                "(id, organization_id, version, predecessor_id, decision_id, topology_json, "
                "status, activated_at, retired_at, created_at) "
                "VALUES (:id, :oid, 1, NULL, NULL, CAST(:topo AS jsonb), "
                "'ACTIVE', :now, NULL, :now)"
            ),
            {
                "id": str(version_id),
                "oid": str(org_id),
                "topo": json.dumps(topology, sort_keys=True, ensure_ascii=False),
                "now": now,
            },
        )
        conn.execute(
            sa.text("UPDATE organizations SET current_version_id=:vid WHERE id=:oid"),
            {"vid": str(version_id), "oid": str(org_id)},
        )
        created += 1
        version_ids.append(str(version_id))

    report_payload = {
        "report_key": report_key,
        "organizations_count": len(orgs),
        "versions_created": created,
        "versions_skipped": skipped,
        "version_ids": sorted(version_ids),
    }
    content_hash = _canonical_hash(report_payload)

    if existing_report is not None:
        # Re-run must produce the same hash for the stable fields (counts + sorted ids).
        if existing_report[0] != content_hash and created == 0:
            # Already fully backfilled; keep prior report.
            return
        conn.execute(
            sa.text("DELETE FROM aar1_backfill_reports WHERE report_key=:k"),
            {"k": report_key},
        )

    conn.execute(
        sa.text(
            "INSERT INTO aar1_backfill_reports "
            "(id, report_key, organizations_count, versions_created, versions_skipped, "
            "content_hash, details_json, created_at) "
            "VALUES (:id, :key, :oc, :vc, :vs, :hash, CAST(:details AS jsonb), :now)"
        ),
        {
            "id": str(uuid.uuid4()),
            "key": report_key,
            "oc": len(orgs),
            "vc": created,
            "vs": skipped,
            "hash": content_hash,
            "details": json.dumps(
                {"version_ids": sorted(version_ids)}, sort_keys=True
            ),
            "now": now,
        },
    )


def downgrade() -> None:
    op.drop_table("aar1_backfill_reports")
    op.drop_index("ix_mcp_invocations_goal", table_name="mcp_invocations")
    op.drop_table("mcp_invocations")
    op.drop_table("mcp_tool_bindings")
    op.drop_table("mcp_servers")
    op.drop_index("ix_envelope_nonces_expires", table_name="envelope_nonces")
    op.drop_table("envelope_nonces")
    op.drop_table("envelope_signing_keys")
    op.drop_index("ix_agent_tasks_goal", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_claimable", table_name="agent_tasks")
    op.drop_table("agent_tasks")
    op.drop_table("agent_relationships")
    op.drop_table("agent_lifecycle_events")
    op.drop_index("ix_agent_deployments_org_version", table_name="agent_deployments")
    op.drop_table("agent_deployments")
    op.drop_table("agent_spec_versions")
    op.drop_table("organization_candidate_checks")
    op.drop_table("organization_candidates")
    op.drop_constraint("fk_organizations_current_version", "organizations", type_="foreignkey")
    op.drop_constraint(
        "fk_organization_decisions_prev_version", "organization_decisions", type_="foreignkey"
    )
    op.drop_index("ix_organization_versions_org_status", table_name="organization_versions")
    op.drop_table("organization_versions")
    op.drop_index("ix_organization_decisions_goal", table_name="organization_decisions")
    op.drop_table("organization_decisions")
    op.drop_table("organization_snapshots")
    op.drop_table("organization_templates")
    op.drop_index("ix_policy_evaluations_decision_point", table_name="policy_evaluations")
    op.drop_index("ix_policy_evaluations_correlation", table_name="policy_evaluations")
    op.drop_table("policy_evaluations")
    op.drop_table("policy_bindings")
    op.drop_table("constitution_versions")
    op.drop_table("constitutions")
    op.drop_column("organizations", "current_version_id")
