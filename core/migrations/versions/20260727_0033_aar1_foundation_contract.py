"""AAR-1 Foundation Contract: stop legacy mutable org writes; tighten Version pointer.

Revision ID: 20260727_0033
Revises: 20260727_0032

Independent of M1 Expand. Rollback restores nullable current_version_id.
Does not drop legacy strategy/rationale columns (historical rows remain readable).
No FK to organization_versions (circular with organization_id); integrity is
enforced by application Contract writer + NOT NULL.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_0033"
down_revision: str | None = "20260727_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _canonical_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def upgrade() -> None:
    conn = op.get_bind()
    orgs = conn.execute(
        sa.text(
            "SELECT id, goal_id, strategy, rationale "
            "FROM organizations WHERE current_version_id IS NULL"
        )
    ).fetchall()
    now = datetime.now(UTC)
    for org_id, _goal_id, strategy, rationale in orgs:
        version_id = uuid.uuid4()
        topology = {
            "template_id": (
                "single-agent-v1"
                if strategy in {"SINGLE_AGENT", "single-agent-v1"}
                else strategy
            ),
            "strategy": strategy,
            "roles": [{"role": "executor", "capabilities": []}],
            "rationale": rationale,
            "contract_backfill": True,
        }
        conn.execute(
            sa.text(
                """
                INSERT INTO organization_versions (
                    id, organization_id, version, predecessor_id, decision_id,
                    topology_json, status, activated_at, retired_at, created_at
                ) VALUES (
                    :id, :organization_id, 1, NULL, NULL,
                    CAST(:topology_json AS jsonb), 'ACTIVE', :activated_at, NULL, :created_at
                )
                """
            ),
            {
                "id": version_id,
                "organization_id": org_id,
                "topology_json": json.dumps(topology, ensure_ascii=False),
                "activated_at": now,
                "created_at": now,
            },
        )
        conn.execute(
            sa.text(
                "UPDATE organizations SET current_version_id = :vid WHERE id = :oid"
            ),
            {"vid": version_id, "oid": org_id},
        )

    remaining = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM organizations WHERE current_version_id IS NULL"
        )
    ).scalar_one()
    if remaining != 0:
        raise RuntimeError(
            f"aar1-m5-contract backfill incomplete: {remaining} orgs without version"
        )

    ids = [
        str(r[0])
        for r in conn.execute(
            sa.text("SELECT id FROM organizations ORDER BY id")
        ).fetchall()
    ]
    # Repeatable verification payload for ops (count/hash).
    _report = {
        "revision": revision,
        "organization_count": len(ids),
        "content_hash": _canonical_hash({"organization_ids": ids}),
    }
    del _report

    op.alter_column(
        "organizations",
        "current_version_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "organizations",
        "current_version_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
