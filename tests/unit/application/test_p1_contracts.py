import uuid

import pytest
from pydantic import ValidationError
from regent.application.p1_contracts import (
    EvidenceClaim,
    EvidenceClassification,
    FileChange,
    FileOperation,
    HypothesisDecisionValue,
    HypothesisSelection,
    ProductHypothesisProposal,
    canonical_hash,
    inherit_constraints,
    validate_evidence_references,
)
from regent.domain.errors import DomainError


def test_selection_requires_candidate() -> None:
    with pytest.raises(ValidationError):
        HypothesisSelection(
            decision=HypothesisDecisionValue.SELECT, rationale="best", policy_version="v1"
        )


def test_file_change_normalizes_and_requires_content() -> None:
    change = FileChange(
        relative_path="app\\main.py",
        operation=FileOperation.CREATE,
        content_artifact_uri="artifact://1",
        content_hash="a" * 64,
        rationale="entry",
    )
    assert change.relative_path == "app/main.py"
    with pytest.raises(ValidationError):
        FileChange(relative_path="../secret", operation=FileOperation.DELETE, rationale="bad")


def test_observed_claim_requires_available_evidence() -> None:
    evidence_id = uuid.uuid4()
    hypothesis = ProductHypothesisProposal(
        candidate_key="candidate-1",
        target_users_hypothesis="users",
        problem_hypothesis="problem",
        value_proposition="value",
        candidate_solution="solution",
        minimum_validation="test",
        success_signals=["use"],
        failure_signals=["leave"],
        estimated_time="1d",
        reversibility="high",
        claims=[
            EvidenceClaim(
                claim_key="c1",
                statement="observed",
                classification=EvidenceClassification.OBSERVED,
                evidence_ids=[evidence_id],
            )
        ],
    )
    validate_evidence_references([hypothesis], {evidence_id})
    with pytest.raises(DomainError):
        validate_evidence_references([hypothesis], set())


def test_hash_is_canonical_and_root_constraints_win() -> None:
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})
    assert inherit_constraints({"region": "cn"}, {"timeout": 3}) == {"timeout": 3, "region": "cn"}
    with pytest.raises(DomainError):
        inherit_constraints({"region": "cn"}, {"region": "us"})
