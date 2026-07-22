"""add generated app preview releases

Revision ID: 20260718_0020
Revises: 20260718_0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0020"
down_revision: str | None = "20260718_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_preview_releases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "app_project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source_hash", sa.String(64)),
        sa.Column("manifest_json", postgresql.JSONB(), nullable=False),
        sa.Column("workspace_locator", sa.String(1024)),
        sa.Column("preview_endpoint", sa.String(1024)),
        sa.Column("verification_checks", postgresql.JSONB(), nullable=False),
        sa.Column("model_ref", sa.String(255)),
        sa.Column("failure_code", sa.String(128)),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('GENERATING','PREVIEW_READY','FAILED')",
            name="ck_app_preview_releases_status",
        ),
    )

    op.alter_column("metric_definition_bindings", "deployment_id", nullable=True)
    op.add_column(
        "metric_definition_bindings",
        sa.Column("preview_release_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_metric_bindings_preview_release",
        "metric_definition_bindings",
        "app_preview_releases",
        ["preview_release_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_metric_bindings_one_target",
        "metric_definition_bindings",
        "(deployment_id IS NOT NULL) <> (preview_release_id IS NOT NULL)",
    )
    op.alter_column("gate_evaluations", "deployment_id", nullable=True)
    op.add_column(
        "gate_evaluations",
        sa.Column("preview_release_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_gate_evaluations_preview_release",
        "gate_evaluations",
        "app_preview_releases",
        ["preview_release_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_gate_evaluations_one_target",
        "gate_evaluations",
        "(deployment_id IS NOT NULL) <> (preview_release_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_gate_evaluations_one_target", "gate_evaluations", type_="check")
    op.drop_constraint(
        "fk_gate_evaluations_preview_release", "gate_evaluations", type_="foreignkey"
    )
    op.drop_column("gate_evaluations", "preview_release_id")
    op.alter_column("gate_evaluations", "deployment_id", nullable=False)
    op.drop_constraint("ck_metric_bindings_one_target", "metric_definition_bindings", type_="check")
    op.drop_constraint(
        "fk_metric_bindings_preview_release",
        "metric_definition_bindings",
        type_="foreignkey",
    )
    op.drop_column("metric_definition_bindings", "preview_release_id")
    op.alter_column("metric_definition_bindings", "deployment_id", nullable=False)
    op.drop_table("app_preview_releases")
