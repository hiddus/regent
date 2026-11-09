"""add durable goal execution and outbox dead letters

Revision ID: 20260720_0022
Revises: 20260718_0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0022"
down_revision: str | None = "20260718_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_outbox_events_status", "outbox_events", type_="check")
    op.create_check_constraint(
        "ck_outbox_events_status",
        "outbox_events",
        "status IN ('PENDING','DISPATCHING','DISPATCHED','FAILED','DEAD_LETTER')",
    )
    op.add_column("app_preview_releases", sa.Column("failure_summary", sa.Text()))


def downgrade() -> None:
    op.execute("UPDATE outbox_events SET status = 'FAILED' WHERE status = 'DEAD_LETTER'")
    op.drop_column("app_preview_releases", "failure_summary")
    op.drop_constraint("ck_outbox_events_status", "outbox_events", type_="check")
    op.create_check_constraint(
        "ck_outbox_events_status",
        "outbox_events",
        "status IN ('PENDING','DISPATCHING','DISPATCHED','FAILED')",
    )
