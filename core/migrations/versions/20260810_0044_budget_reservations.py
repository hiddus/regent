"""Add hard Goal/Run budget reservations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260810_0044"
down_revision = "20260802_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_accounts",
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("spent_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reserved_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "budget_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("reservation_key", sa.String(255), nullable=False),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("cost_type", sa.String(64), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("settled_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("price_book_version", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('RESERVED','CLAIMED','SETTLED','RELEASED')",
            name="ck_budget_reservations_status",
        ),
        sa.CheckConstraint("amount >= 0", name="ck_budget_reservations_amount_non_negative"),
        sa.CheckConstraint(
            "settled_amount >= 0", name="ck_budget_reservations_settled_non_negative"
        ),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("reservation_key", name="uq_budget_reservations_key"),
    )
    op.create_index(
        "ix_budget_reservations_goal_status", "budget_reservations", ["goal_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_budget_reservations_goal_status", table_name="budget_reservations")
    op.drop_table("budget_reservations")
    op.drop_table("budget_accounts")
