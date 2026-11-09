"""0063：处理记录补 `retry_after`——失败与额度不足要能退避重试。

原来的重试规则只有一条「试两次就停」，于是两种完全不同的情况被同一条规则处理：
内容真的读不懂（重试无用）与额度不足（额度恢复后该继续）。前者该停，后者该等。

用 epoch 秒而不是 timestamptz：这条判据要在 sqlite 单测与 Postgres 上算出同一个结果。

Revision ID: 20261027_0063
Revises: 20261026_0062
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0063"
down_revision: str | None = "20261026_0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "novel_state_runs",
        sa.Column("retry_after", sa.BigInteger(), nullable=False, server_default="0"),
    )
    # 已经停在 failed/budget 上的记录：给一个立刻可重试的时间，否则它们要等到
    # 下一次改稿才会被重新捡起来。
    op.execute(
        "UPDATE novel_state_runs SET retry_after = 0 "
        "WHERE status IN ('failed','budget','unknown')"
    )


def downgrade() -> None:
    op.drop_column("novel_state_runs", "retry_after")
