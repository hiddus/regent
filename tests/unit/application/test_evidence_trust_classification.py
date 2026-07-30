"""Spec §12 five-class evidence classification + product Gate hard rule."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from regent.application.evidence_policy import (
    EVIDENCE_CLASS_BUILD_VERIFICATION,
    EVIDENCE_CLASS_DECLARED_INTENT,
    EVIDENCE_CLASS_OPERATIONAL_OBSERVATION,
    EVIDENCE_CLASS_PRODUCT_OBSERVATION,
    EVIDENCE_CLASS_SOURCED_OBSERVATION,
    classify_evidence,
    evidence_may_satisfy_product_gate,
)
from regent.application.p1_ports import EvidenceSourceSnapshot


def _snap(**kwargs: object) -> EvidenceSourceSnapshot:
    base = dict(
        source_uri="https://example.com",
        captured_at=datetime.now(UTC).isoformat(),
        content_artifact_uri="artifact://test",
        content_hash="a" * 64,
        metadata={},
    )
    base.update(kwargs)
    return EvidenceSourceSnapshot(**base)  # type: ignore[arg-type]


def test_classify_evidence_five_classes() -> None:
    assert (
        classify_evidence(_snap(metadata={"kind": "goal-intent"}, source_type="goal-intent"))
        == EVIDENCE_CLASS_DECLARED_INTENT
    )
    assert (
        classify_evidence(_snap(metadata={"kind": "http-snapshot"}, trust_label="UNTRUSTED_DATA"))
        == EVIDENCE_CLASS_SOURCED_OBSERVATION
    )
    assert (
        classify_evidence(_snap(metadata={"kind": "build-report"}, source_type="ci-verification"))
        == EVIDENCE_CLASS_BUILD_VERIFICATION
    )
    assert (
        classify_evidence(
            _snap(metadata={"kind": "product-observation"}, source_type="user-feedback")
        )
        == EVIDENCE_CLASS_PRODUCT_OBSERVATION
    )
    assert (
        classify_evidence(_snap(metadata={"kind": "operational-smoke"}, source_type="monitor"))
        == EVIDENCE_CLASS_OPERATIONAL_OBSERVATION
    )


def test_operational_and_declared_cannot_satisfy_product_gate() -> None:
    assert evidence_may_satisfy_product_gate(EVIDENCE_CLASS_OPERATIONAL_OBSERVATION) is False
    assert evidence_may_satisfy_product_gate(EVIDENCE_CLASS_DECLARED_INTENT) is False
    assert evidence_may_satisfy_product_gate("DECLARED_INTENT") is False
    assert evidence_may_satisfy_product_gate(EVIDENCE_CLASS_SOURCED_OBSERVATION) is True
    assert evidence_may_satisfy_product_gate(EVIDENCE_CLASS_BUILD_VERIFICATION) is True
    assert evidence_may_satisfy_product_gate(EVIDENCE_CLASS_PRODUCT_OBSERVATION) is True


def test_classify_evidence_labels_goal_intent_as_declared() -> None:
    snapshot = _snap(
        source_uri="regent://goal-intent/test",
        metadata={"kind": "goal-intent", "connector": "goal-intent-v1"},
        trust_label="DECLARED_INTENT",
        source_type="goal-intent",
    )
    assert classify_evidence(snapshot) == EVIDENCE_CLASS_DECLARED_INTENT


def test_classify_evidence_labels_http_snapshot_as_sourced() -> None:
    snapshot = _snap(
        metadata={"kind": "http-snapshot", "connector": "allowlisted-http-source-v1"},
        trust_label="UNTRUSTED_DATA",
        source_type="http-snapshot",
    )
    assert classify_evidence(snapshot) == EVIDENCE_CLASS_SOURCED_OBSERVATION


@pytest.mark.asyncio
async def test_goal_intent_connector_sets_declared_intent_label(tmp_path) -> None:
    from regent.application.p1_ports import EvidenceSourceRequest
    from regent.infrastructure.artifact_store import FileArtifactStore
    from regent.infrastructure.evidence_sources import GoalIntentEvidenceConnector

    store = FileArtifactStore(tmp_path)
    connector = GoalIntentEvidenceConnector(store)
    request = EvidenceSourceRequest(
        query="test goal",
        correlation_id="test-corr",
        authorized_urls=[],
    )
    snapshots = await connector.fetch(request)
    assert len(snapshots) == 1
    assert snapshots[0].trust_label == "DECLARED_INTENT"
    assert classify_evidence(snapshots[0]) == EVIDENCE_CLASS_DECLARED_INTENT
