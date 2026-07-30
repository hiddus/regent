"""P2-3 Impact Graph: cycle detection, cascade revoke, gate blocking, decay."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from regent.application.impact_graph_service import ImpactGraphService
from regent.application.memory_service import AdmitMemory, MemoryService
from regent.domain.errors import DomainError


@pytest.mark.asyncio
async def test_impact_edge_cycle_rejected(db_sessions) -> None:
    mem = MemoryService(db_sessions)
    graph = ImpactGraphService(db_sessions)
    a = await mem.admit(
        AdmitMemory(org_key="org", kind="semantic.rule", content={"k": "a"}, actor="t")
    )
    b = await mem.admit(
        AdmitMemory(org_key="org", kind="semantic.rule", content={"k": "b"}, actor="t")
    )
    await graph.add_edge(org_key="org", from_memory_id=a.id, to_memory_id=b.id)
    with pytest.raises(DomainError, match="cycle"):
        await graph.add_edge(org_key="org", from_memory_id=b.id, to_memory_id=a.id)


@pytest.mark.asyncio
async def test_revoke_cascade_marks_dependents(db_sessions) -> None:
    mem = MemoryService(db_sessions)
    graph = ImpactGraphService(db_sessions)
    root = await mem.admit(
        AdmitMemory(org_key="org", kind="semantic.rule", content={"k": "root"}, actor="t")
    )
    child = await mem.admit(
        AdmitMemory(
            org_key="org",
            kind="semantic.rule",
            content={"k": "child"},
            actor="t",
            source_refs=[str(root.id)],
        )
    )
    touched = await graph.revoke_cascade(root.id, actor="ops", reason="bad-source")
    assert root.id in touched
    assert child.id in touched
    refreshed_child = await mem.list_org("org")
    by_id = {m.id: m for m in refreshed_child}
    # root revoked; child still present but revalidation required
    assert by_id[root.id].status == "REVOKED"
    assert by_id[child.id].content_json.get("_revalidation_required") is True
    assert ImpactGraphService.can_support_gate(by_id[child.id]) is False


@pytest.mark.asyncio
async def test_batch_revoke_by_source_ref(db_sessions) -> None:
    mem = MemoryService(db_sessions)
    graph = ImpactGraphService(db_sessions)
    m = await mem.admit(
        AdmitMemory(
            org_key="org",
            kind="episodic.run_failure",
            content={"k": 1},
            actor="t",
            source_refs=["parser:v1"],
        )
    )
    touched = await graph.batch_revoke(
        "org", actor="ops", reason="parser-bump", source_ref="parser:v1"
    )
    assert m.id in touched


@pytest.mark.asyncio
async def test_confidence_decay() -> None:
    now = datetime.now(UTC)
    created = now - timedelta(days=30)
    decayed = ImpactGraphService.confidence_decay(1.0, created_at=created, now=now)
    assert abs(decayed - 0.5) < 1e-9
