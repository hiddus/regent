import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from regent.application.p1_contracts import canonical_hash
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import GoalModel, GoalSpecModel


@dataclass(frozen=True, slots=True)
class CreateGoal:
    original_input: str
    created_by: str
    explicit_constraints: dict[str, Any]
    success_criteria: dict[str, Any]
    metadata: dict[str, Any]
    system_inferences: dict[str, Any] | None = None
    unknowns: list[dict[str, Any]] | None = None
    app_project_id: uuid.UUID | None = None


class GoalService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(self, command: CreateGoal) -> GoalModel:
        goal_id = uuid.uuid4()
        goal = GoalModel(
            id=goal_id,
            original_input=command.original_input,
            app_project_id=command.app_project_id,
            created_by=command.created_by,
            correlation_id=uuid.uuid4(),
            metadata_json=command.metadata,
        )
        spec_content = {
            "explicit_constraints": command.explicit_constraints,
            "system_inferences": command.system_inferences or {},
            "unknowns": command.unknowns or [],
            "success_criteria": command.success_criteria,
            "source_refs": [],
        }
        spec = GoalSpecModel(
            id=uuid.uuid4(),
            goal_id=goal_id,
            version=1,
            status="DRAFT",
            content_hash=canonical_hash(spec_content),
            explicit_constraints=command.explicit_constraints,
            system_inferences=command.system_inferences or {},
            unknowns=command.unknowns or [],
            success_criteria=command.success_criteria,
            source_refs=[],
        )
        async with self._sessions() as session, session.begin():
            session.add_all((goal, spec))
        return goal

    async def get(self, goal_id: uuid.UUID) -> GoalModel:
        async with self._sessions() as session:
            result = await session.execute(
                select(GoalModel)
                .options(selectinload(GoalModel.specs), selectinload(GoalModel.works))
                .where(GoalModel.id == goal_id)
            )
            goal = result.scalar_one_or_none()
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"goal {goal_id} not found")
            return goal
