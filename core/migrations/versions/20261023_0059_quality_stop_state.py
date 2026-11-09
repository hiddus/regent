"""作品状态加 STOPPED_QUALITY：读者不追时的终止状态。

与 DONE（故事讲完）分开，是为了让「写完了几本 / 写废了几本」在报表里能分开数，
也让恢复、重试、扩卷有一条共同的、绕不过去的状态边界。

Revision ID: 20261023_0059
Revises: 20261021_0058
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261023_0059"
down_revision: str | None = "20261021_0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = (
    "'ONBOARDING','READY','RUNNING','PENDING_DECISION','PAUSED_QUOTA',"
    "'PAUSED_COST','RECOMPUTING','FAILED','DONE','CANCELLED','ARCHIVED'"
)
_NEW = (
    "'ONBOARDING','READY','RUNNING','PENDING_DECISION','PAUSED_QUOTA',"
    "'PAUSED_COST','RECOMPUTING','FAILED','DONE','STOPPED_QUALITY',"
    "'CANCELLED','ARCHIVED'"
)
_CONSTRAINT = "ck_novel_works_state"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "novel_works", type_="check")
    op.create_check_constraint(_CONSTRAINT, "novel_works", f"state IN ({_NEW})")


def downgrade() -> None:
    # 回退前必须清掉存量：否则新状态的行会让旧约束建不上。
    op.execute("UPDATE novel_works SET state = 'DONE' WHERE state = 'STOPPED_QUALITY'")
    op.drop_constraint(_CONSTRAINT, "novel_works", type_="check")
    op.create_check_constraint(_CONSTRAINT, "novel_works", f"state IN ({_OLD})")
