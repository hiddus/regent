"""Novel Engine 卷弧结构（30 万字架构 P1）。

Revision ID: 20260904_0049
Revises: 20260903_0048

覆盖：
- 新建 novel_volumes（卷）
- 新建 novel_arc_nodes（弧段）
- novel_works 新增 total_volume_count
- novel_critical_nodes 新增 volume_no / arc_no
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260904_0049"
down_revision: str | None = "20260903_0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- novel_volumes ---
    op.create_table(
        "novel_volumes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "work_id", UUID(as_uuid=True),
            sa.ForeignKey("novel_works.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("volume_no", sa.Integer, nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("summary", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("cultivation_realm", sa.String(200), nullable=False, server_default=""),
        sa.Column("start_chapter_no", sa.Integer, nullable=False, server_default="0"),
        sa.Column("end_chapter_no", sa.Integer, nullable=False, server_default="0"),
        sa.Column("state", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("work_id", "volume_no", name="uq_novel_volumes_work_vol"),
    )
    op.create_index("ix_novel_volumes_work", "novel_volumes", ["work_id"])

    # --- novel_arc_nodes ---
    op.create_table(
        "novel_arc_nodes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "volume_id", UUID(as_uuid=True),
            sa.ForeignKey("novel_volumes.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("arc_no", sa.Integer, nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("arc_type", sa.String(32), nullable=False, server_default="STANDARD"),
        sa.Column("chapter_range_start", sa.Integer, nullable=False, server_default="0"),
        sa.Column("chapter_range_end", sa.Integer, nullable=False, server_default="0"),
        sa.Column("core_conflict", sa.Text, nullable=False, server_default=""),
        sa.Column("resolution_type", sa.String(32), nullable=False, server_default=""),
        sa.UniqueConstraint("volume_id", "arc_no", name="uq_novel_arcs_vol_arc"),
    )
    op.create_index("ix_novel_arcs_volume", "novel_arc_nodes", ["volume_id"])

    # --- novel_works: total_volume_count ---
    op.add_column(
        "novel_works",
        sa.Column("total_volume_count", sa.Integer, nullable=False, server_default="0"),
    )

    # --- novel_critical_nodes: volume_no / arc_no ---
    op.add_column(
        "novel_critical_nodes",
        sa.Column("volume_no", sa.Integer, nullable=True),
    )
    op.add_column(
        "novel_critical_nodes",
        sa.Column("arc_no", sa.Integer, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("novel_critical_nodes", "arc_no")
    op.drop_column("novel_critical_nodes", "volume_no")
    op.drop_column("novel_works", "total_volume_count")
    op.drop_table("novel_arc_nodes")
    op.drop_table("novel_volumes")
