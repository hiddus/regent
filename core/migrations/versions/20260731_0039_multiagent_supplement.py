"""Multi-agent supplementation tables (MA-3 Dispatch/Plan durability).

Revision ID: 20260731_0039
Revises: 20260730_0038
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0039"
down_revision: str | None = "20260730_0038_privacy_consent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_plan_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("item_key", sa.String(length=128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("owner_agent_id", sa.String(length=255), nullable=True),
        sa.Column("dependencies", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
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
            "status IN ('pending','in_progress','completed','cancelled','failed')",
            name="ck_execution_plan_items_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_execution_plan_items_version"),
        sa.UniqueConstraint("goal_id", "item_key", name="uq_execution_plan_items_goal_key"),
    )
    op.create_index(
        "ix_execution_plan_items_goal_status",
        "execution_plan_items",
        ["goal_id", "status"],
    )
    op.create_index("ix_execution_plan_items_goal_id", "execution_plan_items", ["goal_id"])
    op.create_index("ix_execution_plan_items_run_id", "execution_plan_items", ["run_id"])

    op.create_table(
        "dispatch_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("step_id", sa.String(length=128), nullable=False),
        sa.Column("organization_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "scheduling_decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scheduling_decisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_agent_id", sa.String(length=255), nullable=True),
        sa.Column("selected_agent_id", sa.String(length=255), nullable=False),
        sa.Column(
            "candidate_agent_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "candidate_weights",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column(
            "capability_scope",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "permit_refs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("output_digest", sa.String(length=64), nullable=False),
        sa.Column("entropy", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_dispatch_decisions_goal_created",
        "dispatch_decisions",
        ["goal_id", "created_at"],
    )
    op.create_index("ix_dispatch_decisions_run", "dispatch_decisions", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_dispatch_decisions_run", table_name="dispatch_decisions")
    op.drop_index("ix_dispatch_decisions_goal_created", table_name="dispatch_decisions")
    op.drop_table("dispatch_decisions")
    op.drop_index("ix_execution_plan_items_run_id", table_name="execution_plan_items")
    op.drop_index("ix_execution_plan_items_goal_id", table_name="execution_plan_items")
    op.drop_index("ix_execution_plan_items_goal_status", table_name="execution_plan_items")
    op.drop_table("execution_plan_items")
