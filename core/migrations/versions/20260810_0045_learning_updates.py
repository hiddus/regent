"""Add versioned learning updates and application evidence.

Revision ID: 20260810_0045
Revises: 20260810_0044
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260810_0045"
down_revision: str | None = "20260810_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "learning_updates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_key", sa.String(length=128), nullable=False),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="SET NULL"),
        ),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_key", sa.String(length=255), nullable=False),
        sa.Column("base_version", sa.String(length=128), nullable=False),
        sa.Column("candidate_version", sa.String(length=128), nullable=False),
        sa.Column("before_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("after_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "applicability_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "invalidation_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("ttl_seconds", sa.Integer()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "rollback_update_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_updates.id", ondelete="SET NULL"),
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PROPOSED"),
        sa.Column("first_applied_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('PROPOSED','APPLIED','REVOKED','EXPIRED')",
            name="ck_learning_updates_status",
        ),
        sa.CheckConstraint(
            "ttl_seconds IS NULL OR ttl_seconds > 0",
            name="ck_learning_updates_ttl",
        ),
        sa.UniqueConstraint(
            "org_key", "target_type", "target_key", "candidate_version",
            name="uq_learning_updates_target_version",
        ),
    )
    op.create_index(
        "ix_learning_updates_target",
        "learning_updates",
        ["org_key", "target_type", "target_key"],
    )
    op.create_index("ix_learning_updates_status", "learning_updates", ["status", "created_at"])
    op.create_table(
        "learning_update_applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "learning_update_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_updates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("consumer_type", sa.String(length=64), nullable=False),
        sa.Column("consumer_ref", sa.String(length=255), nullable=False),
        sa.Column("applied_version", sa.String(length=128), nullable=False),
        sa.Column(
            "read_context_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "learning_update_id", "consumer_type", "consumer_ref",
            name="uq_learning_update_application_consumer",
        ),
    )
    op.create_index(
        "ix_learning_update_applications_update",
        "learning_update_applications",
        ["learning_update_id", "applied_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_learning_update_applications_update",
        table_name="learning_update_applications",
    )
    op.drop_table("learning_update_applications")
    op.drop_index("ix_learning_updates_status", table_name="learning_updates")
    op.drop_index("ix_learning_updates_target", table_name="learning_updates")
    op.drop_table("learning_updates")
