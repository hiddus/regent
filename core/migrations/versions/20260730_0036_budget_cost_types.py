"""Expand budget_entries cost_type for PRD §8.1 human + failure-recovery costs."""

from __future__ import annotations

from alembic import op

revision = "20260730_0036_budget_cost_types"
down_revision = "20260727_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_budget_entries_cost_type", "budget_entries", type_="check")
    op.create_check_constraint(
        "ck_budget_entries_cost_type",
        "budget_entries",
        "cost_type IN ('model_input_tokens','model_output_tokens',"
        "'tool_invocation','infrastructure','external_operation',"
        "'human_minutes','failure_recovery')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_budget_entries_cost_type", "budget_entries", type_="check")
    op.create_check_constraint(
        "ck_budget_entries_cost_type",
        "budget_entries",
        "cost_type IN ('model_input_tokens','model_output_tokens',"
        "'tool_invocation','infrastructure','external_operation')",
    )
