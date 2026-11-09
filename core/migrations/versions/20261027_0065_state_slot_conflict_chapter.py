"""0065：当前态补 `conflict_chapter`——「值还在，但它已经不代表现状」要能看出来。

隔离冲突时原来的行为是：冲突行不投影，于是当前态保留**上一章**的旧值，
读起来跟「上一章之后没变过」一模一样。这两种情况必须能分开：
一种是「确实没变」，一种是「这一章出现了两个都站得住的值，我们不知道是哪个」。

不去清空 value 而加一列，是因为清空会丢掉可审计的历史；读的人拿
``conflict_chapter > as_of_chapter`` 就知道这个值不能再当硬状态用。

Revision ID: 20261027_0065
Revises: 20261027_0064
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0065"
down_revision: str | None = "20261027_0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "novel_state_slots",
        sa.Column("conflict_chapter", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("novel_state_slots", "conflict_chapter")
