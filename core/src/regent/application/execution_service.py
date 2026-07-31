import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.aar1_contract import is_certified_hive_topology
from regent.application.hive_skill_seed import HiveSkillSeedService
from regent.application.hive_runtime import (
    HiveRoleBinding,
    agent_spec_ref_for_role,
    offer_hive_task_chain,
)
from regent.application.transition_service import TransitionContext, TransitionService
from regent.domain.errors import DomainError, ErrorCode
from regent.domain.transitions import GoalCommand, RunCommand, WorkCommand
from regent.infrastructure.aar1_models import (
    AgentDeploymentModel,
    OrganizationVersionModel,
)
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.models import (
    ArtifactModel,
    EvidenceModel,
    GoalModel,
    OrganizationModel,
    RunModel,
    WorkModel,
)
from regent.model import ModelProvider

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True, slots=True)
class _TopologyContext:
    template_id: str
    strategy: str
    topology: dict[str, Any]
    organization_version_id: uuid.UUID | None
    agent_spec_ref: str
    producer_ref: str
    evaluator_ref: str
    hive_bindings: dict[str, HiveRoleBinding] | None


class SingleAgentExecutionService:
    """Work execution service.

    Name is historical: when the active OrganizationVersion is the certified
    hive template, execution offers durable PM→Dev→QA AgentTasks and stamps
    role-specific producer/reviewer refs. Default remains single-agent champion.
    """

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
            topo = await self._load_topology_context(session, goal_id=goal.id)

        if goal.status == "DRAFT":
            qualified = await self._transitions.transition_goal(
                TransitionContext(goal.id, goal.version, actor, correlation_id),
                GoalCommand.QUALIFY,
            )
            await self._transitions.transition_goal(
                TransitionContext(goal.id, qualified.version, actor, correlation_id),
                GoalCommand.ACTIVATE,
            )
        elif goal.status == "READY":
            await self._transitions.transition_goal(
                TransitionContext(goal.id, goal.version, actor, correlation_id),
                GoalCommand.ACTIVATE,
            )
        elif goal.status != "ACTIVE":
            raise DomainError(ErrorCode.INVALID_STATE, "goal is not executable")
        # Seed built-in SKILLS on first activation
        try:
            await HiveSkillSeedService(self._sessions).seed_goal_skills(goal.id)
        except Exception:
            logger.warning("skill seeding failed (non-fatal)", extra={"goal_id": str(goal.id)}, exc_info=True)
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
                    agent_spec_ref=topo.agent_spec_ref,
                    model_ref="configured-model",
                    input_version="goal-spec:v1",
                    idempotency_key=f"{topo.template_id}:{work_id}:{attempt}",
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

        hive_chain = None
        tasks = None
        pm_plan = None
        pm_notes = None
        try:
            if (
                topo.hive_bindings is not None
                and topo.organization_version_id is not None
                and is_certified_hive_topology(topo.topology)
            ):
                from regent.application.agent_task_service import AgentTaskService

                tasks = AgentTaskService(self._sessions)
                async with self._sessions() as session, session.begin():
                    hive_chain = await offer_hive_task_chain(
                        tasks,
                        goal_id=goal.id,
                        work_id=work_id,
                        organization_version_id=topo.organization_version_id,
                        bindings=topo.hive_bindings,
                        correlation_id=str(correlation_id),
                        attempt=attempt,
                        session=session,
                    )

            if hive_chain is not None and tasks is not None and hive_chain.pm_task is not None:
                pm_lease = await tasks.claim_task(
                    hive_chain.pm_task.id, worker_id=f"{actor}:pm"
                )
                await tasks.start_task(
                    hive_chain.pm_task.id, lease_token=pm_lease.lease_token or ""
                )
                pm_plan = await self._provider.generate_structured(
                    system_prompt=(
                        "You are the PM agent in a certified Regent hive. Produce a short "
                        "execution plan and acceptance focus for the Dev agent. Do not write "
                        "product code. Stay within the work scope."
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
                pm_notes = pm_plan.output.model_dump()
                await tasks.complete_task(
                    hive_chain.pm_task.id,
                    lease_token=pm_lease.lease_token or "",
                    result_ref=f"run:{run_id}:pm",
                )

            if hive_chain is not None and tasks is not None:
                dev_lease = await tasks.claim_task(
                    hive_chain.dev_task.id, worker_id=f"{actor}:dev"
                )
                await tasks.start_task(
                    hive_chain.dev_task.id, lease_token=dev_lease.lease_token or ""
                )

            execution = await self._provider.generate_structured(
                system_prompt=(
                    "Execute only the described logical work. Do not claim external side effects, "
                    "network access, files, credentials, or tools you were not given. Return a "
                    "structured candidate result and explicit evidence claims."
                    + (
                        " You are the Dev agent in a certified hive; follow the PM plan when present."
                        if hive_chain is not None
                        else ""
                    )
                ),
                user_prompt=json.dumps(
                    {
                        "goal": goal.original_input,
                        "work": work.purpose,
                        "acceptance_criteria": work.acceptance_criteria,
                        "constraints": goal.metadata_json,
                        "pm_plan": pm_notes,
                    },
                    ensure_ascii=False,
                ),
                response_model=ExecutionOutput,
            )
            if hive_chain is not None and tasks is not None:
                await tasks.complete_task(
                    hive_chain.dev_task.id,
                    lease_token=dev_lease.lease_token or "",
                    result_ref=f"run:{run_id}:dev",
                )
                qa_lease = await tasks.claim_task(
                    hive_chain.qa_task.id, worker_id=f"{actor}:qa"
                )
                await tasks.start_task(
                    hive_chain.qa_task.id, lease_token=qa_lease.lease_token or ""
                )

            evaluation = await self._provider.generate_structured(
                system_prompt=(
                    "Act as an independent evaluator. Judge only against the supplied acceptance "
                    "criteria and candidate result. Reject unsupported claims. Return a score from "
                    "0.0 to 1.0 and criterion-level reasons."
                    + (
                        " You are the independent QA agent; you must not be the producer."
                        if hive_chain is not None
                        else ""
                    )
                ),
                user_prompt=json.dumps(
                    {
                        "acceptance_criteria": work.acceptance_criteria,
                        "candidate": execution.output.model_dump(),
                        "pm_plan": pm_notes,
                    },
                    ensure_ascii=False,
                ),
                response_model=EvaluationOutput,
            )
            if hive_chain is not None and tasks is not None:
                await tasks.complete_task(
                    hive_chain.qa_task.id,
                    lease_token=qa_lease.lease_token or "",
                    result_ref=f"run:{run_id}:qa",
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
        if pm_plan is not None:
            total_input += pm_plan.usage.input_tokens
            total_output += pm_plan.usage.output_tokens
            model_calls = 3
        else:
            model_calls = 2
        producer_ref = (
            hive_chain.producer_ref if hive_chain is not None else topo.producer_ref
        )
        evaluator_ref = (
            hive_chain.reviewer_ref if hive_chain is not None else topo.evaluator_ref
        )
        async with self._sessions() as session, session.begin():
            run = await session.get(RunModel, run_id, with_for_update=True)
            if run is None:
                raise RuntimeError("run disappeared")
            run.result = execution.output.model_dump()
            run.model_ref = execution.model
            run.resource_usage = {
                "model_calls": model_calls,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "template_id": topo.template_id,
                "strategy": topo.strategy,
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
                        producer_ref=producer_ref,
                        provenance={"model": execution.model, "template_id": topo.template_id},
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
                        producer_ref=evaluator_ref,
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

    async def execute_parallel(
        self, work_ids: list[uuid.UUID], *, actor: str
    ) -> list[ExecutionReceipt]:
        """Execute multiple work items concurrently through the hive.

        Each work item's internal PM->Dev->QA chain remains sequential,
        but different work items run in parallel — true multi-agent.
        """
        if not work_ids:
            return []
        logger.info(
            "parallel hive dispatch",
            extra={"work_count": len(work_ids), "actor": actor},
        )
        results = await asyncio.gather(
            *(self.execute(wid, actor=actor) for wid in work_ids),
            return_exceptions=True,
        )
        receipts: list[ExecutionReceipt] = []
        for wid, result in zip(work_ids, results):
            if isinstance(result, Exception):
                logger.error(
                    "parallel work failed",
                    extra={"work_id": str(wid), "error": str(result)},
                )
                raise result
            receipts.append(result)
        return receipts

    async def _load_topology_context(
        self, session: AsyncSession, *, goal_id: uuid.UUID
    ) -> _TopologyContext:
        org = await session.scalar(
            select(OrganizationModel).where(OrganizationModel.goal_id == goal_id)
        )
        if org is None or org.current_version_id is None:
            return _TopologyContext(
                template_id="single-agent-v1",
                strategy="SINGLE_AGENT",
                topology={
                    "template_id": "single-agent-v1",
                    "strategy": "SINGLE_AGENT",
                    "roles": [{"role": "executor", "capabilities": []}],
                },
                organization_version_id=None,
                agent_spec_ref="single-agent:v1",
                producer_ref="single-agent:v1",
                evaluator_ref="evaluator:v1",
                hive_bindings=None,
            )
        version = await session.get(OrganizationVersionModel, org.current_version_id)
        topology = dict((version.topology_json if version else {}) or {})
        template_id = str(topology.get("template_id") or "single-agent-v1")
        strategy = str(topology.get("strategy") or "SINGLE_AGENT")
        if not is_certified_hive_topology(topology):
            return _TopologyContext(
                template_id=template_id,
                strategy=strategy,
                topology=topology,
                organization_version_id=org.current_version_id,
                agent_spec_ref=f"{template_id}:executor",
                producer_ref=f"{template_id}:executor",
                evaluator_ref="evaluator:v1",
                hive_bindings=None,
            )

        deps = list(
            await session.scalars(
                select(AgentDeploymentModel).where(
                    AgentDeploymentModel.organization_version_id == org.current_version_id,
                    AgentDeploymentModel.status == "OPERATING",
                )
            )
        )
        bindings: dict[str, HiveRoleBinding] = {}
        for dep in deps:
            bindings[dep.role] = HiveRoleBinding(
                role=dep.role,
                agent_spec_id=uuid.uuid4(),  # assignment lookup not required for tasks
                deployment_id=dep.id,
                capabilities=list((dep.effective_permissions_json or {}).get("allow") or []),
            )
        producer_role = "dev" if "dev" in bindings else "executor"
        return _TopologyContext(
            template_id=template_id,
            strategy=strategy,
            topology=topology,
            organization_version_id=org.current_version_id,
            agent_spec_ref=agent_spec_ref_for_role(producer_role, template_id),
            producer_ref=agent_spec_ref_for_role(producer_role, template_id),
            evaluator_ref=agent_spec_ref_for_role("qa", template_id),
            hive_bindings=bindings if bindings else None,
        )

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
                # Internal invariant violation (not a delivery-recoverable gap): the
                # run's goal/evidence/artifact lineage is corrupt. Crash loudly so the
                # caller retries/investigates rather than silently degrading receipts.
                raise RuntimeError("execution receipt is missing required lineage records")
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
