"""Add agent_transcripts table for agentic-generation-v1.

Revision ID: 20260727_0030
Revises: 20260725_0029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_0030"
down_revision: str | None = "20260725_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_transcripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "generation_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_name", sa.String(length=128), nullable=True),
        sa.Column("tool_call_id", sa.String(length=128), nullable=True),
        sa.Column("tool_arguments", postgresql.JSONB(), nullable=True),
        sa.Column("tool_result", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "generation_run_id",
            "turn",
            "seq",
            name="uq_agent_transcripts_run_turn_seq",
        ),
    )
    op.create_index(
        "ix_agent_transcripts_generation_run_id",
        "agent_transcripts",
        ["generation_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_transcripts_generation_run_id", table_name="agent_transcripts")
    op.drop_table("agent_transcripts")
