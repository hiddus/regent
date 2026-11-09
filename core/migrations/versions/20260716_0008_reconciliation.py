"""side effect attempts and reconciliation

Revision ID: 20260716_0008
Revises: 20260716_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0008"
down_revision: str | None = "20260716_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "side_effect_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "permit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_permits.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("external_request_id", sa.String(255)),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("reconciliation_evidence", postgresql.JSONB()),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("reconciled_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('STARTED','SUCCEEDED','FAILED','UNKNOWN','RECONCILED')",
            name="ck_side_effect_attempts_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("side_effect_attempts")
