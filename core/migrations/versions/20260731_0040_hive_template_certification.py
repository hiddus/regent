"""Backfill whole-template certification digest for the fixed Hive.

Revision ID: 20260731_0040
Revises: 20260731_0039
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_0040"
down_revision: str | None = "20260731_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from regent.application.member_contract import (
        compute_template_certification,
        enrich_topology_with_member_contracts,
    )
    from regent.application.p1_contracts import canonical_hash

    conn = op.get_bind()
    row = conn.execute(
        sa.text(
            "SELECT topology_json, semantic_version FROM organization_templates "
            "WHERE name='pm-dev-independent-qa-v1' AND status='CERTIFIED'"
        )
    ).mappings().first()
    if row is None:
        return
    topology = enrich_topology_with_member_contracts(dict(row["topology_json"]))
    certification = compute_template_certification(
        template_id="pm-dev-independent-qa-v1",
        semantic_version=str(row["semantic_version"]),
        topology=topology,
    )
    topology["template_certification"] = certification.as_dict()
    conn.execute(
        sa.text(
            "UPDATE organization_templates SET topology_json=CAST(:topology AS jsonb), "
            "content_hash=:content_hash WHERE name='pm-dev-independent-qa-v1' "
            "AND semantic_version=:semantic_version"
        ),
        {
            "topology": __import__("json").dumps(topology, sort_keys=True),
            "content_hash": canonical_hash(topology),
            "semantic_version": str(row["semantic_version"]),
        },
    )


def downgrade() -> None:
    import json

    from regent.application.p1_contracts import canonical_hash

    conn = op.get_bind()
    row = conn.execute(
        sa.text(
            "SELECT topology_json, semantic_version FROM organization_templates "
            "WHERE name='pm-dev-independent-qa-v1'"
        )
    ).mappings().first()
    if row is None:
        return
    topology = dict(row["topology_json"])
    topology.pop("template_certification", None)
    topology.pop("member_contracts_schema", None)
    topology.pop("member_manifest_hash", None)
    for role in topology.get("roles") or []:
        role.pop("member_contract", None)
        role.pop("member_contract_hash", None)
        role.pop("clarification_required_on_uncertainty", None)
    conn.execute(
        sa.text(
            "UPDATE organization_templates SET topology_json=CAST(:topology AS jsonb), "
            "content_hash=:content_hash WHERE name='pm-dev-independent-qa-v1' "
            "AND semantic_version=:semantic_version"
        ),
        {
            "topology": json.dumps(topology, sort_keys=True),
            "content_hash": canonical_hash(topology),
            "semantic_version": str(row["semantic_version"]),
        },
    )