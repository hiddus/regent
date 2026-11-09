import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.tool_governance import ToolGovernanceService

router = APIRouter(prefix="/v1/tools", tags=["tools"])


class RevokeToolRequest(BaseModel):
    reason: str = Field(min_length=1)


def tool_service(request: Request) -> ToolGovernanceService:
    return ToolGovernanceService(request.app.state.sessions)


ToolDep = Annotated[ToolGovernanceService, Depends(tool_service)]


@router.post("/{tool_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_tool(tool_id: uuid.UUID, payload: RevokeToolRequest, service: ToolDep) -> None:
    await service.revoke(tool_id, payload.reason)
