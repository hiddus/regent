"""P2-3 Impact Graph: memory_impact_edges table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260730_0037_impact_graph"
down_revision = "20260730_0036_budget_cost_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_impact_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_key", sa.String(128), nullable=False),
        sa.Column(
            "from_memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memory_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memory_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("edge_kind", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "edge_kind IN ('DERIVED_FROM','CITES','SUPPORTS')",
            name="ck_memory_impact_edges_kind",
        ),
        sa.CheckConstraint(
            "from_memory_id <> to_memory_id",
            name="ck_memory_impact_edges_no_self",
        ),
        sa.UniqueConstraint(
            "from_memory_id",
            "to_memory_id",
            "edge_kind",
            name="uq_memory_impact_edges_pair_kind",
        ),
    )
    op.create_index("ix_memory_impact_edges_from", "memory_impact_edges", ["from_memory_id"])
    op.create_index("ix_memory_impact_edges_to", "memory_impact_edges", ["to_memory_id"])
    op.create_index("ix_memory_impact_edges_org", "memory_impact_edges", ["org_key"])


def downgrade() -> None:
    op.drop_index("ix_memory_impact_edges_org", table_name="memory_impact_edges")
    op.drop_index("ix_memory_impact_edges_to", table_name="memory_impact_edges")
    op.drop_index("ix_memory_impact_edges_from", table_name="memory_impact_edges")
    op.drop_table("memory_impact_edges")
