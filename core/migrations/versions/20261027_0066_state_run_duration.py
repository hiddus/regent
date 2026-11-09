"""0066：处理记录补 `duration_ms`——「积压多少」看得见，「一章要跑多久」看不见。

没有这个数，影子期只能回答「还剩几章」，回答不了「按这个速度还要多久」，
也发现不了一次从 3 秒退化到 90 秒的抽取调用——那种退化不会报错，只是变慢，
而账本钩子挂在导演链之后，变慢的账本会直接压住下一章的正文生产。

只加列、不回填：历史行没有测过耗时，填 0 会把「没测」伪装成「很快」。
读的时候按 ``duration_ms > 0`` 取样本。

Revision ID: 20261027_0066
Revises: 20261027_0065
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0066"
down_revision: str | None = "20261027_0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "novel_state_runs",
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("novel_state_runs", "duration_ms")
