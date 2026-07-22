import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.csv_summary import execute_csv_summary
from regent.application.transition_service import TransitionContext, TransitionService
from regent.domain.transitions import GoalCommand, RunCommand, WorkCommand
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.models import (
    ArtifactModel,
    EvidenceModel,
    GoalModel,
    GoalSpecModel,
    RunModel,
    WorkModel,
)


@dataclass(frozen=True, slots=True)
class BaselineReceipt:
    goal_id: uuid.UUID
    work_id: uuid.UUID
    run_id: uuid.UUID
    artifact_id: uuid.UUID
    evidence_id: uuid.UUID
    goal_status: str
    work_status: str
    run_status: str
    input_hash: str
    output_hash: str
    replayed: bool


class CsvSummaryBaselineService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        artifacts: FileArtifactStore,
    ) -> None:
        self._sessions = sessions
        self._artifacts = artifacts
        self._transitions = TransitionService(sessions)

    async def execute(
        self,
        *,
        csv_content: str,
        idempotency_key: str,
        actor: str,
    ) -> BaselineReceipt:
        existing = await self._existing(idempotency_key)
        if existing is not None:
            return existing

        goal_id, work_id, run_id, correlation_id = (uuid.uuid4() for _ in range(4))
        async with self._sessions() as session, session.begin():
            session.add_all(
                (
                    GoalModel(
                        id=goal_id,
                        original_input=(
                            "Read authorized orders.csv and write output/summary.json; "
                            "do not use network or modify input."
                        ),
                        created_by=actor,
                        correlation_id=correlation_id,
                        metadata_json={"baseline": "CSV_SUMMARY_BASELINE"},
                    ),
                    GoalSpecModel(
                        id=uuid.uuid4(),
                        goal_id=goal_id,
                        version=1,
                        status="FROZEN",
                        content_hash="legacy-deterministic-baseline",
                        confirmed_by=actor,
                        explicit_constraints={
                            "network": "forbidden",
                            "input_mutation": "forbidden",
                            "write_scope": "output/",
                        },
                        system_inferences={"amount_column": "amount"},
                        unknowns=[],
                        success_criteria={
                            "row_count": 4,
                            "valid_count": 3,
                            "invalid_count": 1,
                            "total_amount": 30.0,
                        },
                        source_refs=[],
                    ),
                    WorkModel(
                        id=work_id,
                        goal_id=goal_id,
                        purpose="Generate deterministic CSV summary",
                        input_refs=[{"name": "orders.csv"}],
                        acceptance_criteria={"exact": True},
                        dependency_ids=[],
                        priority=0,
                        budget={"network_calls": 0},
                        correlation_id=correlation_id,
                        metadata_json={},
                    ),
                    RunModel(
                        id=run_id,
                        work_id=work_id,
                        actor_id="deterministic-csv-executor",
                        tool_ref="csv-summary:v1",
                        input_version="sha256:pending",
                        idempotency_key=idempotency_key,
                        resource_usage={},
                        correlation_id=correlation_id,
                    ),
                )
            )

        await self._transition_all_to_running(
            goal_id=goal_id,
            work_id=work_id,
            run_id=run_id,
            actor=actor,
            correlation_id=correlation_id,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "orders.csv"
            input_path.write_text(csv_content, encoding="utf-8", newline="")
            evidence = execute_csv_summary(
                goal_id=goal_id,
                input_path=input_path,
                artifacts=self._artifacts,
            )

        artifact_id, evidence_id = uuid.uuid4(), uuid.uuid4()
        async with self._sessions() as session, session.begin():
            run = await session.get(RunModel, run_id, with_for_update=True)
            if run is None:
                raise RuntimeError("baseline run disappeared")
            run.input_version = f"sha256:{evidence.input_hash}"
            run.result = {
                "row_count": evidence.row_count,
                "valid_count": evidence.valid_count,
                "invalid_count": evidence.invalid_count,
                "total_amount": evidence.total_amount,
                "output_hash": evidence.output_hash,
            }
            run.resource_usage = {"network_calls": 0, "model_calls": 0}
            session.add_all(
                (
                    ArtifactModel(
                        id=artifact_id,
                        goal_id=goal_id,
                        work_id=work_id,
                        run_id=run_id,
                        artifact_type="csv_summary",
                        schema_ref="regent://schemas/csv-summary/v1",
                        uri=evidence.artifact.uri,
                        content_hash=evidence.output_hash,
                        producer_ref="csv-summary:v1",
                        provenance={"input_hash": evidence.input_hash},
                        version=1,
                    ),
                    EvidenceModel(
                        id=evidence_id,
                        goal_id=goal_id,
                        work_id=work_id,
                        run_id=run_id,
                        artifact_id=artifact_id,
                        evidence_type="deterministic_execution",
                        uri=evidence.artifact.uri,
                        content_hash=evidence.output_hash,
                        producer_ref="csv-summary:v1",
                        quality_tier="EXACT",
                        payload={
                            "input_hash": evidence.input_hash,
                            "output_hash": evidence.output_hash,
                            "input_unchanged": True,
                            "network_calls": 0,
                        },
                    ),
                )
            )

        await self._transition_all_to_complete(
            goal_id=goal_id,
            work_id=work_id,
            run_id=run_id,
            actor=actor,
            correlation_id=correlation_id,
        )
        result = await self._existing(idempotency_key)
        if result is None:
            raise RuntimeError("completed baseline could not be loaded")
        return replace(result, replayed=False)

    async def _transition_all_to_running(
        self,
        *,
        goal_id: uuid.UUID,
        work_id: uuid.UUID,
        run_id: uuid.UUID,
        actor: str,
        correlation_id: uuid.UUID,
    ) -> None:
        for version, goal_command in enumerate((GoalCommand.QUALIFY, GoalCommand.ACTIVATE)):
            await self._transitions.transition_goal(
                TransitionContext(goal_id, version, actor, correlation_id), goal_command
            )
        for version, work_command in enumerate((WorkCommand.MAKE_READY, WorkCommand.START)):
            await self._transitions.transition_work(
                TransitionContext(work_id, version, actor, correlation_id), work_command
            )
        for version, run_command in enumerate(
            (RunCommand.REQUEST_PERMIT, RunCommand.QUEUE, RunCommand.CLAIM)
        ):
            await self._transitions.transition_run(
                TransitionContext(run_id, version, actor, correlation_id), run_command
            )

    async def _transition_all_to_complete(
        self,
        *,
        goal_id: uuid.UUID,
        work_id: uuid.UUID,
        run_id: uuid.UUID,
        actor: str,
        correlation_id: uuid.UUID,
    ) -> None:
        await self._transitions.transition_run(
            TransitionContext(run_id, 3, actor, correlation_id), RunCommand.MARK_EXECUTED
        )
        await self._transitions.transition_work(
            TransitionContext(work_id, 2, actor, correlation_id),
            WorkCommand.REQUEST_EVALUATION,
        )
        await self._transitions.transition_work(
            TransitionContext(work_id, 3, actor, correlation_id), WorkCommand.ACCEPT
        )
        await self._transitions.transition_goal(
            TransitionContext(goal_id, 2, actor, correlation_id), GoalCommand.ACHIEVE
        )

    async def _existing(self, idempotency_key: str) -> BaselineReceipt | None:
        async with self._sessions() as session:
            run = await session.scalar(
                select(RunModel).where(RunModel.idempotency_key == idempotency_key)
            )
            if run is None or run.status != "EXECUTED":
                return None
            work = await session.get(WorkModel, run.work_id)
            if work is None:
                return None
            goal = await session.get(GoalModel, work.goal_id)
            artifact = await session.scalar(
                select(ArtifactModel).where(ArtifactModel.run_id == run.id)
            )
            evidence = await session.scalar(
                select(EvidenceModel).where(EvidenceModel.run_id == run.id)
            )
            if goal is None or artifact is None or evidence is None:
                return None
            payload: dict[str, Any] = evidence.payload
            return BaselineReceipt(
                goal_id=goal.id,
                work_id=work.id,
                run_id=run.id,
                artifact_id=artifact.id,
                evidence_id=evidence.id,
                goal_status=goal.status,
                work_status=work.status,
                run_status=run.status,
                input_hash=str(payload["input_hash"]),
                output_hash=str(payload["output_hash"]),
                replayed=True,
            )
