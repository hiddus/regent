"""Add budget_entries table for real-time cost tracking.

Revision ID: 20260727_0035
Revises: 20260727_0034

Tracks per-Goal/Run model token costs, tool invocations, and
infrastructure spend.  Supports budget-limit enforcement.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260727_0035"
down_revision: str | None = "20260727_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "budget_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("cost_type", sa.String(64), nullable=False),
        sa.Column("amount", sa.Float, nullable=False, server_default="0"),
        sa.Column(
            "price_book_version", sa.String(64), nullable=False, server_default="price-book-v1"
        ),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cost_type IN ('model_input_tokens','model_output_tokens',"
            "'tool_invocation','infrastructure','external_operation')",
            name="ck_budget_entries_cost_type",
        ),
        sa.CheckConstraint("amount >= 0", name="ck_budget_entries_amount_non_negative"),
    )


def downgrade() -> None:
    op.drop_table("budget_entries")
