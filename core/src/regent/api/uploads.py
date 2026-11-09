"""File upload endpoint for user-supplied artifacts."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

router = APIRouter(prefix="/v1/uploads", tags=["uploads"])


class UploadResponse(BaseModel):
    id: str
    filename: str
    size: int
    content_type: str
    sha256: str


@router.post("", response_model=UploadResponse, status_code=201)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    actor: str = Form(default="anonymous"),
) -> UploadResponse:
    """Upload a file for use in goal execution or generation."""
    settings = request.app.state.sessions  # type: ignore[attr-defined]

    # Read file content
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:  # 50MB limit
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")

    sha256 = hashlib.sha256(content).hexdigest()
    file_id = uuid.uuid4().hex[:12]

    # Determine upload directory
    from regent.config import get_settings
    cfg = get_settings()
    upload_dir = Path(cfg.workspace_root) / "uploads"
    if project_id:
        upload_dir = upload_dir / project_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Save file
    safe_name = f"{file_id}_{file.filename or 'upload'}"
    dest = upload_dir / safe_name
    dest.write_bytes(content)

    # Store metadata in conversation_messages if project is bound
    if project_id:
        try:
            from sqlalchemy import text
            async with settings() as session, session.begin():
                # Find conversation for this project
                conv_id = await session.scalar(
                    text(
                        "SELECT id FROM conversations "
                        "WHERE app_project_id = :pid ORDER BY updated_at DESC LIMIT 1"
                    ),
                    {"pid": project_id},
                )
                if conv_id:
                    next_ord = await session.scalar(
                        text(
                            "SELECT COALESCE(MAX(ordinal), 0) + 1 "
                            "FROM conversation_messages "
                            "WHERE conversation_id = :cid"
                        ),
                        {"cid": str(conv_id)},
                    )
                    await session.execute(
                        text(
                            "INSERT INTO conversation_messages "
                            "(id, conversation_id, ordinal, role, message_type, content, "
                            "metadata, created_by) "
                            "VALUES (:id, :cid, :ord, 'USER', 'FILE_UPLOADED', :content, "
                            ":meta, :actor)"
                        ),
                        {
                            "id": str(uuid.uuid4()),
                            "cid": str(conv_id),
                            "ord": next_ord,
                            "content": f"Uploaded file: {file.filename}",
                            "meta": {
                                "file_id": file_id,
                                "filename": file.filename,
                                "size": len(content),
                                "sha256": sha256,
                                "path": str(dest),
                            },
                            "actor": actor,
                        },
                    )
        except Exception:
            pass  # Non-critical: file is saved even if metadata insert fails

    return UploadResponse(
        id=file_id,
        filename=file.filename or "upload",
        size=len(content),
        content_type=file.content_type or "application/octet-stream",
        sha256=sha256,
    )
