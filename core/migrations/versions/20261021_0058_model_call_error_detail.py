"""调用账本保留原始故障现场：error_detail。

error_code 会被终态覆盖（对账耗尽改成 RECONCILE_EXHAUSTED），覆盖之后只剩
「钱按已发生结算了」，查不出这次调用到底是超时、超长还是解析失败。原始码与
异常消息落到 error_detail，终态覆盖时并入而不丢弃。

Revision ID: 20261021_0058
Revises: 20261020_0057
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_0058"
down_revision: str | None = "20261020_0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "novel_model_calls",
        sa.Column("error_detail", sa.String(255), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("novel_model_calls", "error_detail")
