import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from regent.application.execution_service import ExecutionReceipt, SingleAgentExecutionService
from regent.config import get_settings
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.model.factory import build_model_provider

router = APIRouter(prefix="/v1/works", tags=["works"])


class ExecuteRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=255)


class ParallelExecuteRequest(BaseModel):
    work_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    actor: str = Field(min_length=1, max_length=255)


def execution_service(request: Request) -> SingleAgentExecutionService:
    settings = get_settings()
    return SingleAgentExecutionService(
        request.app.state.sessions,
        build_model_provider(settings),
        FileArtifactStore(Path(settings.artifact_root)),
    )


ExecutionDep = Annotated[SingleAgentExecutionService, Depends(execution_service)]


@router.post("/{work_id}/execute", response_model=ExecutionReceipt)
async def execute_work(
    work_id: uuid.UUID, payload: ExecuteRequest, service: ExecutionDep
) -> ExecutionReceipt:
    return await service.execute(work_id, actor=payload.actor)


@router.post("/execute-parallel", response_model=list[ExecutionReceipt])
async def execute_works_parallel(
    payload: ParallelExecuteRequest, service: ExecutionDep
) -> list[ExecutionReceipt]:
    """Execute multiple work items concurrently via the hive.

    Each work item's PM->Dev->QA chain runs internally sequential,
    but different work items execute in parallel — true multi-agent.
    """
    return await service.execute_parallel(payload.work_ids, actor=payload.actor)
