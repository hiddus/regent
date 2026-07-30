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
    last_status_fingerprint: str | None,
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any] | None]:
    """Poll for new messages and real status changes.

    Returns ``(events, status_fingerprint, live_action)``.
    ``status_change`` is emitted only when the fingerprint changes.
    ``live_action`` is always returned (when available) for heartbeat sync.
    """
    events: list[dict[str, Any]] = []
    status_fingerprint = last_status_fingerprint
    live_action: dict[str, Any] | None = None
    try:
        async with sessions_factory() as session:
            if project_id:
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
                        created_at = row[6]
                        events.append({
                            "type": "new_message",
                            "data": {
                                "id": str(row[0]),
                                "ordinal": row[1],
                                "role": row[2],
                                "message_type": row[3],
                                "content": row[4],
                                "metadata": row[5] if isinstance(row[5], dict) else {},
                                "created_at": (
                                    created_at.isoformat()
                                    if hasattr(created_at, "isoformat")
                                    else str(created_at) if created_at is not None else None
                                ),
                            },
                        })

                # Goals are keyed by app_project_id (not app_projects.goal_id).
                goal_row = await session.execute(
                    text(
                        "SELECT g.id, g.status, g.metadata, g.updated_at FROM goals g "
                        "WHERE g.app_project_id = :pid "
                        "ORDER BY g.created_at DESC LIMIT 1"
                    ),
                    {"pid": str(project_id)},
                )
                g_row = goal_row.first()
                if g_row:
                    metadata = g_row[2] if isinstance(g_row[2], dict) else {}
                    if isinstance(metadata.get("live_action"), dict):
                        live_action = dict(metadata["live_action"])
                    updated_at = g_row[3]
                    updated_iso = (
                        updated_at.isoformat()
                        if hasattr(updated_at, "isoformat")
                        else str(updated_at) if updated_at is not None else ""
                    )
                    stage = str(metadata.get("execution_stage") or "") if isinstance(metadata, dict) else ""
                    live_at = str((live_action or {}).get("updated_at") or "")
                    fingerprint = f"{g_row[1]}|{stage}|{updated_iso}|{live_at}"
                    if fingerprint != last_status_fingerprint:
                        status_fingerprint = fingerprint
                        events.append({
                            "type": "status_change",
                            "data": {
                                "goal_id": str(g_row[0]),
                                "status": g_row[1],
                                "metadata": metadata,
                                "updated_at": updated_iso or None,
                                "live_action": live_action,
                            },
                        })
    except Exception:
        pass
    return events, status_fingerprint, live_action


@router.get("/stream")
async def event_stream(
    request: Request,
    project_id: str | None = Query(default=None),
    poll_interval: float = Query(default=1.0, gt=0, le=30),
):
    """SSE endpoint that pushes real-time updates to the console."""

    async def generate():
        from datetime import datetime, timezone

        last_ordinal = 0
        last_status_fingerprint: str | None = None
        pid = uuid.UUID(project_id) if project_id else None

        yield (
            "data: "
            + json.dumps(
                {
                    "type": "connected",
                    "data": {"server_time": datetime.now(timezone.utc).isoformat()},
                }
            )
            + "\n\n"
        )

        while True:
            if await request.is_disconnected():
                break

            sessions = request.app.state.sessions
            changes, last_status_fingerprint, live_action = await _poll_changes(
                sessions, pid, last_ordinal, last_status_fingerprint
            )

            for event in changes:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] == "new_message":
                    ord_val = event["data"].get("ordinal", 0)
                    if ord_val > last_ordinal:
                        last_ordinal = ord_val

            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "heartbeat",
                        "data": {
                            "server_time": datetime.now(timezone.utc).isoformat(),
                            "has_changes": bool(changes),
                            "live_action": live_action,
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )

            try:
                await asyncio.wait_for(
                    request.is_disconnected(),
                    timeout=poll_interval,
                )
                break
            except TimeoutError:
                pass

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
