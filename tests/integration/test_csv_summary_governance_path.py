"""CSV_SUMMARY_BASELINE governance path — real Goal/Work/Run/Evidence/Audit."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from regent.application.baseline_service import CsvSummaryBaselineService
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.models import (
    AuditRecordModel,
    EvidenceModel,
    GoalModel,
    RunModel,
    WorkModel,
)

FIXTURE = Path("tests/fixtures/csv_summary_baseline/orders.csv")


@pytest.mark.governance
@pytest.mark.asyncio
async def test_csv_summary_baseline_governance_path(db_sessions, tmp_path: Path) -> None:
    csv_content = FIXTURE.read_text(encoding="utf-8")
    service = CsvSummaryBaselineService(
        db_sessions,
        FileArtifactStore(tmp_path / "artifacts"),
    )
    receipt = await service.execute(
        csv_content=csv_content,
        idempotency_key="csv-baseline-gov-1",
        actor="test-governance",
    )

    assert receipt.goal_status == "ACHIEVED"
    assert receipt.work_status == "ACCEPTED"
    assert receipt.run_status == "EXECUTED"
    assert receipt.replayed is False
    assert receipt.input_hash
    assert receipt.output_hash
    assert receipt.evidence_id
    assert receipt.artifact_id

    async with db_sessions() as session:
        goal = await session.get(GoalModel, receipt.goal_id)
        work = await session.get(WorkModel, receipt.work_id)
        run = await session.get(RunModel, receipt.run_id)
        evidence = await session.get(EvidenceModel, receipt.evidence_id)
        audits = list(
            await session.scalars(
                select(AuditRecordModel).where(
                    AuditRecordModel.aggregate_id.in_(
                        [receipt.goal_id, receipt.work_id, receipt.run_id]
                    )
                )
            )
        )

    assert goal is not None and goal.status == "ACHIEVED"
    assert work is not None and work.status == "ACCEPTED"
    assert run is not None and run.status == "EXECUTED"
    assert evidence is not None
    assert evidence.content_hash == receipt.output_hash
    assert evidence.goal_id == receipt.goal_id
    assert len(audits) >= 3


@pytest.mark.governance
@pytest.mark.idempotency
@pytest.mark.asyncio
async def test_csv_summary_baseline_idempotent_replay(db_sessions, tmp_path: Path) -> None:
    csv_content = FIXTURE.read_text(encoding="utf-8")
    service = CsvSummaryBaselineService(
        db_sessions,
        FileArtifactStore(tmp_path / "artifacts"),
    )
    first = await service.execute(
        csv_content=csv_content,
        idempotency_key="csv-baseline-gov-replay",
        actor="test-governance",
    )
    second = await service.execute(
        csv_content=csv_content,
        idempotency_key="csv-baseline-gov-replay",
        actor="test-governance",
    )
    assert second.replayed is True
    assert second.goal_id == first.goal_id
    assert second.run_id == first.run_id
    assert second.output_hash == first.output_hash
