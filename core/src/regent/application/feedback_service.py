import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.p1_contracts import canonical_hash
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    DeploymentModel,
    GateEvaluationModel,
    GoalModel,
    IterationDecisionModel,
    MetricDefinitionBindingModel,
    ObservationModel,
    WorkModel,
)


class Aggregation(StrEnum):
    COUNT = "COUNT"
    SUM = "SUM"
    AVERAGE = "AVERAGE"


class Comparison(StrEnum):
    GTE = "GTE"
    LTE = "LTE"


class MetricDefinition(BaseModel):
    metric_key: str = Field(min_length=1, max_length=255)
    definition_version: str = Field(min_length=1, max_length=128)
    observation_source: str = Field(min_length=1, max_length=255)
    value_field: str = Field(default="value", min_length=1, max_length=120)
    aggregation: Aggregation
    comparison: Comparison
    threshold: float
    minimum_samples: int = Field(ge=1)
    exclude_bots: bool = True
    exclude_internal: bool = True


@dataclass(frozen=True, slots=True)
class BindMetricDefinition:
    goal_id: uuid.UUID
    deployment_id: uuid.UUID
    definition: MetricDefinition
    actor: str


@dataclass(frozen=True, slots=True)
class CreateIterationDecision:
    gate_evaluation_id: uuid.UUID
    actor: str
    primary_hypothesis: str | None = None
    new_work_id: uuid.UUID | None = None


