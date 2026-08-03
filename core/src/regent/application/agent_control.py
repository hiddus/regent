"""H0 control plane: abort flags, execution_mode, tool permission gate."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any, Literal

from regent.application.agent_loop_exit import build_ask_envelope

ExecutionMode = Literal["ask", "act"]

META_ABORT = "agent_abort_requested"
META_EXECUTION_MODE = "execution_mode"
META_SESSION_ALWAYS_TOOLS = "agent_permission_always"  # list[str] session-scoped

# Tools that may mutate product / side effects — gated in ask mode.
GATED_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "run_command",
    }
)
# Always gated even in act mode (destructive / egress-ish).
ALWAYS_ASK_TOOLS = frozenset(
    {
        # reserved for future delete_file / send_*; run_command stays gated in ask only
    }
)

_abort_lock = threading.Lock()
_abort_flags: dict[str, dict[str, Any]] = {}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def request_abort(
    goal_id: str,
    *,
    actor: str = "user",
    reason: str = "user_abort",
) -> dict[str, Any]:
    payload = {
        "at": utc_now_iso(),
        "actor": actor,
        "reason": reason,
    }
    with _abort_lock:
        _abort_flags[str(goal_id)] = payload
    return payload


def clear_abort(goal_id: str) -> None:
    with _abort_lock:
        _abort_flags.pop(str(goal_id), None)


def is_abort_requested(goal_id: str | None, metadata: dict[str, Any] | None = None) -> bool:
    if not goal_id:
        return False
    gid = str(goal_id)
    with _abort_lock:
        if gid in _abort_flags:
            return True
    meta = dict(metadata or {})
    return bool(meta.get(META_ABORT))


def abort_payload(goal_id: str | None, metadata: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not goal_id:
        return None
    gid = str(goal_id)
    with _abort_lock:
        mem = _abort_flags.get(gid)
    if mem:
        return dict(mem)
    raw = dict(metadata or {}).get(META_ABORT)
    return dict(raw) if isinstance(raw, dict) else None


def apply_abort_to_goal_metadata(
    metadata: dict[str, Any],
    goal_id: str,
    *,
    actor: str = "user",
    reason: str = "user_abort",
) -> dict[str, Any]:
    request_abort(goal_id, actor=actor, reason=reason)
    meta = dict(metadata or {})
    meta[META_ABORT] = {
        "at": utc_now_iso(),
        "actor": actor,
        "reason": reason,
    }
    return meta


def get_execution_mode(metadata: dict[str, Any] | None) -> ExecutionMode:
    raw = str(dict(metadata or {}).get(META_EXECUTION_MODE) or "ask").lower()
    return "act" if raw == "act" else "ask"


def set_execution_mode(metadata: dict[str, Any], mode: ExecutionMode) -> dict[str, Any]:
    meta = dict(metadata or {})
    meta[META_EXECUTION_MODE] = "act" if mode == "act" else "ask"
    return meta


def session_always_tools(metadata: dict[str, Any] | None) -> set[str]:
    raw = dict(metadata or {}).get(META_SESSION_ALWAYS_TOOLS) or []
    if not isinstance(raw, list):
        return set()
    return {str(x) for x in raw if str(x).strip()}


def grant_session_always(metadata: dict[str, Any], tool: str) -> dict[str, Any]:
    meta = dict(metadata or {})
    tools = session_always_tools(meta)
    tools.add(str(tool))
    meta[META_SESSION_ALWAYS_TOOLS] = sorted(tools)
    return meta


def tool_needs_permission(
    tool_name: str,
    *,
    execution_mode: ExecutionMode,
    always_tools: set[str] | None = None,
) -> bool:
    name = str(tool_name or "")
    if name in (always_tools or set()):
        return False
    if name in ALWAYS_ASK_TOOLS:
        return True
    if execution_mode == "act":
        return False
    return name in GATED_TOOLS


def permission_ask_envelope(
    *,
    tool_name: str,
    args_preview: str = "",
    execution_mode: ExecutionMode = "ask",
) -> dict[str, Any]:
    preview = (args_preview or "")[:200]
    return build_ask_envelope(
        question=f"是否允许执行工具 `{tool_name}`？\n{preview}".strip(),
        why_blocked=f"execution_mode={execution_mode}；危险/写操作需人确认（OpenWork permission）。",
        options=[
            {"id": "allow_once", "label": "允许一次"},
            {"id": "allow_always_session", "label": "本会话允许此类工具"},
            {"id": "deny", "label": "拒绝并停止"},
        ],
        suggested="allow_once",
        ask_type="permission",
        deny_consequence="拒绝后本轮 STOP，草稿保留。",
    )


class UserAbortError(RuntimeError):
    """Raised when user abort_token trips during agent loop."""

    def __init__(self, reason: str = "user_abort") -> None:
        super().__init__(reason)
        self.reason = reason
        self.failure_code = "USER_ABORT"


class ToolPermissionRequiredError(RuntimeError):
    """Raised to exit lease into ASK_HUMAN for tool permission."""

    def __init__(
        self,
        tool_name: str,
        *,
        args_preview: str = "",
        envelope: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"permission required for {tool_name}")
        self.tool_name = tool_name
        self.args_preview = args_preview
        self.envelope = envelope or permission_ask_envelope(
            tool_name=tool_name, args_preview=args_preview
        )
        self.failure_code = "TOOL_PERMISSION_REQUIRED"


class AskUserRequiredError(RuntimeError):
    """Raised when model calls ask_user_question."""

    def __init__(
        self,
        question: str,
        *,
        options: list[dict[str, str]] | None = None,
        envelope: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(question)
        self.question = question
        self.options = options or []
        self.envelope = envelope or build_ask_envelope(
            question=question,
            why_blocked="Agent 需要你确认后再继续。",
            options=options,
            ask_type="ask_user",
            suggested=(options[0]["id"] if options else "continue_fix"),
        )
        self.failure_code = "ASK_USER_REQUIRED"
