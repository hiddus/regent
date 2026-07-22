import uuid

import pytest
from regent.application.p1_ports import EvidenceSourceRequest
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.evidence_sources import GoalIntentEvidenceConnector


@pytest.mark.asyncio
async def test_goal_intent_evidence_is_persisted_and_hashed(tmp_path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    connector = GoalIntentEvidenceConnector(store)
    request = EvidenceSourceRequest(
        query="Build a hello world timestamp page",
        correlation_id=str(uuid.uuid4()),
    )
    snapshots = await connector.fetch(request)
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.metadata["kind"] == "goal-intent"
    assert snap.content_hash
    assert snap.content_artifact_uri.startswith("file:")
    again = await connector.fetch(request)
    assert again[0].content_hash == snap.content_hash


@pytest.mark.asyncio
async def test_goal_intent_evidence_skips_empty_query(tmp_path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    connector = GoalIntentEvidenceConnector(store)
    snapshots = await connector.fetch(
        EvidenceSourceRequest(query="   ", correlation_id=str(uuid.uuid4()))
    )
    assert snapshots == []
