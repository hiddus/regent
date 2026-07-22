import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AgentSpecModel,
    AssignmentModel,
    CapabilityModel,
    GoalModel,
    OrganizationModel,
    WorkModel,
)


@dataclass(frozen=True, slots=True)
class OrganizationReceipt:
    organization_id: uuid.UUID
    goal_id: uuid.UUID
    strategy: str
    agent_spec_ids: list[uuid.UUID]
    required_capabilities: list[str]
    reused_capabilities: list[str]
    capability_gaps: list[str]
    assignment_count: int
    replayed: bool


class OrganizationService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def organize(self, goal_id: uuid.UUID) -> OrganizationReceipt:
        async with self._sessions() as session:
            goal = await session.get(GoalModel, goal_id)
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"goal {goal_id} not found")
            if goal.status not in {"READY", "ACTIVE"}:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    "goal must be confirmed before organization",
                )
            works = list(
                await session.scalars(select(WorkModel).where(WorkModel.goal_id == goal_id))
            )
            if not works:
                raise DomainError(
                    ErrorCode.INVALID_STATE, "goal must be planned before organization"
                )
            existing = await session.scalar(
                select(OrganizationModel).where(OrganizationModel.goal_id == goal_id)
            )
            if existing is not None:
                return await self._receipt(existing.id, replayed=True)
            required = sorted(
                {
                    str(capability)
                    for work in works
                    for capability in work.metadata_json.get("required_capabilities", [])
                }
            )
            verified = set(
                await session.scalars(
                    select(CapabilityModel.name).where(
                        CapabilityModel.name.in_(required),
                        CapabilityModel.status == "VERIFIED",
                        CapabilityModel.scope_goal_id.is_(None),
                    )
                )
            )

        gaps = sorted(set(required) - verified)
        organization_id, agent_spec_id = uuid.uuid4(), uuid.uuid4()
        async with self._sessions() as session, session.begin():
            session.add(
                AgentSpecModel(
                    id=agent_spec_id,
                    name="goal-single-agent",
                    version=1,
                    status="CANDIDATE" if gaps else "ACTIVE",
                    scope_goal_id=goal_id,
                    capability_names=required,
                    model_ref="configured-model",
                    tool_refs=[],
                    constraints={"goal_scope_only": True, "max_delegation_depth": 0},
                )
            )
            for gap in gaps:
                session.add(
                    CapabilityModel(
                        id=uuid.uuid4(),
                        name=gap,
                        status="CANDIDATE",
                        scope_goal_id=goal_id,
                        description=f"Goal-scoped candidate capability for {gap}",
                        verification={"required_tests": 1, "passed_tests": 0},
                    )
                )
            session.add(
                OrganizationModel(
                    id=organization_id,
                    goal_id=goal_id,
                    strategy="SINGLE_AGENT",
                    rationale=(
                        "Default to one goal-scoped agent; capability gaps remain candidates until "
                        "independently verified."
                    ),
                    status="ACTIVE",
                    max_agents=4,
                )
            )
            await session.flush()
            for work in works:
                session.add(
                    AssignmentModel(
                        id=uuid.uuid4(),
                        organization_id=organization_id,
                        work_id=work.id,
                        agent_spec_id=agent_spec_id,
                        role="executor",
                        delegated_capabilities=list(
                            work.metadata_json.get("required_capabilities", [])
                        ),
                    )
                )
        return await self._receipt(organization_id, replayed=False)

    async def _receipt(self, organization_id: uuid.UUID, *, replayed: bool) -> OrganizationReceipt:
        async with self._sessions() as session:
            organization = await session.get(OrganizationModel, organization_id)
            if organization is None:
                raise RuntimeError("organization disappeared")
            assignments = list(
                await session.scalars(
                    select(AssignmentModel).where(
                        AssignmentModel.organization_id == organization_id
                    )
                )
            )
            agent_ids = sorted({assignment.agent_spec_id for assignment in assignments}, key=str)
            agents = list(
                await session.scalars(
                    select(AgentSpecModel).where(AgentSpecModel.id.in_(agent_ids))
                )
            )
            required = sorted({cap for agent in agents for cap in agent.capability_names})
            candidates = set(
                await session.scalars(
                    select(CapabilityModel.name).where(
                        CapabilityModel.scope_goal_id == organization.goal_id,
                        CapabilityModel.status == "CANDIDATE",
                    )
                )
            )
            return OrganizationReceipt(
                organization_id=organization.id,
                goal_id=organization.goal_id,
                strategy=organization.strategy,
                agent_spec_ids=agent_ids,
                required_capabilities=required,
                reused_capabilities=sorted(set(required) - candidates),
                capability_gaps=sorted(candidates),
                assignment_count=len(assignments),
                replayed=replayed,
            )
