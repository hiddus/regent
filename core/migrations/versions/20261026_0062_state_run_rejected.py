"""0062：处理记录新增 `rejected` 状态——「抽到的全被拒」不是「这一章没有状态」。

20 章回放里 4 章报 `empty`，其中 3 章各有 5–6 条全被拒。两者混成一个数，
覆盖率就会显得比实际好，而「全被拒」恰恰是最该改抽取的那个信号。

Revision ID: 20261026_0062
Revises: 20261026_0061
"""

from alembic import op

revision: str = "20261026_0062"
down_revision: str | None = "20261026_0061"
branch_labels = None
depends_on = None

_NAME = "ck_novel_state_run_status"
_NEW = "status IN ('ok','empty','rejected','failed','budget','unknown')"
_OLD = "status IN ('ok','empty','failed','budget','unknown')"


def upgrade() -> None:
    op.drop_constraint(_NAME, "novel_state_runs", type_="check")
    op.create_check_constraint(_NAME, "novel_state_runs", _NEW)
    # 已经跑过的章按新语义回填：旧版把「全被拒」记成了 empty，不回填就永远读不出
    # 哪些章其实一条状态都没进账。
    op.execute(
        "UPDATE novel_state_runs SET status = 'rejected' "
        "WHERE status = 'empty' AND rejected > 0"
    )


def downgrade() -> None:
    op.execute("UPDATE novel_state_runs SET status = 'empty' WHERE status = 'rejected'")
    op.drop_constraint(_NAME, "novel_state_runs", type_="check")
    op.create_check_constraint(_NAME, "novel_state_runs", _OLD)
