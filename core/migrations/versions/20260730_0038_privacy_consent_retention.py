"""PRD §7.1–7.3: privacy consents + observation anonymized_at."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260730_0038_privacy_consent"
down_revision = "20260730_0037_impact_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "privacy_consents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("notice_version", sa.String(64), nullable=False),
        sa.Column("notice_text", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('GRANTED','WITHDRAWN')",
            name="ck_privacy_consents_status",
        ),
        sa.UniqueConstraint("goal_id", "subject", name="uq_privacy_consents_goal_subject"),
    )
    op.create_index("ix_privacy_consents_goal_id", "privacy_consents", ["goal_id"])
    op.create_index("ix_privacy_consents_status", "privacy_consents", ["status"])

    op.add_column(
        "observations",
        sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("observations", "anonymized_at")
    op.drop_index("ix_privacy_consents_status", table_name="privacy_consents")
    op.drop_index("ix_privacy_consents_goal_id", table_name="privacy_consents")
    op.drop_table("privacy_consents")
