"""Unit tests for H0 agent control plane helpers."""

from __future__ import annotations

from regent.agent.events import RegentEvent, append_regent_event, events_from_metadata
from regent.application.agent_control import (
    UserAbortError,
    apply_abort_to_goal_metadata,
    clear_abort,
    get_execution_mode,
    is_abort_requested,
    permission_ask_envelope,
    request_abort,
    set_execution_mode,
    tool_needs_permission,
)


def test_abort_flag_memory_and_metadata() -> None:
    clear_abort("g1")
    assert not is_abort_requested("g1")
    request_abort("g1", actor="u", reason="user_abort")
    assert is_abort_requested("g1")
    clear_abort("g1")
    meta = apply_abort_to_goal_metadata({}, "g2", actor="u")
    assert is_abort_requested("g2", meta)
    assert meta["agent_abort_requested"]["reason"] == "user_abort"
    clear_abort("g2")


def test_execution_mode_default_ask() -> None:
    assert get_execution_mode({}) == "ask"
    assert get_execution_mode(set_execution_mode({}, "act")) == "act"


def test_tool_permission_ask_vs_act() -> None:
    assert tool_needs_permission("write_file", execution_mode="ask", always_tools=set())
    assert not tool_needs_permission(
        "write_file", execution_mode="ask", always_tools={"write_file"}
    )
    assert not tool_needs_permission("write_file", execution_mode="act", always_tools=set())
    assert not tool_needs_permission("list_files", execution_mode="ask", always_tools=set())


def test_permission_envelope() -> None:
    env = permission_ask_envelope(tool_name="run_command", args_preview="pip install x")
    assert env["ask_type"] == "permission"
    assert any(o["id"] == "allow_once" for o in env["options"])


def test_regent_event_ring() -> None:
    meta = append_regent_event(
        {},
        RegentEvent(type="tool_call", summary="write", tool="write_file", turn=1),
    )
    rows = events_from_metadata(meta)
    assert len(rows) == 1
    assert rows[0]["type"] == "tool_call"
    assert "event_id" in rows[0]


def test_user_abort_error_code() -> None:
    err = UserAbortError("user_abort")
    assert err.failure_code == "USER_ABORT"
