"""Server-Sent Events endpoint for real-time console updates.

CD-5：自适应轮询；LISTEN/NOTIFY 仍为后续增强。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import text

router = APIRouter(prefix="/events", tags=["events"])

_ADAPTIVE_POLL_MIN = 0.25
_ADAPTIVE_POLL_MAX = 1.0
_ADAPTIVE_POLL_STEP = 0.25


async def _poll_changes(
    sessions_factory: Any,
    project_id: uuid.UUID | None,
    last_message_ordinal: int,
    last_status_fingerprint: str | None,
    last_regent_event_count: int = 0,
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any] | None, int]:
    """Poll for new messages, status, and RegentEvent deltas.

    Returns ``(events, status_fingerprint, live_action, regent_event_count)``.
    """
    events: list[dict[str, Any]] = []
    status_fingerprint = last_status_fingerprint
    live_action: dict[str, Any] | None = None
    regent_count = last_regent_event_count
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
                    # H1.2: push new RegentEvent rows as agent_event.
                    regent_buf = metadata.get("regent_events")
                    if isinstance(regent_buf, list):
                        regent_count = len(regent_buf)
                        if regent_count > last_regent_event_count:
                            for row in regent_buf[last_regent_event_count:]:
                                if isinstance(row, dict):
                                    events.append(
                                        {
                                            "type": "agent_event",
                                            "data": {
                                                "goal_id": str(g_row[0]),
                                                **row,
                                            },
                                        }
                                    )
                    updated_at = g_row[3]
                    updated_iso = (
                        updated_at.isoformat()
                        if hasattr(updated_at, "isoformat")
                        else str(updated_at) if updated_at is not None else ""
                    )
                    stage = str(metadata.get("execution_stage") or "") if isinstance(metadata, dict) else ""
                    live_at = str((live_action or {}).get("updated_at") or "")
                    exit_kind = ""
                    exit_row = metadata.get("agent_loop_exit")
                    if isinstance(exit_row, dict):
                        exit_kind = str(exit_row.get("exit_kind") or "")
                    fingerprint = (
                        f"{g_row[1]}|{stage}|{updated_iso}|{live_at}|{exit_kind}|{regent_count}"
                    )
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
                                "agent_loop_exit": exit_row if isinstance(exit_row, dict) else None,
                                "execution_mode": metadata.get("execution_mode") or "ask",
                            },
                        })
    except Exception:
        pass
    return events, status_fingerprint, live_action, regent_count


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
        last_regent_event_count = 0
        last_transient_sequence = 0
        pid = uuid.UUID(project_id) if project_id else None
        poll_backoff = _ADAPTIVE_POLL_MIN

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
            changes, last_status_fingerprint, live_action, last_regent_event_count = (
                await _poll_changes(
                    sessions,
                    pid,
                    last_ordinal,
                    last_status_fingerprint,
                    last_regent_event_count,
                )
            )

            for event in changes:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] == "new_message":
                    ord_val = event["data"].get("ordinal", 0)
                    if ord_val > last_ordinal:
                        last_ordinal = ord_val

            transient: list[dict[str, Any]] = []
            registry = getattr(request.app.state, "transient_progress", None)
            if registry is not None and pid is not None:
                transient = await registry.since(str(pid), last_transient_sequence)
                for item in transient:
                    last_transient_sequence = max(last_transient_sequence, int(item["sequence"]))
                    yield f"data: {json.dumps({'type': 'guidance_progress', 'data': item}, ensure_ascii=False)}\n\n"

            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "heartbeat",
                        "data": {
                            "server_time": datetime.now(timezone.utc).isoformat(),
                            "has_changes": bool(changes or transient),
                            "live_action": live_action,
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )

            if changes:
                poll_backoff = _ADAPTIVE_POLL_MIN
            else:
                poll_backoff = min(_ADAPTIVE_POLL_MAX, poll_backoff + _ADAPTIVE_POLL_STEP)

            wait_seconds = min(poll_interval, poll_backoff)
            try:
                await asyncio.wait_for(
                    request.is_disconnected(),
                    timeout=wait_seconds,
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
