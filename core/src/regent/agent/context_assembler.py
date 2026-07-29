"""Layered context assembler for agentic generation (budgeted segments)."""

from __future__ import annotations

import json
from typing import Any

from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import ChatMessage, VerificationGap

# Soft character budgets (~4 chars/token heuristic).
_BUDGETS = {
    "goal_anchor": 8_000,
    "project_memory": 24_000,
    "workspace_state": 32_000,
    "todo_state": 4_000,
    "recent_failures": 16_000,
}


def _clip(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    return text[: budget - 20] + "\n...[truncated]"


class ContextAssembler:
    """Assemble per-turn prompts with hard segment budgets + goal re-injection."""

    def __init__(
        self,
        *,
        plan: dict[str, Any],
        toolkit: WorkspaceToolkit,
        regent_md: str = "",
        gaps: list[VerificationGap] | None = None,
    ) -> None:
        self._plan = plan
        self._toolkit = toolkit
        self._regent_md = regent_md
        self._gaps = list(gaps or [])
        acceptance = dict(plan.get("acceptance_contract") or {})
        self._goal_text = str(plan.get("goal_anchor_text") or "")
        self._first_deliverable = str(acceptance.get("first_deliverable") or "")
        self._success_criteria = acceptance.get("success_criteria")
        self._planned_paths = list(plan.get("planned_paths") or [])

    def system_prompt(self) -> str:
        return (
            "You are Regent's delivery agent. Build a real, runnable product — not a demo poster.\n"
            "Use tools to write files, install deps, run tests, and smoke-check endpoints.\n"
            "Rules:\n"
            "- src/app.py must be a real WSGI/ASGI app with business logic (no pure static hosting).\n"
            "- Prefer persistence + empty states over fake placeholder users/cards.\n"
            "- Stay within planned_paths when possible; create supporting files as needed.\n"
            "- When done, ensure requirements.txt, README.md, and a working entrypoint exist.\n"
            "- Do not claim success until you have verified the app can start.\n"
        )

    def assemble(
        self,
        *,
        turn: int,
        conversation: list[ChatMessage],
        force_goal_reinject: bool = False,
    ) -> list[ChatMessage]:
        segments = [
            self._goal_anchor_segment(),
            self._project_memory_segment(),
            self._workspace_segment(),
            self._todo_segment(),
            self._failures_segment(),
        ]
        reinject = force_goal_reinject or turn == 0 or (turn > 0 and turn % 10 == 0)
        if reinject and turn > 0:
            segments.insert(
                0,
                "══════ GOAL REMINDER (re-injected) ══════\n" + self._goal_anchor_segment(),
            )
        user_blob = "\n\n".join(s for s in segments if s)
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=self.system_prompt()),
            ChatMessage(role="user", content=user_blob),
            *conversation,
        ]
        return messages

    def _goal_anchor_segment(self) -> str:
        lines = ["══════ GOAL ANCHOR ══════"]
        if self._goal_text:
            lines.append(f"Original goal: {self._goal_text}")
        if self._first_deliverable:
            lines.append(f"First deliverable: {self._first_deliverable}")
        if self._success_criteria:
            lines.append("Success criteria:")
            lines.append(json.dumps(self._success_criteria, ensure_ascii=False, indent=2))
        acceptance = self._plan.get("acceptance_contract") or {}
        if acceptance.get("full_goal_success_criteria"):
            lines.append("Full goal success criteria (always visible):")
            lines.append(
                json.dumps(acceptance["full_goal_success_criteria"], ensure_ascii=False, indent=2)
            )
        if self._planned_paths:
            lines.append("Planned paths: " + ", ".join(self._planned_paths[:40]))
        return _clip("\n".join(lines), _BUDGETS["goal_anchor"])

    def _project_memory_segment(self) -> str:
        if not self._regent_md.strip():
            return ""
        return _clip("══════ REGENT.md ══════\n" + self._regent_md, _BUDGETS["project_memory"])

    def _workspace_segment(self) -> str:
        tree = self._toolkit.list_tree(".", limit=120)
        lines = ["══════ WORKSPACE ══════", "File tree:"]
        lines.extend(f"  {p}" for p in tree[:80])
        recent = self._toolkit.recent_writes[-5:]
        for rel in recent:
            try:
                content = self._toolkit.read_text(rel, max_chars=4_000)
            except OSError:
                continue
            lines.append(f"\n--- recent file: {rel} ---")
            lines.append(content)
        return _clip("\n".join(lines), _BUDGETS["workspace_state"])

    def _todo_segment(self) -> str:
        if not self._toolkit.todos:
            return "══════ TODOS ══════\n(none yet — create with todo_write)"
        blob = json.dumps(self._toolkit.todos, ensure_ascii=False, indent=2)
        return _clip("══════ TODOS ══════\n" + blob, _BUDGETS["todo_state"])

    def _failures_segment(self) -> str:
        acceptance = self._plan.get("acceptance_contract") or {}
        gap_reasons = list(acceptance.get("delivery_gap_reasons") or [])
        lines = ["══════ RECENT FAILURES ══════"]
        if gap_reasons:
            lines.append("Prior delivery gap reasons:")
            lines.extend(f"  - {r}" for r in gap_reasons[:12])
        for gap in self._gaps[:8]:
            lines.append(f"[{gap.code}] {gap.detail}")
            if gap.artifact_snippet:
                lines.append(gap.artifact_snippet[:2_000])
        if len(lines) == 1:
            return ""
        return _clip("\n".join(lines), _BUDGETS["recent_failures"])
