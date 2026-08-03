"""Tests for H1/H2 helpers: nudge, replan detect, hive policy."""

from __future__ import annotations

from regent.application.hive_policy import (
    coding_default_is_primary,
    get_goal_kind,
    hive_opt_in_allowed,
)
from regent.application.work_plan import (
    current_blocked_item_key,
    looks_like_replan_request,
    todo_nudge_message,
)


def test_blocked_item_prefers_in_progress() -> None:
    todos = [
        {"id": "1", "content": "a", "status": "pending"},
        {"id": "2", "content": "b", "status": "in_progress"},
    ]
    assert current_blocked_item_key(todos) == "2"


def test_todo_nudge_after_stale_turns() -> None:
    todos = [{"id": "1", "content": "scaffold", "status": "pending"}]
    assert todo_nudge_message(todos, turns_since_plan_update=3) is None
    msg = todo_nudge_message(todos, turns_since_plan_update=8)
    assert msg is not None
    assert "scaffold" in msg


def test_replan_keywords() -> None:
    assert looks_like_replan_request("请重新规划步骤")
    assert looks_like_replan_request("revise_plan")
    assert not looks_like_replan_request("继续修复 typo")


def test_hive_opt_in_not_coding_default() -> None:
    assert coding_default_is_primary({})
    assert coding_default_is_primary({"goal_kind": "coding"})
    assert not coding_default_is_primary({"goal_kind": "scenic"})
    assert hive_opt_in_allowed({"hive_enabled": True})
    assert get_goal_kind({"goal_kind": "city"}) == "city"
