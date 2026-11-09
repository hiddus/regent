"""对账次数与模型 attempt 分离（Plan v6.4 §10 P0-1）。

Revision ID: 20260908_0052
Revises: 20260907_0051

``reconcile()`` 此前用 ``attempt`` 判断对账重试上限，而 attempt 只在新一次
模型调用时递增，因此默认配置下 UNKNOWN 调用会永远停在 PENDING。新增
``reconcile_count`` 单独计数。

回退说明：删列会一并丢弃对账次数历史，回退后仍在 PENDING 的调用需要重新
对账，不保留中间计数。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0052"
down_revision: str | None = "20260907_0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "novel_model_calls",
        sa.Column("reconcile_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.alter_column("novel_model_calls", "reconcile_count", server_default=None)


def downgrade() -> None:
    op.drop_column("novel_model_calls", "reconcile_count")
