import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import GoalModel, WorkModel
from regent.model import ModelProvider, ModelUsage


class WorkProposal(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=1)
    acceptance_criteria: dict[str, Any] = Field(default_factory=dict)
    dependency_keys: list[str] = Field(default_factory=list)
    priority: int = Field(default=0, ge=-100, le=100)
    budget: dict[str, int | float] = Field(default_factory=dict)
    required_capabilities: list[str] = Field(default_factory=list)


class PlanProposal(BaseModel):
    rationale: str = Field(min_length=1)
    works: list[WorkProposal] = Field(min_length=1, max_length=8)


@dataclass(frozen=True, slots=True)
class PlannedWork:
    id: uuid.UUID
    key: str
    purpose: str
    dependencies: list[str]
    required_capabilities: list[str]


@dataclass(frozen=True, slots=True)
class PlanReceipt:
    goal_id: uuid.UUID
    rationale: str
    works: list[PlannedWork]
    model: str
    usage: ModelUsage
    replayed: bool


class PlanningService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: ModelProvider,
    ) -> None:
        self._sessions = sessions
        self._provider = provider

    async def plan(self, goal_id: uuid.UUID) -> PlanReceipt:
        async with self._sessions() as session:
            goal = await session.scalar(
                select(GoalModel)
                .options(selectinload(GoalModel.specs), selectinload(GoalModel.works))
                .where(GoalModel.id == goal_id)
            )
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"goal {goal_id} not found")
            if goal.status not in {"READY", "ACTIVE"}:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    "goal must be confirmed before planning",
                )
            if goal.works:
                return PlanReceipt(
                    goal_id=goal.id,
                    rationale=str(goal.metadata_json.get("plan_rationale", "existing plan")),
                    works=[self._to_planned(work) for work in goal.works],
                    model=str(goal.metadata_json.get("planning_model", "unknown")),
                    usage=ModelUsage(0, 0),
                    replayed=True,
                )
            spec = max(goal.specs, key=lambda item: item.version)
            prompt = {
                "original_goal": goal.original_input,
                "explicit_constraints": spec.explicit_constraints,
                "system_inferences": spec.system_inferences,
                "unknowns": spec.unknowns,
                "success_criteria": spec.success_criteria,
            }

        response = await self._provider.generate_structured(
            system_prompt=(
                "Create the smallest executable work graph. Do not add permissions. "
                "Dependencies must reference work keys in this plan. Include bounded budgets "
                "and explicit required capabilities. Return only the required structure."
            ),
            user_prompt=str(prompt),
            response_model=PlanProposal,
        )
        proposal = response.output
        keys = [work.key for work in proposal.works]
        if len(keys) != len(set(keys)):
            raise ValueError("planner returned duplicate work keys")
        known = set(keys)
        if any(set(work.dependency_keys) - known for work in proposal.works):
            raise ValueError("planner returned an unknown dependency key")

        key_to_id = {key: uuid.uuid4() for key in keys}
        works = [
            WorkModel(
                id=key_to_id[item.key],
                goal_id=goal_id,
                purpose=item.purpose,
                input_refs=[],
                acceptance_criteria=item.acceptance_criteria,
                dependency_ids=[str(key_to_id[key]) for key in item.dependency_keys],
                priority=item.priority,
                budget=item.budget,
                correlation_id=goal.correlation_id,
                metadata_json={
                    "plan_key": item.key,
                    "required_capabilities": item.required_capabilities,
                },
            )
            for item in proposal.works
        ]
        async with self._sessions() as session, session.begin():
            locked = await session.get(GoalModel, goal_id, with_for_update=True)
            if locked is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"goal {goal_id} not found")
            existing = await session.scalar(
                select(WorkModel.id).where(WorkModel.goal_id == goal_id)
            )
            if existing is not None:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "goal was already planned")
            locked.metadata_json = {
                **locked.metadata_json,
                "plan_rationale": proposal.rationale,
                "planning_model": response.model,
            }
            session.add_all(works)
        return PlanReceipt(
            goal_id=goal_id,
            rationale=proposal.rationale,
            works=[self._to_planned(work) for work in works],
            model=response.model,
            usage=response.usage,
            replayed=False,
        )

    @staticmethod
    def _to_planned(work: WorkModel) -> PlannedWork:
        return PlannedWork(
            id=work.id,
            key=str(work.metadata_json.get("plan_key", work.id)),
            purpose=work.purpose,
            dependencies=work.dependency_ids,
            required_capabilities=list(work.metadata_json.get("required_capabilities", [])),
        )
