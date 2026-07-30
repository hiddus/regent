"""Eval delivery-verification scoring + DecisionRecord + north-star unit tests."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from regent.application.budget_ledger import COST_INFRASTRUCTURE, COST_MODEL_INPUT, BudgetLedger
from regent.application.eval_harness_service import CreateEvalRun, EvalHarnessService
from regent.application.north_star_metrics import MIN_VERIFIED_SUCCESS, NorthStarMetricsService
from regent.domain.states import GoalState
from regent.infrastructure.models import EvidenceModel, GoalModel

FIXTURE = Path("fixtures/eval_single_agent_baseline_v1.json")


@pytest.mark.eval
@pytest.mark.asyncio
async def test_eval_scores_via_delivery_signals_not_hash_stub(db_sessions) -> None:
    task_set = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # Drop optional goal_evidence task without goal_id so it doesn't drag rate down oddly;
    # keep four delivery_signal tasks (3 expect pass, 1 expect fail-closed pass).
    task_set["tasks"] = [t for t in task_set["tasks"] if t["id"] != "goal-db-verified"]
    svc = EvalHarnessService(db_sessions)
    model = await svc.create(
        CreateEvalRun(
            name="single-agent-baseline",
            task_set=task_set,
            baseline={"name": "strong_single_agent"},
            budget={"wall_clock_budget_s": 600},
            seed="eval-seed-1",
            actor="tester",
        )
    )
    await svc.freeze(model.id, actor="tester")
    scored = await svc.run_and_score(model.id, actor="tester")
    assert scored.status == "SCORED"
    assert scored.scores_json["scoring_mode"] == "delivery_verification"
    assert "hash%2" not in json.dumps(scored.scores_json)
    for item in scored.scores_json["tasks"]:
        assert "evidence_refs" in item
        assert item["scoring_mode"] in {"delivery_signals", "goal_evidence"}
        assert "pass@1" in item
    assert scored.scores_json["pass_at_1_rate"] == 1.0
    assert scored.scores_json.get("evidence_digest")

    decided = await svc.decide(model.id, actor="tester")
    assert decided.status == "DECIDED"
    record = decided.metrics_json["product_decision_record"]
    assert record["signature"]
    assert record["task_set_hash"] == decided.task_set_hash
    assert record["org_adaptive_status"] == "ROLLOUT_NOT_ALLOWED"


@pytest.mark.eval
@pytest.mark.asyncio
async def test_eval_goal_evidence_mode(db_sessions) -> None:
    goal_id = uuid.uuid4()
    corr = uuid.uuid4()
    async with db_sessions() as session, session.begin():
        session.add(
            GoalModel(
                id=goal_id,
                original_input="eval goal",
                created_by="tester",
                correlation_id=corr,
                status=GoalState.ACHIEVED.value,
                metadata_json={},
            )
        )
        await session.flush()
        session.add(
            EvidenceModel(
                id=uuid.uuid4(),
                goal_id=goal_id,
                evidence_type="deterministic_execution",
                content_hash="a" * 64,
                producer_ref="test",
                quality_tier="EXACT",
                payload={},
            )
        )
    svc = EvalHarnessService(db_sessions)
    model = await svc.create(
        CreateEvalRun(
            name="goal-evidence",
            task_set={
                "tasks": [
                    {
                        "id": "g1",
                        "verification": {"mode": "goal_evidence", "goal_id": str(goal_id)},
                    }
                ]
            },
            baseline={},
            budget={},
            seed="s",
            actor="tester",
        )
    )
    await svc.freeze(model.id, actor="tester")
    scored = await svc.run_and_score(model.id, actor="tester")
    assert scored.scores_json["tasks"][0]["pass@1"] is True


@pytest.mark.eval
@pytest.mark.asyncio
async def test_north_star_insufficient_evidence_below_sample(db_sessions) -> None:
    svc = NorthStarMetricsService(db_sessions)
    report = await svc.report(now=datetime.now(UTC))
    assert report.status == "INSUFFICIENT_EVIDENCE"
    assert report.verified_success_count < MIN_VERIFIED_SUCCESS
    assert report.cost_per_verified_success is None
    assert len(report.guardrails) == 8


@pytest.mark.eval
@pytest.mark.asyncio
async def test_north_star_window_and_guardrail_red_on_stale_unknown(db_sessions) -> None:
    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    ledger = BudgetLedger(db_sessions)
    for i in range(MIN_VERIFIED_SUCCESS):
        goal_id = uuid.uuid4()
        corr = uuid.uuid4()
        async with db_sessions() as session, session.begin():
            goal = GoalModel(
                id=goal_id,
                original_input=f"vs-{i}",
                created_by="tester",
                correlation_id=corr,
                status=GoalState.ACHIEVED.value,
                metadata_json={},
            )
            # Stamp window timestamps after flush via explicit assignment.
            session.add(goal)
            await session.flush()
            goal.created_at = now - timedelta(days=2)
            goal.updated_at = now - timedelta(days=1)
            session.add(
                EvidenceModel(
                    id=uuid.uuid4(),
                    goal_id=goal_id,
                    evidence_type="deterministic_execution",
                    content_hash=f"{i:064d}"[:64].replace(" ", "0"),
                    producer_ref="test",
                    quality_tier="EXACT",
                    payload={},
                )
            )
        await ledger.record_cost(
            goal_id,
            None,
            cost_type=COST_MODEL_INPUT,
            amount=2.0,
            description="north-star",
        )
        await ledger.record_cost(
            goal_id,
            None,
            cost_type=COST_INFRASTRUCTURE,
            amount=1.0,
        )

    # Force recorded_at into window for budget rows.
    async with db_sessions() as session, session.begin():
        from regent.infrastructure.models import BudgetEntryModel

        rows = list(await session.scalars(select(BudgetEntryModel)))
        for row in rows:
            row.recorded_at = now - timedelta(days=1)

    report = await NorthStarMetricsService(db_sessions).report(now=now)
    assert report.verified_success_count >= MIN_VERIFIED_SUCCESS
    assert report.cost_per_verified_success is not None
    assert report.cost_per_verified_success == pytest.approx(3.0)
    assert any(g.name == "completion_rate" for g in report.guardrails)
