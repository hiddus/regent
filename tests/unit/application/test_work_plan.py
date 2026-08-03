"""Unit tests for Session Work Plan helpers + Step 0 gate."""

from __future__ import annotations

from regent.application.work_plan import (
    WRITE_TOOLS,
    has_active_plan_items,
    is_trivial_work,
    normalize_single_in_progress,
    open_plan_item_contents,
    plan_approve_envelope,
    step0_rejection_message,
)


def test_trivial_single_path_and_hints() -> None:
    assert is_trivial_work({"planned_paths": ["app.py"]})
    assert is_trivial_work({}, "请只改 typo")
    assert not is_trivial_work(
        {"planned_paths": ["src/app.py", "src/models.py", "tests/test_app.py"]},
        "做一个完整待办应用",
    )


def test_has_active_and_open_contents() -> None:
    todos = [
        {"id": "1", "content": "scaffold", "status": "completed"},
        {"id": "2", "content": "persist", "status": "in_progress"},
        {"id": "3", "content": "tests", "status": "pending"},
    ]
    assert has_active_plan_items(todos)
    assert open_plan_item_contents(todos) == ["persist", "tests"]


def test_normalize_single_in_progress() -> None:
    out = normalize_single_in_progress(
        [
            {"id": "1", "content": "a", "status": "in_progress"},
            {"id": "2", "content": "b", "status": "in_progress"},
        ]
    )
    assert out[0]["status"] == "in_progress"
    assert out[1]["status"] == "pending"


def test_plan_approve_envelope() -> None:
    env = plan_approve_envelope(
        items=[{"id": "1", "content": "搭路由"}, {"id": "2", "content": "持久化"}],
        goal_summary="做一个待办 App",
    )
    assert env["ask_type"] == "plan_approve"
    assert env["suggested"] == "approve_plan"
    assert "搭路由" in env["question"]


def test_write_tools_and_step0_message() -> None:
    assert "write_file" in WRITE_TOOLS
    assert "todo_write" in step0_rejection_message()
