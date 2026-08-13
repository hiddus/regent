"""Unit tests for generation hive executor models + steer overlay."""

from __future__ import annotations

from regent.application.generation_hive_executor import (
    GENERATION_HIVE_LEASE_SECONDS,
    GenerationHivePmPlan,
    GenerationHiveQaReview,
)


def test_hive_lease_covers_long_generation() -> None:
    assert GENERATION_HIVE_LEASE_SECONDS >= 900


def test_pm_plan_schema() -> None:
    plan = GenerationHivePmPlan(
        execution_plan=["seed US/SG", "wire refresh jobs"],
        acceptance_focus=["crosswalk readable", "fault-tolerant collect"],
        multi_agent_work_split=["country agents", "pairwise agents"],
        risk_notes=["stale law pages"],
        progress_summary="Ship compliance crosswalk MVP",
    )
    assert "US/SG" in plan.execution_plan[0]


def test_qa_review_schema() -> None:
    review = GenerationHiveQaReview(
        accepted=True,
        score=0.8,
        reason="MVP structure covers US-SG crosswalk scaffold",
        gaps=[],
        pending_live_verification=True,
    )
    assert review.accepted
    assert review.pending_live_verification is True


def test_live_content_review_model() -> None:
    from regent.application.generation_hive_executor import GenerationHiveLiveContentReview

    review = GenerationHiveLiveContentReview(
        accepted=False,
        score=0.2,
        reason="Live Preview content-depth failed",
        gaps=["preview-content-depth"],
        product_notes=["seed too thin"],
        tech_notes=["/api/countries 404"],
    )
    assert review.accepted is False
    assert "preview-content-depth" in review.gaps