class FeedbackService:
    POLICY_VERSION = "iteration-gate-v1"

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def bind_metric(self, command: BindMetricDefinition) -> MetricDefinitionBindingModel:
        content = command.definition.model_dump(mode="json")
        digest = canonical_hash(content)
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(MetricDefinitionBindingModel).where(
                    MetricDefinitionBindingModel.goal_id == command.goal_id,
                    MetricDefinitionBindingModel.metric_key == command.definition.metric_key,
                    MetricDefinitionBindingModel.definition_version
                    == command.definition.definition_version,
                )
            )
            if existing is not None:
                if existing.definition_hash != digest:
                    raise DomainError(
                        ErrorCode.INVALID_STATE,
                        "metric definition version is immutable",
                    )
                return existing
            if await session.get(GoalModel, command.goal_id) is None:
                raise DomainError(ErrorCode.NOT_FOUND, "goal not found")
            deployment = await session.get(DeploymentModel, command.deployment_id)
            if deployment is None or deployment.status != "SUCCEEDED":
                raise DomainError(
                    ErrorCode.INVALID_STATE, "successful preview deployment is required"
                )
            model = MetricDefinitionBindingModel(
                id=uuid.uuid4(),
                goal_id=command.goal_id,
                deployment_id=deployment.id,
                metric_key=command.definition.metric_key,
                definition_version=command.definition.definition_version,
                definition_json=content,
                definition_hash=digest,
                created_by=command.actor,
            )
            session.add(model)
            await session.flush()
            return model

    async def evaluate(
        self, goal_id: uuid.UUID, deployment_id: uuid.UUID, *, actor: str
    ) -> GateEvaluationModel:
        async with self._sessions() as session, session.begin():
            bindings = list(
                await session.scalars(
                    select(MetricDefinitionBindingModel).where(
                        MetricDefinitionBindingModel.goal_id == goal_id,
                        MetricDefinitionBindingModel.deployment_id == deployment_id,
                    )
                )
            )
            if not bindings:
                raise DomainError(ErrorCode.INVALID_STATE, "no metric definitions are bound")
            results: list[dict[str, Any]] = []
            observation_ids: list[uuid.UUID] = []
            for binding in sorted(bindings, key=lambda item: item.metric_key):
                definition = MetricDefinition.model_validate(binding.definition_json)
                statement = select(ObservationModel).where(
                    ObservationModel.goal_id == goal_id,
                    ObservationModel.metric_name == definition.metric_key,
                    ObservationModel.definition_version == definition.definition_version,
                    ObservationModel.source == definition.observation_source,
                )
                if definition.exclude_bots:
                    statement = statement.where(ObservationModel.is_bot.is_(False))
                if definition.exclude_internal:
                    statement = statement.where(ObservationModel.is_internal.is_(False))
                observations = list(await session.scalars(statement))
                observation_ids.extend(item.id for item in observations)
                values = (
                    [1.0 for _item in observations]
                    if definition.aggregation is Aggregation.COUNT
                    else [
                        self._numeric(item.metric_value, definition.value_field)
                        for item in observations
                    ]
                )
                enough = len(values) >= definition.minimum_samples
                aggregate = self._aggregate(values, definition.aggregation) if values else None
                passed = (
                    enough
                    and aggregate is not None
                    and self._compare(aggregate, definition.threshold, definition.comparison)
                )
                results.append(
                    {
                        "metric_key": definition.metric_key,
                        "definition_version": definition.definition_version,
                        "sample_count": len(values),
                        "minimum_samples": definition.minimum_samples,
                        "aggregation": definition.aggregation.value,
                        "aggregate": aggregate,
                        "comparison": definition.comparison.value,
                        "threshold": definition.threshold,
                        "enough_evidence": enough,
                        "passed": passed,
                    }
                )
            if any(not item["enough_evidence"] for item in results):
                status = "INSUFFICIENT_EVIDENCE"
            elif all(item["passed"] for item in results):
                status = "PASSED"
            else:
                status = "FAILED"
            evidence_payload = {
                "bindings": [item.definition_hash for item in bindings],
                "observations": sorted(str(value) for value in observation_ids),
                "results": results,
            }
            input_digest = canonical_hash(
                {"goal_id": str(goal_id), "deployment_id": str(deployment_id), **evidence_payload}
            )
            existing = await session.scalar(
                select(GateEvaluationModel).where(GateEvaluationModel.input_digest == input_digest)
            )
            if existing is not None:
                return existing
            model = GateEvaluationModel(
                id=uuid.uuid4(),
                goal_id=goal_id,
                deployment_id=deployment_id,
                status=status,
                input_digest=input_digest,
                policy_version=self.POLICY_VERSION,
                result_json={"metrics": results},
                observation_ids=[str(value) for value in observation_ids],
                evidence_digest=canonical_hash(evidence_payload),
                created_by=actor,
            )
            session.add(model)
            await session.flush()
            return model

    async def get_gate(self, gate_id: uuid.UUID) -> GateEvaluationModel:
        async with self._sessions() as session:
            model = await session.get(GateEvaluationModel, gate_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "gate evaluation not found")
            return model

    async def list_decisions(self, goal_id: uuid.UUID) -> list[IterationDecisionModel]:
        async with self._sessions() as session:
            if await session.get(GoalModel, goal_id) is None:
                raise DomainError(ErrorCode.NOT_FOUND, "goal not found")
            return list(
                await session.scalars(
                    select(IterationDecisionModel)
                    .where(IterationDecisionModel.goal_id == goal_id)
                    .order_by(IterationDecisionModel.created_at.desc())
                )
            )

    async def decide(self, command: CreateIterationDecision) -> IterationDecisionModel:
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(IterationDecisionModel).where(
                    IterationDecisionModel.gate_evaluation_id == command.gate_evaluation_id
                )
            )
            if existing is not None:
                return existing
            gate = await session.get(GateEvaluationModel, command.gate_evaluation_id)
            if gate is None:
                raise DomainError(ErrorCode.NOT_FOUND, "gate evaluation not found")
            if gate.status == "PASSED":
                decision = "CONTINUE"
                rationale = "all frozen metric gates passed"
            elif command.primary_hypothesis and command.new_work_id:
                work = await session.get(WorkModel, command.new_work_id)
                if work is None or work.goal_id != gate.goal_id:
                    raise DomainError(ErrorCode.INVALID_STATE, "revision work must belong to goal")
                decision = "REVISE"
                rationale = "metric evidence requires a revision of the primary hypothesis"
            elif gate.status == "FAILED":
                decision = "STOP"
                rationale = "one or more frozen metric gates failed"
            else:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    "insufficient evidence requires a primary hypothesis and new work",
                )
            model = IterationDecisionModel(
                id=uuid.uuid4(),
                goal_id=gate.goal_id,
                gate_evaluation_id=gate.id,
                decision=decision,
                rationale=rationale,
                primary_hypothesis=(command.primary_hypothesis if decision == "REVISE" else None),
                new_work_id=command.new_work_id if decision == "REVISE" else None,
                evidence_digest=gate.evidence_digest,
                policy_version=self.POLICY_VERSION,
                created_by=command.actor,
            )
            session.add(model)
            await session.flush()
            return model

    @staticmethod
    def _numeric(value: dict[str, Any], field: str) -> float:
        raw = value.get(field)
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise DomainError(ErrorCode.INVALID_STATE, f"metric field {field} must be numeric")
        return float(raw)

    @staticmethod
    def _aggregate(values: list[float], aggregation: Aggregation) -> float:
        if aggregation is Aggregation.COUNT:
            return float(len(values))
        if aggregation is Aggregation.SUM:
            return sum(values)
        return sum(values) / len(values)

    @staticmethod
    def _compare(value: float, threshold: float, comparison: Comparison) -> bool:
        return value >= threshold if comparison is Comparison.GTE else value <= threshold
