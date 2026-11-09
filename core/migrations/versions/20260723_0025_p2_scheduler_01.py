"""P2-1 scheduler tables

Revision ID: 20260723_0025
Revises: 20260722_0024
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0025"
down_revision: str | None = "20260722_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "goal_priority_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column(
            "params_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("version", name="uq_goal_priority_policies_version"),
    )
    op.create_table(
        "resource_quotas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_key", sa.String(128), nullable=False),
        sa.Column("resource_name", sa.String(64), nullable=False),
        sa.Column("price_book_version", sa.String(64), nullable=False),
        sa.Column("limit_amount", sa.Integer(), nullable=False),
        sa.Column("held_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("limit_amount >= 0", name="ck_resource_quotas_limit"),
        sa.CheckConstraint("held_amount >= 0", name="ck_resource_quotas_held"),
        sa.UniqueConstraint(
            "org_key",
            "resource_name",
            "price_book_version",
            name="uq_resource_quotas_org_resource_book",
        ),
    )
    op.create_index("ix_resource_quotas_org_key", "resource_quotas", ["org_key"])

    op.create_table(
        "execution_queue_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column("org_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("base_priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("aging_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "resource_request",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED','SCHEDULED','COMPLETED','CANCELLED')",
            name="ck_execution_queue_entries_status",
        ),
    )
    op.create_index(
        "ix_execution_queue_entries_status_aging",
        "execution_queue_entries",
        ["status", "aging_score"],
    )
    op.create_index("ix_execution_queue_entries_goal_id", "execution_queue_entries", ["goal_id"])
    op.create_index("ix_execution_queue_entries_org_key", "execution_queue_entries", ["org_key"])

    op.create_table(
        "scheduling_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("price_book_version", sa.String(64), nullable=False),
        sa.Column("queue_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("quota_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("input_snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column("output_json", postgresql.JSONB(), nullable=False),
        sa.Column("random_seed", sa.String(64)),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "resource_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "queue_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_queue_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scheduling_decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scheduling_decisions.id", ondelete="SET NULL"),
        ),
        sa.Column("org_key", sa.String(128), nullable=False),
        sa.Column("resource_name", sa.String(64), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("price_book_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('REQUESTED','HELD','RELEASED','PREEMPTED','EXPIRED','FAILED')",
            name="ck_resource_reservations_status",
        ),
        sa.CheckConstraint("amount > 0", name="ck_resource_reservations_amount"),
    )
    op.create_index("ix_resource_reservations_status", "resource_reservations", ["status"])

    op.create_table(
        "budget_ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_key", sa.String(128), nullable=False),
        sa.Column("price_book_version", sa.String(64), nullable=False),
        sa.Column("entry_type", sa.String(16), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("ref_type", sa.String(64)),
        sa.Column("ref_id", sa.String(64)),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entry_type IN ('DEBIT','CREDIT')", name="ck_budget_ledger_entries_type"
        ),
        sa.CheckConstraint("amount > 0", name="ck_budget_ledger_entries_amount"),
    )
    op.create_index("ix_budget_ledger_entries_org_key", "budget_ledger_entries", ["org_key"])


def downgrade() -> None:
    op.drop_table("budget_ledger_entries")
    op.drop_table("resource_reservations")
    op.drop_table("scheduling_decisions")
    op.drop_table("execution_queue_entries")
    op.drop_table("resource_quotas")
    op.drop_table("goal_priority_policies")
