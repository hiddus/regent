"""Novel Engine 盲评评估（Plan §4 R4）。

Revision ID: 20260908_0054
Revises: 20260908_0053

冻结的样本/rubric/预算带/停止条件/晋级阈值、盲评样本顺序、人评打分与裁决结论
落在同一行：配置指纹与报告指纹不一致时不得宣布晋级。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260908_0054"
down_revision: str | None = "20260908_0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "novel_eval_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("eval_id", sa.String(120), nullable=False),
        sa.Column("config", JSONB, nullable=False, server_default="{}"),
        sa.Column("config_fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("samples", JSONB, nullable=False, server_default="[]"),
        sa.Column("scores", JSONB, nullable=False, server_default="[]"),
        sa.Column("report", JSONB, nullable=False, server_default="{}"),
        sa.Column("report_fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("verdict", sa.String(16), nullable=False, server_default="HOLD"),
        sa.Column("verdict_reasons", JSONB, nullable=False, server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("eval_id", name="uq_novel_eval_id"),
    )


def downgrade() -> None:
    op.drop_table("novel_eval_runs")
