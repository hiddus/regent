"""0064：变更流水补 `downgrade`——「名字在引文里、方向判不了」要能事后逐条复核。

原来的机械锚点是「持有人名字逐字出现在引文里」，真机数据里立刻出现反例：
「铜刺扎入赵无极左臂上端」——赵无极确实在引文里，但他是被刺的人，不是拿着铜刺的人。
现在这类行降级为候选（留证、不投影），降级原因写进这一列。

单独一列而不是塞进 evidence：evidence 存的是「值被哪段原文支持」，
降级原因存的是「为什么不能据此投影」，两种用途混在一列里，以后两边都查不清。

Revision ID: 20261027_0064
Revises: 20261027_0063
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0064"
down_revision: str | None = "20261027_0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "novel_state_changes",
        sa.Column("downgrade", sa.String(32), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("novel_state_changes", "downgrade")
