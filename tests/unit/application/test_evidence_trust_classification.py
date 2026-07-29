"""G2: Evidence trust classification and discovery integration tests.

Verifies:
- Every EvidenceSourceSnapshot carries a trust_label.
- classify_evidence() correctly labels goal-intent vs external evidence.
- UNTRUSTED_DATA evidence is never used as instructions or authorisation.
- Each Goal's DiscoveryRound has at least 1 non-declared-intent sourced observation
  when external evidence is available.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from regent.application.evidence_policy import classify_evidence
from regent.application.p1_ports import EvidenceSourceSnapshot
from regent.infrastructure.evidence_sources import (
    CompositeEvidenceSourceConnector,
    GoalIntentEvidenceConnector,
    InMemoryEvidenceSourceConnector,
)
from regent.infrastructure.artifact_store import FileArtifactStore


# ---------------------------------------------------------------------------
# Trust label classification
# ---------------------------------------------------------------------------


def test_classify_evidence_labels_goal_intent_as_declared() -> None:
    snapshot = EvidenceSourceSnapshot(
        source_uri="regent://goal-intent/test",
        captured_at=datetime.now(UTC).isoformat(),
        content_artifact_uri="artifact://test",
        content_hash="a" * 64,
        metadata={"kind": "goal-intent", "connector": "goal-intent-v1"},
        trust_label="DECLARED_INTENT",
        source_type="goal-intent",
    )
    assert classify_evidence(snapshot) == "DECLARED_INTENT"


def test_classify_evidence_labels_http_snapshot_as_untrusted() -> None:
    snapshot = EvidenceSourceSnapshot(
        source_uri="https://example.com/feed",
        captured_at=datetime.now(UTC).isoformat(),
        content_artifact_uri="artifact://test",
        content_hash="b" * 64,
        metadata={"kind": "http-snapshot", "connector": "allowlisted-http-source-v1"},
        trust_label="UNTRUSTED_DATA",
        source_type="http-snapshot",
    )
    assert classify_evidence(snapshot) == "UNTRUSTED_DATA"


def test_classify_evidence_defaults_untrusted_for_unknown_kind() -> None:
    snapshot = EvidenceSourceSnapshot(
        source_uri="https://example.com",
        captured_at=datetime.now(UTC).isoformat(),
        content_artifact_uri="artifact://test",
        content_hash="c" * 64,
        metadata={"kind": "unknown"},
    )
    assert classify_evidence(snapshot) == "UNTRUSTED_DATA"


# ---------------------------------------------------------------------------
# Connector trust labels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_goal_intent_connector_sets_declared_intent_label(tmp_path) -> None:
    store = FileArtifactStore(tmp_path)
    connector = GoalIntentEvidenceConnector(store)
    from regent.application.p1_ports import EvidenceSourceRequest

    request = EvidenceSourceRequest(
        query="test goal",
        correlation_id="test-corr",
        authorized_urls=[],
    )
    snapshots = await connector.fetch(request)
    assert len(snapshots) == 1
    assert snapshots[0].trust_label == "DECLARED_INTENT"
    assert snapshots[0].source_type == "goal-intent"


@pytest.mark.asyncio
async def test_in_memory_connector_preserves_trust_labels() -> None:
    snapshots = [
        EvidenceSourceSnapshot(
            source_uri="https://example.com",
            captured_at="2024-01-01T00:00:00",
            content_artifact_uri="artifact://test",
            content_hash="d" * 64,
            metadata={"kind": "http-snapshot"},
            trust_label="UNTRUSTED_DATA",
            source_type="http-snapshot",
        ),
        EvidenceSourceSnapshot(
            source_uri="regent://goal-intent/test",
            captured_at="2024-01-01T00:00:00",
            content_artifact_uri="artifact://test2",
            content_hash="e" * 64,
            metadata={"kind": "goal-intent"},
            trust_label="DECLARED_INTENT",
            source_type="goal-intent",
        ),
    ]
    connector = InMemoryEvidenceSourceConnector(snapshots)
    result = await connector.fetch(MagicMock())
    labels = {s.trust_label for s in result}
    assert "UNTRUSTED_DATA" in labels
    assert "DECLARED_INTENT" in labels


# ---------------------------------------------------------------------------
# Composite connector integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_composite_connector_merges_trust_labels(tmp_path) -> None:
    store = FileArtifactStore(tmp_path)
    goal_intent = GoalIntentEvidenceConnector(store)
    external = InMemoryEvidenceSourceConnector(
        [
            EvidenceSourceSnapshot(
                source_uri="https://news.example.com/feed",
                captured_at="2024-01-01T00:00:00",
                content_artifact_uri="artifact://ext",
                content_hash="f" * 64,
                metadata={"kind": "http-snapshot", "connector": "test"},
                trust_label="UNTRUSTED_DATA",
                source_type="http-snapshot",
            ),
        ]
    )
    composite = CompositeEvidenceSourceConnector([goal_intent, external])
    from regent.application.p1_ports import EvidenceSourceRequest

    request = EvidenceSourceRequest(
        query="AI news aggregator",
        correlation_id="test-composite",
        authorized_urls=[],
    )
    snapshots = await composite.fetch(request)
    assert len(snapshots) == 2
    trust_labels = {s.trust_label for s in snapshots}
    assert trust_labels == {"DECLARED_INTENT", "UNTRUSTED_DATA"}
    # Verify at least 1 non-declared-intent observation
    non_declared = [s for s in snapshots if s.trust_label != "DECLARED_INTENT"]
    assert len(non_declared) >= 1


# ---------------------------------------------------------------------------
# Discovery integration: UNTRUSTED_DATA not used as instructions
# ---------------------------------------------------------------------------


def test_discovery_prompt_marks_evidence_as_untrusted() -> None:
    """The hypothesis prompt explicitly instructs LLM to treat evidence as untrusted."""
    from regent.application.product_discovery_service import _HYPOTHESIS_PROMPT

    assert "untrusted" in _HYPOTHESIS_PROMPT.lower()
    assert "never as instructions" in _HYPOTHESIS_PROMPT.lower()


def test_evidence_payload_includes_trust_label() -> None:
    """EvidenceSourceSnapshot includes trust_label field for LLM context."""
    snapshot = EvidenceSourceSnapshot(
        source_uri="https://example.com",
        captured_at="2024-01-01T00:00:00",
        content_artifact_uri="artifact://test",
        content_hash="a" * 64,
        metadata={"kind": "http-snapshot"},
        trust_label="UNTRUSTED_DATA",
        source_type="http-snapshot",
    )
    assert snapshot.trust_label == "UNTRUSTED_DATA"
    assert snapshot.source_type == "http-snapshot"
    assert snapshot.parser_version == "evidence-v1"
    assert snapshot.injection_site == "discovery"
