"""Add Outbox dispatch leases and Worker leases.

Revision ID: 20260716_0002
Revises: 20260716_0001
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0002"
down_revision: str | None = "20260716_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("outbox_events", sa.Column("lease_owner", sa.String(length=255), nullable=True))
    op.add_column(
        "outbox_events",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("outbox_events", sa.Column("last_error", sa.Text(), nullable=True))
    op.create_index(
        "ix_outbox_claimable",
        "outbox_events",
        ["status", "available_at", "lease_expires_at"],
    )

    op.create_table(
        "worker_leases",
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("worker_id"),
        sa.UniqueConstraint("lease_token", name="uq_worker_leases_token"),
    )
    op.create_index("ix_worker_leases_expires_at", "worker_leases", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_worker_leases_expires_at", table_name="worker_leases")
    op.drop_table("worker_leases")
    op.drop_index("ix_outbox_claimable", table_name="outbox_events")
    op.drop_column("outbox_events", "last_error")
    op.drop_column("outbox_events", "lease_expires_at")
    op.drop_column("outbox_events", "lease_owner")
