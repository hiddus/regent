"""In-process SubagentRunner runtime roster for console observability."""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
# goal_id -> list[agent runtime dict]
_RUNTIME: dict[str, list[dict[str, Any]]] = {}
_MAX_PER_GOAL = 24


def upsert_subagent_runtime(
    goal_id: str | None,
    *,
    agent_id: str,
    name: str,
    activity: str,
    detail: str | None = None,
    tool: str | None = None,
    milestone_key: str | None = None,
) -> None:
    if not goal_id:
        return
    gid = str(goal_id)
    entry = {
        "id": agent_id,
        "name": name,
        "role": "executor",
        "role_label": name,
        "kind": "subagent",
        "activity": activity,
        "detail": detail,
        "tool": tool,
        "milestone_key": milestone_key,
        "is_main": False,
        "updated_at": time.time(),
    }
    with _lock:
        rows = list(_RUNTIME.get(gid) or [])
        replaced = False
        for i, row in enumerate(rows):
            if row.get("id") == agent_id:
                rows[i] = {**row, **entry}
                replaced = True
                break
        if not replaced:
            rows.append(entry)
        _RUNTIME[gid] = rows[-_MAX_PER_GOAL:]


def list_subagent_runtime(goal_id: str) -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in (_RUNTIME.get(str(goal_id)) or [])]
