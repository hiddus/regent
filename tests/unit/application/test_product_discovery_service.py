import uuid
from typing import Any

import pytest
from regent.application.goal_eligibility_service import GoalEligibilityService
from regent.application.p1_contracts import (
    AppRequirementProposal,
    EvidenceClaim,
    EvidenceClassification,
    HypothesisDecisionValue,
    HypothesisSelection,
    ProductHypothesisProposal,
)
from regent.application.p1_ports import EvidenceSourceRequest, EvidenceSourceSnapshot
from regent.application.product_discovery_service import (
    ProductDiscoveryService,
    ProductHypothesisBatch,
    RequirementRevisionService,
    observed_evidence_ids,
)
from regent.domain.errors import DomainError
from regent.infrastructure.evidence_sources import InMemoryEvidenceSourceConnector
from regent.model import ModelUsage, StructuredModelResponse


class QueueProvider:
    def __init__(self, *outputs: Any) -> None:
        self.outputs = list(outputs)

    async def generate_structured(self, **_: Any) -> Any:
        output = self.outputs.pop(0)
        return StructuredModelResponse(
            output=output,
            usage=ModelUsage(input_tokens=1, output_tokens=1),
            model="fake-v1",
        )


def hypothesis(key: str, evidence_id: uuid.UUID) -> ProductHypothesisProposal:
    return ProductHypothesisProposal(
        candidate_key=key,
        target_users_hypothesis="target users",
        problem_hypothesis="problem",
        value_proposition="value",
        candidate_solution="solution",
        minimum_validation="preview test",
        success_signals=["retention"],
        failure_signals=["no usage"],
        estimated_time="one week",
        reversibility="high",
        claims=[
            EvidenceClaim(
                claim_key=f"{key}-claim",
                statement="observed need",
                classification=EvidenceClassification.OBSERVED,
                evidence_ids=[evidence_id],
            )
        ],
    )


@pytest.mark.asyncio
async def test_discovery_acquires_evidence_and_selects_existing_candidate() -> None:
    evidence_id = uuid.uuid4()
    snapshot = EvidenceSourceSnapshot(
        source_uri="memory://research",
        captured_at="2026-07-18T00:00:00Z",
        content_artifact_uri="artifact://evidence/1",
        content_hash="a" * 64,
    )
    candidates = ProductHypothesisBatch(
        hypotheses=[hypothesis("one", evidence_id), hypothesis("two", evidence_id)]
    )
    decision = HypothesisSelection(
        decision=HypothesisDecisionValue.SELECT,
        selected_candidate_key="one",
        rationale="best evidence",
        policy_version="product-hypothesis-decision-v1",
    )
    connector = InMemoryEvidenceSourceConnector([snapshot])
    service = ProductDiscoveryService(connector, QueueProvider(candidates, decision))  # type: ignore[arg-type]
    outcome = await service.discover(
        goal="find a useful product",
        constraints={},
        requests=[EvidenceSourceRequest(query="research", correlation_id="corr")],
        evidence_ids_by_hash={"a" * 64: evidence_id},
    )
    assert outcome.decision.selected_candidate_key == "one"
    assert outcome.evidence_digest
    assert connector.requests[0].query == "research"


@pytest.mark.asyncio
async def test_discovery_rejects_model_selected_unknown_candidate() -> None:
    evidence_id = uuid.uuid4()
    snapshot = EvidenceSourceSnapshot(
        source_uri="memory://research",
        captured_at="now",
        content_artifact_uri="artifact://1",
        content_hash="b" * 64,
    )
    candidates = ProductHypothesisBatch(
        hypotheses=[hypothesis("one", evidence_id), hypothesis("two", evidence_id)]
    )
    decision = HypothesisSelection(
        decision=HypothesisDecisionValue.SELECT,
        selected_candidate_key="missing",
        rationale="bad",
        policy_version="product-hypothesis-decision-v1",
    )
    service = ProductDiscoveryService(
        InMemoryEvidenceSourceConnector([snapshot]),
        QueueProvider(candidates, decision),  # type: ignore[arg-type]
    )
    with pytest.raises(DomainError):
        await service.discover(
            goal="goal",
            constraints={},
            requests=[EvidenceSourceRequest(query="q", correlation_id="c")],
            evidence_ids_by_hash={"b" * 64: evidence_id},
        )


def test_goal_eligibility_is_explicit() -> None:
    service = GoalEligibilityService()
    assert service.evaluate({"goal_type": "product_creation"}, {}).eligible
    assert not service.evaluate({"goal_type": "analysis"}, {}).eligible


@pytest.mark.asyncio
async def test_requirement_revision_inherits_constraints() -> None:
    evidence_id = uuid.uuid4()
    proposal = AppRequirementProposal(
        product_outcome="outcome",
        target_users=["users"],
        problem_statement="problem",
        value_proposition="value",
        user_journeys=[{"name": "journey"}],
        functional_requirements=[{"name": "feature"}],
        success_metrics=[{"name": "metric"}],
        release_gates=[{"name": "gate"}],
        source_evidence=[evidence_id],
    )
    service = RequirementRevisionService(QueueProvider(proposal))  # type: ignore[arg-type]
    response = await service.propose(
        hypothesis=hypothesis("one", evidence_id),
        root_constraints={"region": "cn"},
        proposed_constraints={"timeout": 5},
    )
    assert response.output.product_outcome == "outcome"
    assert observed_evidence_ids(hypothesis("one", evidence_id)) == {evidence_id}
    with pytest.raises(DomainError):
        await service.propose(
            hypothesis=hypothesis("one", evidence_id),
            root_constraints={"region": "cn"},
            proposed_constraints={"region": "us"},
        )


@pytest.mark.asyncio
async def test_requirement_rejects_unrelated_evidence() -> None:
    hypothesis_evidence = uuid.uuid4()
    proposal = AppRequirementProposal(
        product_outcome="outcome",
        target_users=["users"],
        problem_statement="problem",
        value_proposition="value",
        user_journeys=[{"name": "journey"}],
        functional_requirements=[{"name": "feature"}],
        success_metrics=[{"name": "metric"}],
        release_gates=[{"name": "gate"}],
        source_evidence=[uuid.uuid4()],
    )
    service = RequirementRevisionService(QueueProvider(proposal))  # type: ignore[arg-type]
    with pytest.raises(DomainError):
        await service.propose(
            hypothesis=hypothesis("one", hypothesis_evidence), root_constraints={}
        )
