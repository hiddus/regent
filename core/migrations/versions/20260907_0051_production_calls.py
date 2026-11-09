"""Novel Engine 生产调用协议（R0：预算预留、租约、调用恢复）。

Revision ID: 20260907_0051
Revises: 20260905_0050

覆盖：
- novel_model_calls：attempt / 预留与实际金额 / 供应商请求 id / usage 来源 /
  缓存 token / 调用租约 / 输出快照 / 错误码；唯一键改为 (logical_call_id, attempt)
- novel_chapter_runs：运行租约（lease_owner / lease_expires_at / fencing_token）
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_0051"
down_revision: str | None = "20260905_0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- novel_model_calls: 唯一键允许同 logical_call 的多次 attempt ---
    op.drop_constraint("uq_novel_model_calls_logical", "novel_model_calls")
    op.add_column(
        "novel_model_calls",
        sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_unique_constraint(
        "uq_novel_model_calls_logical_attempt",
        "novel_model_calls",
        ["logical_call_id", "attempt"],
    )

    for name, column in (
        ("reserved_amount_minor", sa.BigInteger),
        ("actual_amount_minor", sa.BigInteger),
        ("cached_input_tokens", sa.Integer),
        ("lease_ttl_seconds", sa.Integer),
    ):
        op.add_column(
            "novel_model_calls",
            sa.Column(name, column, nullable=True),
        )
    op.add_column(
        "novel_model_calls",
        sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"),
    )
    op.add_column(
        "novel_model_calls",
        sa.Column("provider_request_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "novel_model_calls",
        sa.Column("usage_source", sa.String(32), nullable=False, server_default="provider"),
    )
    op.add_column("novel_model_calls", sa.Column("lease_owner", sa.String(64), nullable=True))
    op.add_column(
        "novel_model_calls", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("novel_model_calls", sa.Column("output_json", sa.JSON, nullable=True))
    op.add_column("novel_model_calls", sa.Column("error_code", sa.String(64), nullable=True))
    op.add_column(
        "novel_model_calls",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_novel_model_calls_status", "novel_model_calls", ["status"])
    op.create_index("ix_novel_model_calls_logical", "novel_model_calls", ["logical_call_id"])

    # --- novel_cost_entries: 结算幂等键加入 entry_kind（两段式需区分 CONSUME/RELEASE）---
    op.drop_constraint(
        "uq_novel_cost_settlement", "novel_cost_entries", type_="unique"
    )
    op.create_unique_constraint(
        "uq_novel_cost_settlement",
        "novel_cost_entries",
        ["logical_call_id", "funding_pool", "entry_kind"],
    )

    # --- novel_chapter_runs: 运行租约与 fencing token ---
    op.add_column("novel_chapter_runs", sa.Column("lease_owner", sa.String(64), nullable=True))
    op.add_column(
        "novel_chapter_runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "novel_chapter_runs",
        sa.Column("fencing_token", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_novel_runs_lease", "novel_chapter_runs", ["lease_expires_at"])


def downgrade() -> None:
    op.drop_constraint(
        "uq_novel_cost_settlement", "novel_cost_entries", type_="unique"
    )
    op.create_unique_constraint(
        "uq_novel_cost_settlement", "novel_cost_entries", ["logical_call_id", "funding_pool"]
    )

    op.drop_index("ix_novel_runs_lease", table_name="novel_chapter_runs")
    op.drop_column("novel_chapter_runs", "fencing_token")
    op.drop_column("novel_chapter_runs", "lease_expires_at")
    op.drop_column("novel_chapter_runs", "lease_owner")

    op.drop_index("ix_novel_model_calls_logical", table_name="novel_model_calls")
    op.drop_index("ix_novel_model_calls_status", table_name="novel_model_calls")
    for column in (
        "updated_at",
        "error_code",
        "output_json",
        "lease_expires_at",
        "lease_owner",
        "usage_source",
        "provider_request_id",
        "currency",
        "lease_ttl_seconds",
        "cached_input_tokens",
        "actual_amount_minor",
        "reserved_amount_minor",
    ):
        op.drop_column("novel_model_calls", column)
    op.drop_constraint("uq_novel_model_calls_logical_attempt", "novel_model_calls")
    op.drop_column("novel_model_calls", "attempt")
    op.create_unique_constraint(
        "uq_novel_model_calls_logical", "novel_model_calls", ["logical_call_id"]
    )
