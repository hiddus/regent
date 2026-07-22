import uuid
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import CapabilityModel, ToolSpecModel


class ToolGovernanceService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def revoke(self, tool_id: uuid.UUID, reason: str) -> None:
        async with self._sessions() as session, session.begin():
            tool = await session.get(ToolSpecModel, tool_id, with_for_update=True)
            if tool is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"tool {tool_id} not found")
            if tool.status != "CERTIFIED":
                raise DomainError(ErrorCode.INVALID_STATE, "only a certified tool can be revoked")
            tool.status = "REVOKED"
            tool.constraints = {**tool.constraints, "revocation_reason": reason}
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(CapabilityModel)
                    .where(
                        CapabilityModel.name == tool.capability_name,
                        CapabilityModel.scope_goal_id == tool.scope_goal_id,
                        CapabilityModel.status == "GOAL_CERTIFIED",
                    )
                    .values(
                        status="REVOKED",
                        verification={"revoked": True, "reason": reason},
                    )
                ),
            )
            if result.rowcount != 1:
                raise DomainError(ErrorCode.INVALID_STATE, "certified capability was not found")
