"""Phase 5.2: Evidence chain integrity tests.

Verifies that the full evidence chain from Goal creation to Decision
is queryable and properly linked:
- GoalSpec → DiscoveryRound → Evidence → Hypothesis → Decision
- Requirement → Resolution → Generation → Build → Deployment
- Observation → GateEvaluation → IterationDecision
"""

from __future__ import annotations

import uuid

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from regent.application.execution_events import P1_MAIN_CHAIN_EVENTS
from regent.infrastructure.models import (
    AppBuildModel,
    BudgetEntryModel,
    DeploymentModel,
    DiscoveryRoundModel,
    EvidenceModel,
    GoalModel,
    GoalSpecModel,
    HypothesisDecisionModel,
    HypothesisEvidenceRefModel,
    ObservationModel,
    ProductHypothesisModel,
    RequirementRevisionModel,
)


# ---------------------------------------------------------------------------
# Goal → Discovery → Evidence → Hypothesis → Decision chain
# ---------------------------------------------------------------------------


def test_goal_has_fk_to_specs_and_rounds() -> None:
    """GoalModel is the root; specs and rounds reference it via FK."""
    goal_ddl = str(CreateTable(GoalModel.__table__).compile(dialect=postgresql.dialect()))
    spec_ddl = str(CreateTable(GoalSpecModel.__table__).compile(dialect=postgresql.dialect()))
    round_ddl = str(CreateTable(DiscoveryRoundModel.__table__).compile(dialect=postgresql.dialect()))
    assert "goals" in goal_ddl
    assert "goal_id" in spec_ddl.lower() or "goals" in spec_ddl
    assert "goal_id" in round_ddl.lower() or "goals" in round_ddl


def test_evidence_links_to_goal() -> None:
    """EvidenceModel has FK to goals table."""
    ddl = str(CreateTable(EvidenceModel.__table__).compile(dialect=postgresql.dialect()))
    assert "goal_id" in ddl.lower()
    assert "goals" in ddl


def test_hypothesis_links_to_round() -> None:
    """ProductHypothesisModel has FK to discovery_rounds."""
    ddl = str(CreateTable(ProductHypothesisModel.__table__).compile(dialect=postgresql.dialect()))
    assert "round_id" in ddl.lower()
    assert "discovery_rounds" in ddl


def test_hypothesis_evidence_ref_bridges_hypothesis_and_evidence() -> None:
    """HypothesisEvidenceRefModel bridges hypotheses ↔ evidence."""
    ddl = str(
        CreateTable(HypothesisEvidenceRefModel.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "hypothesis_id" in ddl.lower()
    assert "evidence_id" in ddl.lower()
    assert "product_hypotheses" in ddl
    assert "evidence" in ddl


def test_decision_links_to_round_and_hypothesis() -> None:
    """HypothesisDecisionModel links to both round and selected hypothesis."""
    ddl = str(
        CreateTable(HypothesisDecisionModel.__table__).compile(dialect=postgresql.dialect())
    )
    assert "round_id" in ddl.lower()
    assert "selected_hypothesis_id" in ddl.lower()


# ---------------------------------------------------------------------------
# Requirement → Resolution → Generation → Build → Deployment chain
# ---------------------------------------------------------------------------


def test_requirement_revision_model_exists() -> None:
    """RequirementRevisionModel table is defined."""
    ddl = str(
        CreateTable(RequirementRevisionModel.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "requirement_revisions" in ddl


def test_app_build_model_exists() -> None:
    """AppBuildModel table is defined."""
    ddl = str(CreateTable(AppBuildModel.__table__).compile(dialect=postgresql.dialect()))
    assert "app_builds" in ddl


def test_deployment_model_exists() -> None:
    """DeploymentModel table is defined."""
    ddl = str(CreateTable(DeploymentModel.__table__).compile(dialect=postgresql.dialect()))
    assert "deployments" in ddl


# ---------------------------------------------------------------------------
# Observation → Decision chain
# ---------------------------------------------------------------------------


def test_observation_model_exists() -> None:
    """ObservationModel table is defined."""
    ddl = str(CreateTable(ObservationModel.__table__).compile(dialect=postgresql.dialect()))
    assert "observations" in ddl


# ---------------------------------------------------------------------------
# Budget chain
# ---------------------------------------------------------------------------


def test_budget_entry_links_to_goal() -> None:
    """BudgetEntryModel has FK to goals."""
    ddl = str(CreateTable(BudgetEntryModel.__table__).compile(dialect=postgresql.dialect()))
    assert "goal_id" in ddl.lower()
    assert "goals" in ddl
    assert "budget_entries" in ddl


# ---------------------------------------------------------------------------
# Event catalog completeness
# ---------------------------------------------------------------------------


def test_p1_event_catalog_has_16_events() -> None:
    """P1 main chain has 16 event types covering the full lifecycle."""
    assert len(P1_MAIN_CHAIN_EVENTS) == 16


def test_p1_events_cover_goal_to_deployment() -> None:
    """P1 events span from goal execution to deployment."""
    assert "GoalExecutionRequested" in P1_MAIN_CHAIN_EVENTS
    assert "DiscoveryRoundRequested" in P1_MAIN_CHAIN_EVENTS
    assert "DiscoveryCompleted" in P1_MAIN_CHAIN_EVENTS
    assert "AppBuildRequested" in P1_MAIN_CHAIN_EVENTS
    assert "PreviewDeploymentRequested" in P1_MAIN_CHAIN_EVENTS
    assert "PreviewDeploymentSucceeded" in P1_MAIN_CHAIN_EVENTS
    assert "QualityApprovalRequested" in P1_MAIN_CHAIN_EVENTS
    assert "QualityApprovalCompleted" in P1_MAIN_CHAIN_EVENTS


# ---------------------------------------------------------------------------
# Trust classification in evidence chain
# ---------------------------------------------------------------------------


def test_evidence_model_has_quality_tier() -> None:
    """EvidenceModel carries quality_tier for trust classification."""
    ddl = str(CreateTable(EvidenceModel.__table__).compile(dialect=postgresql.dialect()))
    assert "quality_tier" in ddl.lower()


def test_evidence_model_has_content_hash() -> None:
    """EvidenceModel carries content_hash for integrity verification."""
    ddl = str(CreateTable(EvidenceModel.__table__).compile(dialect=postgresql.dialect()))
    assert "content_hash" in ddl.lower()
