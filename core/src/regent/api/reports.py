"""API endpoints for multi-format report and spreadsheet generation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.report_generators import (
    MultiFormatArtifactPublisher,
    ReportPayload,
    ReportSection,
    SpreadsheetGenerator,
)

router = APIRouter(prefix="/v1/reports", tags=["reports"])


class ReportSectionBody(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(default="")
    tables: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateReportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    subtitle: str = Field(default="", max_length=500)
    author: str = Field(default="Regent Core", max_length=200)
    sections: list[ReportSectionBody] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    formats: list[str] = Field(default_factory=lambda: ["markdown", "html"])


class CreateSpreadsheetRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    columns: list[str] = Field(min_length=1)
    rows: list[list[Any]] = Field(default_factory=list)
    format: str = Field(default="csv", pattern=r"^(csv|html)$")


class ArtifactResponse(BaseModel):
    filename: str
    media_type: str
    content_hash: str
    size: int


def _get_artifact_store(request: Request) -> FileArtifactStore:
    from regent.config import get_settings
    from pathlib import Path
    settings = get_settings()
    return FileArtifactStore(Path(settings.artifact_root))


@router.post("", response_model=list[ArtifactResponse], status_code=201)
async def create_report(
    payload: CreateReportRequest, request: Request
) -> list[ArtifactResponse]:
    """Generate a report in one or more formats."""
    sections = [
        ReportSection(
            title=s.title,
            body=s.body,
            tables=s.tables,
            metadata=s.metadata,
        )
        for s in payload.sections
    ]
    report = ReportPayload(
        title=payload.title,
        subtitle=payload.subtitle,
        author=payload.author,
        sections=sections,
        metadata=payload.metadata,
    )
    store = _get_artifact_store(request)
    publisher = MultiFormatArtifactPublisher(store)
    artifacts = publisher.publish_report(report, formats=payload.formats)
    return [
        ArtifactResponse(
            filename=a.filename,
            media_type=a.media_type,
            content_hash=a.content_hash,
            size=a.size,
        )
        for a in artifacts
    ]


@router.post("/spreadsheet", response_model=ArtifactResponse, status_code=201)
async def create_spreadsheet(
    payload: CreateSpreadsheetRequest, request: Request
) -> ArtifactResponse:
    """Generate a CSV or HTML spreadsheet."""
    store = _get_artifact_store(request)
    gen = SpreadsheetGenerator()
    data = {"title": payload.title, "columns": payload.columns, "rows": payload.rows}
    if payload.format == "html":
        artifact = gen.generate_html_table(data)
    else:
        artifact = gen.generate_csv(data)
    # Store artifact
    import uuid as _uuid
    scope = _uuid.uuid5(_uuid.NAMESPACE_URL, f"regent:api-spreadsheet:{payload.title}")
    store.put(scope, f"spreadsheets/{artifact.filename}", artifact.content)
    return ArtifactResponse(
        filename=artifact.filename,
        media_type=artifact.media_type,
        content_hash=artifact.content_hash,
        size=artifact.size,
    )


@router.get("/{filename}")
async def download_report(filename: str, request: Request) -> Response:
    """Download a previously generated report by filename."""
    if "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise HTTPException(status_code=404, detail="Report not found")
    store = _get_artifact_store(request)
    for prefix in ("reports", "spreadsheets"):
        content = store.find(f"{prefix}/{filename}")
        if content is None:
            continue
        media_type = "application/octet-stream"
        if filename.endswith(".md"):
            media_type = "text/markdown"
        elif filename.endswith(".html"):
            media_type = "text/html"
        elif filename.endswith(".csv"):
            media_type = "text/csv"
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    raise HTTPException(status_code=404, detail="Report not found")
