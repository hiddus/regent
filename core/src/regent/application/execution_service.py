import json
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.transition_service import TransitionContext, TransitionService
from regent.domain.errors import DomainError, ErrorCode
from regent.domain.transitions import GoalCommand, RunCommand, WorkCommand
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.models import (
    ArtifactModel,
    EvidenceModel,
    GoalModel,
    RunModel,
    WorkModel,
)
from regent.model import ModelProvider


class ExecutionOutput(BaseModel):
    result: dict[str, Any]
    evidence_claims: list[str] = Field(default_factory=list)
    progress_summary: str = Field(min_length=1)


class CriterionResult(BaseModel):
    criterion: str
    passed: bool
    reason: str


class EvaluationOutput(BaseModel):
    accepted: bool
    score: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)
    criteria: list[CriterionResult] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    goal_id: uuid.UUID
    work_id: uuid.UUID
    run_id: uuid.UUID
    artifact_id: uuid.UUID
    evidence_id: uuid.UUID
    run_status: str
    work_status: str
    goal_status: str
    accepted: bool
    score: float
    model_calls: int
    input_tokens: int
    output_tokens: int


class SingleAgentExecutionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: ModelProvider,
        artifacts: FileArtifactStore,
    ) -> None:
        self._sessions = sessions
        self._provider = provider
        self._artifacts = artifacts
        self._transitions = TransitionService(sessions)

    async def execute(self, work_id: uuid.UUID, *, actor: str) -> ExecutionReceipt:
        async with self._sessions() as session:
            work = await session.get(WorkModel, work_id)
            if work is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"work {work_id} not found")
            goal = await session.get(GoalModel, work.goal_id)
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"goal {work.goal_id} not found")
            dependencies = [uuid.UUID(value) for value in work.dependency_ids]
            if dependencies:
                states = list(
                    await session.scalars(
                        select(WorkModel.status).where(WorkModel.id.in_(dependencies))
                    )
                )
                if len(states) != len(dependencies) or any(state != "ACCEPTED" for state in states):
                    raise DomainError(ErrorCode.INVALID_STATE, "work dependencies are not accepted")
            max_calls = int(work.budget.get("max_model_calls", 2))
            if max_calls < 2:
                raise DomainError(ErrorCode.INVALID_STATE, "work budget requires two model calls")
            runs = list(
                await session.scalars(
                    select(RunModel)
                    .where(RunModel.work_id == work_id)
                    .order_by(RunModel.created_at)
                )
            )
            existing = next((run for run in runs if run.status == "EXECUTED"), None)
            if existing is not None:
                return await self._receipt(existing.id)
            attempt = len(runs) + 1
            work_status = work.status
            work_version = work.version
            correlation_id = work.correlation_id

        if goal.status == "READY":
            await self._transitions.transition_goal(
                TransitionContext(goal.id, goal.version, actor, correlation_id),
                GoalCommand.ACTIVATE,
            )
        elif goal.status != "ACTIVE":
            raise DomainError(ErrorCode.INVALID_STATE, "goal must be confirmed before execution")
        if work_status in {"UNKNOWN", "REJECTED"}:
            receipt = await self._transitions.transition_work(
                TransitionContext(work_id, work_version, actor, correlation_id),
                WorkCommand.RETRY,
            )
            work_version = receipt.version
        elif work_status == "PLANNED":
            receipt = await self._transitions.transition_work(
                TransitionContext(work_id, work_version, actor, correlation_id),
                WorkCommand.MAKE_READY,
            )
            work_version = receipt.version
        if work_status not in {"PLANNED", "READY", "UNKNOWN", "REJECTED"}:
            raise DomainError(ErrorCode.INVALID_STATE, f"work cannot execute from {work_status}")
        run_id = uuid.uuid4()
        async with self._sessions() as session, session.begin():
            session.add(
                RunModel(
                    id=run_id,
                    work_id=work_id,
                    actor_id=actor,
                    agent_spec_ref="single-agent:v1",
                    model_ref="configured-model",
                    input_version="goal-spec:v1",
                    idempotency_key=f"single-agent:{work_id}:{attempt}",
                    resource_usage={},
                    correlation_id=correlation_id,
                )
            )
        receipt = await self._transitions.transition_work(
            TransitionContext(work_id, work_version, actor, correlation_id),
            WorkCommand.START,
        )
        running_work_version = receipt.version
        for version, run_command in enumerate(
            (RunCommand.REQUEST_PERMIT, RunCommand.QUEUE, RunCommand.CLAIM)
        ):
            await self._transitions.transition_run(
                TransitionContext(run_id, version, actor, correlation_id), run_command
            )

        try:
            execution = await self._provider.generate_structured(
                system_prompt=(
                    "Execute only the described logical work. Do not claim external side effects, "
                    "network access, files, credentials, or tools you were not given. Return a "
                    "structured candidate result and explicit evidence claims."
                ),
                user_prompt=json.dumps(
                    {
                        "goal": goal.original_input,
                        "work": work.purpose,
                        "acceptance_criteria": work.acceptance_criteria,
                        "constraints": goal.metadata_json,
                    },
                    ensure_ascii=False,
                ),
                response_model=ExecutionOutput,
            )
            evaluation = await self._provider.generate_structured(
                system_prompt=(
                    "Act as an independent evaluator. Judge only against the supplied acceptance "
                    "criteria and candidate result. Reject unsupported claims. Return a score from "
                    "0.0 to 1.0 and criterion-level reasons."
                ),
                user_prompt=json.dumps(
                    {
                        "acceptance_criteria": work.acceptance_criteria,
                        "candidate": execution.output.model_dump(),
                    },
                    ensure_ascii=False,
                ),
                response_model=EvaluationOutput,
            )
        except Exception:
            await self._transitions.transition_run(
                TransitionContext(run_id, 3, actor, correlation_id), RunCommand.MARK_FAILED
            )
            await self._transitions.transition_work(
                TransitionContext(work_id, running_work_version, actor, correlation_id),
                WorkCommand.MARK_UNKNOWN,
            )
            raise
        artifact_bytes = json.dumps(
            execution.output.result, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
        stored = self._artifacts.put(
            goal.id, f"output/work-{work_id}-attempt-{attempt}.json", artifact_bytes
        )
        artifact_id, evidence_id = uuid.uuid4(), uuid.uuid4()
        total_input = execution.usage.input_tokens + evaluation.usage.input_tokens
        total_output = execution.usage.output_tokens + evaluation.usage.output_tokens
        async with self._sessions() as session, session.begin():
            run = await session.get(RunModel, run_id, with_for_update=True)
            if run is None:
                raise RuntimeError("run disappeared")
            run.result = execution.output.model_dump()
            run.model_ref = execution.model
            run.resource_usage = {
                "model_calls": 2,
                "input_tokens": total_input,
                "output_tokens": total_output,
            }
            session.add_all(
                (
                    ArtifactModel(
                        id=artifact_id,
                        goal_id=goal.id,
                        work_id=work_id,
                        run_id=run_id,
                        artifact_type="agent_result",
                        schema_ref="regent://schemas/agent-result/v1",
                        uri=stored.uri,
                        content_hash=stored.content_hash,
                        producer_ref="single-agent:v1",
                        provenance={"model": execution.model},
                        version=attempt,
                    ),
                    EvidenceModel(
                        id=evidence_id,
                        goal_id=goal.id,
                        work_id=work_id,
                        run_id=run_id,
                        artifact_id=artifact_id,
                        evidence_type="independent_model_evaluation",
                        uri=stored.uri,
                        content_hash=stored.content_hash,
                        producer_ref="evaluator:v1",
                        quality_tier="MODEL_REVIEW",
                        payload=evaluation.output.model_dump(),
                    ),
                )
            )
        await self._transitions.transition_run(
            TransitionContext(run_id, 3, actor, correlation_id), RunCommand.MARK_EXECUTED
        )
        await self._transitions.transition_work(
            TransitionContext(work_id, running_work_version, actor, correlation_id),
            WorkCommand.REQUEST_EVALUATION,
        )
        work_command = WorkCommand.ACCEPT if evaluation.output.accepted else WorkCommand.REJECT
        await self._transitions.transition_work(
            TransitionContext(work_id, running_work_version + 1, actor, correlation_id),
            work_command,
        )
        if evaluation.output.accepted:
            await self._achieve_if_complete(goal.id, actor, correlation_id)
        return await self._receipt(run_id)

    async def _achieve_if_complete(
        self, goal_id: uuid.UUID, actor: str, correlation_id: uuid.UUID
    ) -> None:
        async with self._sessions() as session:
            states = list(
                await session.scalars(select(WorkModel.status).where(WorkModel.goal_id == goal_id))
            )
            goal = await session.get(GoalModel, goal_id)
        if states and all(state == "ACCEPTED" for state in states) and goal is not None:
            await self._transitions.transition_goal(
                TransitionContext(goal_id, goal.version, actor, correlation_id), GoalCommand.ACHIEVE
            )

    async def _receipt(self, run_id: uuid.UUID) -> ExecutionReceipt:
        async with self._sessions() as session:
            run = await session.get(RunModel, run_id)
            if run is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"run {run_id} not found")
            work = await session.get(WorkModel, run.work_id)
            if work is None:
                raise RuntimeError("work disappeared")
            goal = await session.get(GoalModel, work.goal_id)
            evidence = await session.scalar(
                select(EvidenceModel).where(EvidenceModel.run_id == run_id)
            )
            artifact = await session.scalar(
                select(ArtifactModel).where(ArtifactModel.run_id == run_id)
            )
            if goal is None or evidence is None or artifact is None:
                raise RuntimeError("execution receipt is incomplete")
            usage = run.resource_usage
            return ExecutionReceipt(
                goal_id=goal.id,
                work_id=work.id,
                run_id=run.id,
                artifact_id=artifact.id,
                evidence_id=evidence.id,
                run_status=run.status,
                work_status=work.status,
                goal_status=goal.status,
                accepted=bool(evidence.payload["accepted"]),
                score=float(evidence.payload["score"]),
                model_calls=int(usage["model_calls"]),
                input_tokens=int(usage["input_tokens"]),
                output_tokens=int(usage["output_tokens"]),
            )
