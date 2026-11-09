"""0061：状态账本处理记录补一列拒收原因分布。

只记「拒了几条」看不出该改哪儿：`quote-not-in-prose` 多说明抽取在编，
`value-not-in-quote` 多说明它把「读到的事实」冒充成「这句说的值」，
`holder-not-in-cast` 多说明它从正文里捡了没登记的人。三种原因要改的地方不同。

Revision ID: 20261026_0061
Revises: 20261024_0060
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261026_0061"
down_revision: str | None = "20261024_0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "novel_state_runs",
        sa.Column("reasons", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("novel_state_runs", "reasons")
