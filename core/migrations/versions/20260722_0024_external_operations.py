"""add external_operations for G0 durable effects

Revision ID: 20260722_0024
Revises: 20260721_0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0024"
down_revision: str | None = "20260721_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operation_key", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column(
            "permit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("execution_permits.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("local_fencing_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dispatch_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("worker_lease_token", sa.String(255)),
        sa.Column("external_id", sa.String(512)),
        sa.Column(
            "result_summary",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("reconciled_at", sa.DateTime(timezone=True)),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="SET NULL"),
        ),
        sa.Column("correlation_id", sa.String(255)),
        sa.Column("causation_id", sa.String(255)),
        sa.Column("failure_code", sa.String(128)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PREPARED','DISPATCHING','SUCCEEDED','FAILED_TERMINAL',"
            "'UNKNOWN','RECONCILING','MANUAL_REVIEW')",
            name="ck_external_operations_status",
        ),
        sa.CheckConstraint(
            "dispatch_generation >= 0", name="ck_external_operations_generation"
        ),
        sa.UniqueConstraint("operation_key", name="uq_external_operations_operation_key"),
        sa.UniqueConstraint("permit_id", name="uq_external_operations_permit_id"),
    )
    op.create_index("ix_external_operations_status", "external_operations", ["status"])
    op.create_index("ix_external_operations_provider", "external_operations", ["provider"])


def downgrade() -> None:
    op.drop_index("ix_external_operations_provider", table_name="external_operations")
    op.drop_index("ix_external_operations_status", table_name="external_operations")
    op.drop_table("external_operations")
