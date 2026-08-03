"""RegentEvent — discriminant event contract (H0.6 prebury for H1 streaming).

TRANSITIONAL persistence: append-only ring on goal.metadata_json["regent_events"].
Not yet the sole durable truth; dual-write with live_action / activity_log until H1.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

RegentEventType = Literal[
    "turn_start",
    "turn_end",
    "tool_call",
    "plan_updated",
    "permission_asked",
    "ask_user",
    "abort_requested",
    "submit",
    "result",
    "agent_started",
    "agent_finished",
    "budget",
    "status",
]

META_EVENTS_KEY = "regent_events"
META_EVENTS_MAX = 120


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class RegentEvent:
    type: RegentEventType
    summary: str
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    at: str = field(default_factory=utc_now_iso)
    run_id: str | None = None
    goal_id: str | None = None
    turn: int | None = None
    tool: str | None = None
    parent_tool_use_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        return {k: v for k, v in raw.items() if v is not None and v != {}}


def append_regent_event(
    metadata: dict[str, Any],
    event: RegentEvent | dict[str, Any],
    *,
    max_events: int = META_EVENTS_MAX,
) -> dict[str, Any]:
    meta = dict(metadata or {})
    row = event.as_dict() if isinstance(event, RegentEvent) else dict(event)
    if "event_id" not in row:
        row["event_id"] = str(uuid.uuid4())
    if "at" not in row:
        row["at"] = utc_now_iso()
    buf = list(meta.get(META_EVENTS_KEY) or [])
    buf.append(row)
    meta[META_EVENTS_KEY] = buf[-max_events:]
    return meta


def events_from_metadata(metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = list((metadata or {}).get(META_EVENTS_KEY) or [])
    return [e for e in raw if isinstance(e, dict)]
