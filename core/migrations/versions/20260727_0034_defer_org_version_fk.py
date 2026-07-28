"""Defer organizations.current_version_id FK for circular org↔version inserts.

Revision ID: 20260727_0034
Revises: 20260727_0033

Contract organize inserts Organization with a pre-allocated current_version_id
before OrganizationVersion exists. PostgreSQL requires this FK to be DEFERRABLE.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260727_0034"
down_revision: str | None = "20260727_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE organizations DROP CONSTRAINT fk_organizations_current_version")
    op.execute(
        """
        ALTER TABLE organizations
        ADD CONSTRAINT fk_organizations_current_version
        FOREIGN KEY (current_version_id)
        REFERENCES organization_versions(id)
        ON DELETE SET NULL
        DEFERRABLE INITIALLY DEFERRED
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE organizations DROP CONSTRAINT fk_organizations_current_version")
    op.execute(
        """
        ALTER TABLE organizations
        ADD CONSTRAINT fk_organizations_current_version
        FOREIGN KEY (current_version_id)
        REFERENCES organization_versions(id)
        ON DELETE SET NULL
        NOT DEFERRABLE
        """
    )
