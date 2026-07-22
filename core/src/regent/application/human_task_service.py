import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import HumanTaskModel


class HumanTaskService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self,
        *,
        goal_id: uuid.UUID,
        work_id: uuid.UUID | None,
        run_id: uuid.UUID | None,
        task_type: str,
        prompt: str,
        requested_by: str,
        due_at: datetime,
    ) -> uuid.UUID:
        if due_at.tzinfo is None:
            raise ValueError("due_at must be timezone-aware")
        task_id = uuid.uuid4()
        async with self._sessions() as session, session.begin():
            session.add(
                HumanTaskModel(
                    id=task_id,
                    goal_id=goal_id,
                    work_id=work_id,
                    run_id=run_id,
                    task_type=task_type,
                    prompt=prompt,
                    requested_by=requested_by,
                    due_at=due_at,
                    status="OPEN",
                )
            )
        return task_id

    async def complete(
        self,
        task_id: uuid.UUID,
        *,
        assigned_to: str,
        response: dict[str, Any],
    ) -> None:
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(HumanTaskModel)
                    .where(
                        HumanTaskModel.id == task_id,
                        HumanTaskModel.status == "OPEN",
                        HumanTaskModel.due_at > func.now(),
                    )
                    .values(
                        status="COMPLETED",
                        assigned_to=assigned_to,
                        response=response,
                        completed_at=func.now(),
                    )
                ),
            )
            if result.rowcount != 1:
                raise DomainError(ErrorCode.INVALID_STATE, "human task is unavailable or expired")

    async def timeout_due(self) -> int:
        async with self._sessions() as session, session.begin():
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(HumanTaskModel)
                    .where(HumanTaskModel.status == "OPEN", HumanTaskModel.due_at <= func.now())
                    .values(status="TIMED_OUT")
                ),
            )
            return result.rowcount
