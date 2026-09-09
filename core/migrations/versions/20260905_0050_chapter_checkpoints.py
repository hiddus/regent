"""Novel Engine 章节检查点（人在回路）。

Revision ID: 20260905_0050
Revises: 20260904_0049

覆盖：
- novel_chapter_runs 新增 user_guidance (JSONB)
- novel_chapter_runs 新增 auto_advance (Boolean)
- 更新 CHECK CONSTRAINT 加入 AWAITING_INPUT 状态
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0050"
down_revision: str | None = "20260904_0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- novel_chapter_runs: user_guidance ---
    op.add_column(
        "novel_chapter_runs",
        sa.Column(
            "user_guidance",
            sa.JSON,
            nullable=False,
            server_default="{}",
        ),
    )

    # --- novel_chapter_runs: auto_advance ---
    op.add_column(
        "novel_chapter_runs",
        sa.Column(
            "auto_advance",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # --- 更新 CHECK CONSTRAINT 加入 AWAITING_INPUT ---
    op.drop_constraint("ck_novel_runs_state", "novel_chapter_runs")
    op.create_check_constraint(
        "ck_novel_runs_state",
        "novel_chapter_runs",
        "state IN ("
        "'QUEUED','RUNNING','PENDING_DECISION','AWAITING_INPUT',"
        "'RETRYABLE_FAILED','TERMINAL_FAILED','CANONIZED',"
        "'SUPERSEDED','CANCELLED'"
        ")",
    )


def downgrade() -> None:
    # 回退 CHECK CONSTRAINT
    op.drop_constraint("ck_novel_runs_state", "novel_chapter_runs")
    op.create_check_constraint(
        "ck_novel_runs_state",
        "novel_chapter_runs",
        "state IN ("
        "'QUEUED','RUNNING','PENDING_DECISION',"
        "'RETRYABLE_FAILED','TERMINAL_FAILED','CANONIZED',"
        "'SUPERSEDED','CANCELLED'"
        ")",
    )
    op.drop_column("novel_chapter_runs", "auto_advance")
    op.drop_column("novel_chapter_runs", "user_guidance")
