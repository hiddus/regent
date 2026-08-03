"""Session Work Plan helpers (Step 0 gate, completeness, plan_approve)."""

from __future__ import annotations

from typing import Any, Sequence

from regent.application.agent_loop_exit import build_ask_envelope

# Tools that mutate the product workspace — require a work plan first (W-1).
WRITE_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "run_command",
    }
)

_TRIVIAL_HINTS = (
    "只改",
    "仅改",
    "fix typo",
    "typo",
    "一行",
    "single file",
    "one file",
    "小修",
)


def is_trivial_work(plan: dict[str, Any] | None, user_or_goal_text: str = "") -> bool:
    """Small fixes may skip Step 0 (product Q2)."""
    meta = dict((plan or {}).get("acceptance_contract") or {})
    if meta.get("plan_required") is False or meta.get("work_plan_trivial"):
        return True
    if (plan or {}).get("work_plan_trivial"):
        return True
    text = (user_or_goal_text or str((plan or {}).get("goal_anchor_text") or "")).lower()
    if any(h.lower() in text for h in _TRIVIAL_HINTS):
        return True
    paths = list((plan or {}).get("planned_paths") or [])
    if len(paths) == 1 and str(paths[0]).count("/") <= 1:
        return True
    return False


def has_active_plan_items(todos: Sequence[dict[str, Any]] | None) -> bool:
    if not todos:
        return False
    for item in todos:
        status = str(item.get("status") or "pending").lower()
        if status in {"pending", "in_progress"}:
            return True
        if status == "completed":
            return True  # already planned this lease
    return False


def open_plan_item_contents(items: Sequence[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for item in items or []:
        status = str(item.get("status") or "").lower()
        if status in {"completed", "cancelled"}:
            continue
        content = str(item.get("content") or item.get("item_key") or "").strip()
        if content:
            out.append(content[:200])
    return out[:12]


def plan_approve_envelope(*, items: Sequence[dict[str, Any]], goal_summary: str = "") -> dict[str, Any]:
    lines = []
    for i, item in enumerate(items[:12], start=1):
        lines.append(f"{i}. {item.get('content') or item.get('id') or item.get('item_key')}")
    question = "请确认本轮工作清单后再开始改代码："
    if lines:
        question = question + "\n" + "\n".join(lines)
    if goal_summary:
        question = f"{goal_summary.strip()[:200]}\n\n{question}"
    return build_ask_envelope(
        question=question[:800],
        why_blocked="大改/首跑需先批准工作清单（OpenWork plan preview）。",
        options=[
            {"id": "approve_plan", "label": "批准计划，开始执行"},
            {"id": "revise_plan", "label": "要修改计划（说明方向）"},
            {"id": "stop", "label": "停止"},
        ],
        suggested="approve_plan",
        ask_type="plan_approve",
    )


def normalize_single_in_progress(todos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """At most one in_progress (W-2 soft)."""
    seen = False
    out: list[dict[str, Any]] = []
    for item in todos:
        row = dict(item)
        status = str(row.get("status") or "pending").lower()
        if status == "in_progress":
            if seen:
                row["status"] = "pending"
            else:
                seen = True
                row["status"] = "in_progress"
        out.append(row)
    return out


def step0_rejection_message() -> str:
    return (
        "Work plan required (Step 0): call todo_write with a checklist of steps "
        "before write_file/edit_file/run_command. "
        "Example: [{\"id\":\"1\",\"content\":\"scaffold app\",\"status\":\"pending\"}, ...]"
    )
