from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from regent.application.baseline_service import BaselineReceipt, CsvSummaryBaselineService
from regent.application.evt_gap_service import EvtGapReceipt, EvtParserGapService
from regent.config import get_settings
from regent.infrastructure.artifact_store import FileArtifactStore

router = APIRouter(prefix="/v1/baselines", tags=["baselines"])


class CsvSummaryRequest(BaseModel):
    csv_content: str = Field(min_length=1, max_length=1_000_000)
    idempotency_key: str = Field(min_length=8, max_length=255)
    actor: str = Field(min_length=1, max_length=255)


class EvtGapRequest(BaseModel):
    input_text: str = Field(min_length=1, max_length=1_000_000)
    idempotency_key: str = Field(min_length=8, max_length=255)
    actor: str = Field(min_length=1, max_length=255)


def baseline_service(request: Request) -> CsvSummaryBaselineService:
    return CsvSummaryBaselineService(
        request.app.state.sessions,
        FileArtifactStore(Path(get_settings().artifact_root)),
    )


BaselineServiceDep = Annotated[CsvSummaryBaselineService, Depends(baseline_service)]


@router.post("/csv-summary", response_model=BaselineReceipt, status_code=status.HTTP_201_CREATED)
async def csv_summary(payload: CsvSummaryRequest, service: BaselineServiceDep) -> BaselineReceipt:
    return await service.execute(**payload.model_dump())


@router.post("/evt-parser-gap", response_model=EvtGapReceipt, status_code=status.HTTP_201_CREATED)
async def evt_parser_gap(payload: EvtGapRequest, request: Request) -> EvtGapReceipt:
    settings = get_settings()
    return await EvtParserGapService(
        request.app.state.sessions,
        FileArtifactStore(Path(settings.artifact_root)),
    ).execute(**payload.model_dump())
