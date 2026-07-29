"""Server-Sent Events endpoint for real-time console updates."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import text

router = APIRouter(prefix="/events", tags=["events"])


async def _poll_changes(
    sessions_factory: Any,
    project_id: uuid.UUID | None,
    last_message_ordinal: int,
) -> list[dict[str, Any]]:
    """Poll for new messages and status changes."""
    events: list[dict[str, Any]] = []
    try:
        async with sessions_factory() as session:
            # Check for new messages in conversations bound to this project
            if project_id:
                # Get conversation for this project
                conv_row = await session.execute(
                    text(
                        "SELECT id FROM conversations "
                        "WHERE app_project_id = :pid ORDER BY updated_at DESC LIMIT 1"
                    ),
                    {"pid": str(project_id)},
                )
                conv_id = conv_row.scalar()
                if conv_id:
                    msg_rows = await session.execute(
                        text(
                            "SELECT id, ordinal, role, message_type, content, metadata, "
                            "created_at FROM conversation_messages "
                            "WHERE conversation_id = :cid AND ordinal > :ord "
                            "ORDER BY ordinal ASC"
                        ),
                        {"cid": str(conv_id), "ord": last_message_ordinal},
                    )
                    for row in msg_rows:
                        events.append({
                            "type": "new_message",
                            "data": {
                                "id": str(row[0]),
                                "ordinal": row[1],
                                "role": row[2],
                                "message_type": row[3],
                                "content": row[4],
                                "metadata": row[5] if isinstance(row[5], dict) else {},
                            },
                        })

                # Check goal status change
                goal_row = await session.execute(
                    text(
                        "SELECT g.id, g.status, g.metadata FROM goals g "
                        "JOIN app_projects ap ON ap.goal_id = g.id "
                        "WHERE ap.id = :pid"
                    ),
                    {"pid": str(project_id)},
                )
                g_row = goal_row.first()
                if g_row:
                    events.append({
                        "type": "status_change",
                        "data": {
                            "goal_id": str(g_row[0]),
                            "status": g_row[1],
                            "metadata": g_row[2] if isinstance(g_row[2], dict) else {},
                        },
                    })
    except Exception:
        pass
    return events


@router.get("/stream")
async def event_stream(
    request: Request,
    project_id: str | None = Query(default=None),
    poll_interval: float = Query(default=2.0, gt=0, le=30),
):
    """SSE endpoint that pushes real-time updates to the console."""

    async def generate():
        last_ordinal = 0
        pid = uuid.UUID(project_id) if project_id else None

        # Send initial connection event
        yield f"data: {json.dumps({'type': 'connected', 'data': {}})}\n\n"

        while True:
            if await request.is_disconnected():
                break

            sessions = request.app.state.sessions
            changes = await _poll_changes(sessions, pid, last_ordinal)

            for event in changes:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] == "new_message":
                    ord_val = event["data"].get("ordinal", 0)
                    if ord_val > last_ordinal:
                        last_ordinal = ord_val

            try:
                await asyncio.wait_for(
                    request.is_disconnected(),
                    timeout=poll_interval,
                )
                break  # Client disconnected
            except TimeoutError:
                pass  # Continue polling

    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
