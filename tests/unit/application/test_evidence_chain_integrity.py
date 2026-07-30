"""Evidence chain integrity — behavioral persistence, not DDL string checks."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from regent.application.execution_events import P1_MAIN_CHAIN_EVENTS
from regent.domain.states import GoalState
from regent.infrastructure.models import (
    ArtifactModel,
    AuditRecordModel,
    BudgetEntryModel,
    EvidenceModel,
    GoalModel,
    GoalSpecModel,
    HypothesisDecisionModel,
    DiscoveryRoundModel,
    ProductHypothesisModel,
    HypothesisEvidenceRefModel,
)


@pytest.mark.governance
@pytest.mark.asyncio
async def test_evidence_chain_persists_goal_to_decision(db_sessions) -> None:
    goal_id = uuid.uuid4()
    corr = uuid.uuid4()
    artifact_id = uuid.uuid4()
    evidence_id = uuid.uuid4()
    round_id = uuid.uuid4()
    hyp_id = uuid.uuid4()
    decision_id = uuid.uuid4()

    async with db_sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                original_input="evidence chain fixture",
                created_by="tester",
                correlation_id=corr,
                status=GoalState.ACTIVE.value,
                metadata_json={"budget_limit": 100.0},
            )
        )
        await session.flush()
        session.add_all(
            (
                GoalSpecModel(
                    id=uuid.uuid4(),
                    goal_id=goal_id,
                    version=1,
                    status="FROZEN",
                    content_hash="a" * 64,
                    confirmed_by="tester",
                    explicit_constraints={},
                    system_inferences={},
                    unknowns=[],
                    success_criteria={"ok": True},
                    source_refs=[],
                ),
                DiscoveryRoundModel(
                    id=round_id,
                    goal_id=goal_id,
                    round=1,
                    status="DECIDED",
                    version=1,
                    input_snapshot_hash="d" * 64,
                    budget={"rounds": 1},
                    policy_version="discovery-v1",
                    idempotency_key=f"round-{goal_id}",
                    created_by="tester",
                    correlation_id=str(corr),
                ),
                ArtifactModel(
                    id=artifact_id,
                    goal_id=goal_id,
                    work_id=None,
                    run_id=None,
                    artifact_type="source_snapshot",
                    schema_ref="regent://schemas/evidence/v1",
                    uri="artifact://chain/1",
                    content_hash="b" * 64,
                    producer_ref="test",
                    provenance={},
                    version=1,
                ),
                EvidenceModel(
                    id=evidence_id,
                    goal_id=goal_id,
                    work_id=None,
                    run_id=None,
                    artifact_id=artifact_id,
                    evidence_type="sourced_observation",
                    uri="artifact://chain/1",
                    content_hash="b" * 64,
                    producer_ref="test",
                    quality_tier="OBSERVED",
                    payload={"class": "sourced-observation"},
                ),
                ProductHypothesisModel(
                    id=hyp_id,
                    round_id=round_id,
                    candidate_key="hyp-1",
                    content_json={"statement": "users need X"},
                    content_hash="e" * 64,
                    eligibility="ELIGIBLE",
                    invalid_reasons=[],
                    generator_ref="test-generator",
                ),
                HypothesisEvidenceRefModel(
                    id=uuid.uuid4(),
                    hypothesis_id=hyp_id,
                    evidence_id=evidence_id,
                    claim_key="market-need",
                    relation="supports",
                ),
                HypothesisDecisionModel(
                    id=decision_id,
                    round_id=round_id,
                    decision="SELECT",
                    selected_hypothesis_id=hyp_id,
                    rationale="best supported",
                    evidence_digest="c" * 64,
                    policy_version="discovery-v1",
                    created_by="tester",
                ),
                BudgetEntryModel(
                    id=uuid.uuid4(),
                    goal_id=goal_id,
                    run_id=None,
                    cost_type="model_input_tokens",
                    amount=1.25,
                    price_book_version="price-book-v1",
                    description="chain fixture",
                ),
                AuditRecordModel(
                    id=uuid.uuid4(),
                    aggregate_type="goal",
                    aggregate_id=goal_id,
                    aggregate_version=1,
                    action="ACTIVATE",
                    actor="tester",
                    payload={"from": "QUALIFIED"},
                    correlation_id=corr,
                ),
            )
        )

    async with db_sessions() as session:
        evidence = await session.get(EvidenceModel, evidence_id)
        hyp = await session.get(ProductHypothesisModel, hyp_id)
        decision = await session.get(HypothesisDecisionModel, decision_id)
        refs = list(
            await session.scalars(
                select(HypothesisEvidenceRefModel).where(
                    HypothesisEvidenceRefModel.hypothesis_id == hyp_id
                )
            )
        )
        budget = await session.scalar(
            select(BudgetEntryModel).where(BudgetEntryModel.goal_id == goal_id)
        )
        audits = list(
            await session.scalars(
                select(AuditRecordModel).where(AuditRecordModel.aggregate_id == goal_id)
            )
        )

    assert evidence is not None
    assert evidence.quality_tier == "OBSERVED"
    assert evidence.content_hash == "b" * 64
    assert hyp is not None and hyp.round_id == round_id
    assert decision is not None and decision.selected_hypothesis_id == hyp_id
    assert len(refs) == 1 and refs[0].evidence_id == evidence_id
    assert budget is not None and budget.amount == 1.25
    assert len(audits) == 1


@pytest.mark.governance
def test_p1_event_catalog_covers_lifecycle() -> None:
    assert len(P1_MAIN_CHAIN_EVENTS) == 16
    for name in (
        "GoalExecutionRequested",
        "DiscoveryRoundRequested",
        "DiscoveryCompleted",
        "AppBuildRequested",
        "PreviewDeploymentRequested",
        "PreviewDeploymentSucceeded",
        "QualityApprovalRequested",
        "QualityApprovalCompleted",
    ):
        assert name in P1_MAIN_CHAIN_EVENTS
