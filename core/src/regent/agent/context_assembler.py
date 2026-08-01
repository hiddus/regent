"""Layered context assembler for agentic generation (budgeted segments).

Prompt-cache layout (P0 / D9 fix):
  system → static user (stable within a Run) → conversation → volatile user

Volatile workspace/todos/failures must NOT precede conversation, or every write
invalidates the entire prefix cache.
"""

from __future__ import annotations

import json
from typing import Any

from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import ChatMessage, VerificationGap

# Soft character budgets (~4 chars/token heuristic).
_BUDGETS = {
    "goal_anchor": 8_000,
    "skill_guidance": 6_000,
    "project_memory": 24_000,
    # Tree-only; no file fulltext (use read_file tool).
    "workspace_state": 8_000,
    "todo_state": 4_000,
    "recent_failures": 16_000,
    "conversation_context": 6_000,
    "evidence_context": 8_000,
}


def _clip(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    return text[: budget - 20] + "\n...[truncated]"


def _join_segments(segments: list[str]) -> str:
    return "\n\n".join(s for s in segments if s)


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
            "- Workspace context only lists paths; call read_file before editing unknown content.\n"
        )

    def static_prefix_text(self) -> str:
        """Within-run stable blob (must not include workspace/todos/gaps)."""
        return _join_segments(
            [
                self._goal_anchor_segment(),
                self._skill_guidance_segment(),
                self._project_memory_segment(),
                self._conversation_segment(),
                self._evidence_segment(),
            ]
        )

    def volatile_suffix_text(
        self,
        *,
        turn: int,
        force_goal_reinject: bool = False,
    ) -> str:
        """Per-turn delta placed AFTER conversation for prompt-cache friendliness."""
        segments = [
            self._workspace_segment(),
            self._todo_segment(),
            self._failures_segment(),
        ]
        reinject = force_goal_reinject or (turn > 0 and turn % 10 == 0)
        if reinject:
            segments.append(
                "══════ GOAL REMINDER (re-injected) ══════\n"
                + self._goal_anchor_segment()
            )
        return _join_segments(segments)

    def assemble(
        self,
        *,
        turn: int,
        conversation: list[ChatMessage],
        force_goal_reinject: bool = False,
    ) -> list[ChatMessage]:
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=self.system_prompt()),
        ]
        static_blob = self.static_prefix_text()
        if static_blob:
            messages.append(ChatMessage(role="user", content=static_blob))
        messages.extend(conversation)
        volatile = self.volatile_suffix_text(
            turn=turn, force_goal_reinject=force_goal_reinject
        )
        if volatile:
            messages.append(ChatMessage(role="user", content=volatile))
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

    def _skill_guidance_segment(self) -> str:
        """M5: inject selected Skill guidance into the user turn (not just metadata)."""
        guidance = str(self._plan.get("skill_guidance") or "").strip()
        if not guidance:
            return ""
        refs = self._plan.get("skill_refs") or []
        header = "══════ SKILL GUIDANCE ══════"
        if isinstance(refs, list) and refs:
            ids = [
                str(r.get("skill_id") or "")
                for r in refs
                if isinstance(r, dict) and r.get("skill_id")
            ]
            if ids:
                header += "\nSelected: " + ", ".join(ids)
        return _clip(header + "\n" + guidance, _BUDGETS["skill_guidance"])

    def _project_memory_segment(self) -> str:
        if not self._regent_md.strip():
            return ""
        return _clip("══════ REGENT.md ══════\n" + self._regent_md, _BUDGETS["project_memory"])

    def _conversation_segment(self) -> str:
        """CD-4.3: retrieved conversation snippets (guidance history, user
        clarifications) so the agent can reference prior human decisions.

        Reads ``plan["conversation_snippets"]`` — a list of either plain strings
        or ``{"role": ..., "content": ...}`` dicts. Absent/empty by default; the
        caller (delivery pipeline) is responsible for retrieval/ranking.
        """
        snippets = self._plan.get("conversation_snippets") or []
        if not snippets:
            return ""
        lines = ["══════ CONVERSATION CONTEXT ══════"]
        for item in snippets:
            if isinstance(item, dict):
                role = str(item.get("role") or "user").strip() or "user"
                text = str(item.get("content") or item.get("text") or "").strip()
                if text:
                    lines.append(f"[{role}] {text}")
            else:
                text = str(item).strip()
                if text:
                    lines.append(f"- {text}")
        if len(lines) == 1:
            return ""
        return _clip("\n".join(lines), _BUDGETS["conversation_context"])

    def _evidence_segment(self) -> str:
        """CD-4.3: retrieved evidence snippets (discovery sources, observed data)
        so generation can cite/ground itself in acquired evidence.

        Reads ``plan["evidence_snippets"]`` — a list of either plain strings or
        ``{"title", "source"/"url", "summary"/"content"}`` dicts. Absent/empty by
        default.
        """
        snippets = self._plan.get("evidence_snippets") or []
        if not snippets:
            return ""
        lines = ["══════ EVIDENCE ══════"]
        for item in snippets:
            if isinstance(item, dict):
                title = str(item.get("title") or "").strip()
                source = str(item.get("source") or item.get("url") or "").strip()
                text = str(item.get("summary") or item.get("content") or "").strip()
                header = " — ".join(part for part in (title, source) if part)
                if header:
                    lines.append(f"- {header}")
                if text:
                    lines.append(f"  {text}")
            else:
                text = str(item).strip()
                if text:
                    lines.append(f"- {text}")
        if len(lines) == 1:
            return ""
        return _clip("\n".join(lines), _BUDGETS["evidence_context"])

    def _workspace_segment(self) -> str:
        """Tree-only workspace view — no file fulltext (prompt-cache + cost)."""
        tree = self._toolkit.list_tree(".", limit=120)
        lines = [
            "══════ WORKSPACE ══════",
            "File tree (paths only — use read_file for contents):",
        ]
        lines.extend(f"  {p}" for p in tree[:80])
        recent = list(self._toolkit.recent_writes[-8:] or [])
        if recent:
            lines.append("Recent writes:")
            lines.extend(f"  - {rel}" for rel in recent)
        return _clip("\n".join(lines), _BUDGETS["workspace_state"])

    def _todo_segment(self) -> str:
        if not self._toolkit.todos:
            return "══════ TODOS ══════\n(none yet — create with todo_write)"
        blob = json.dumps(self._toolkit.todos, ensure_ascii=False, indent=2)
        return _clip("══════ TODOS ══════\n" + blob, _BUDGETS["todo_state"])

    def _failures_segment(self) -> str:
        acceptance = self._plan.get("acceptance_contract") or {}
        gap_reasons = list(acceptance.get("delivery_gap_reasons") or [])
        lessons = list(acceptance.get("failure_lessons") or [])
        constraints = list(acceptance.get("learned_constraints") or [])
        lines = ["══════ RECENT FAILURES ══════"]
        replan_nonce = str(acceptance.get("replan_nonce") or "").strip()
        if replan_nonce:
            lines.append(f"Replan nonce (must change plan inputs): {replan_nonce}")
        if gap_reasons:
            lines.append("Prior delivery gap reasons:")
            lines.extend(f"  - {r}" for r in gap_reasons[:12])
        if constraints:
            lines.append("Learned constraints from prior failures:")
            lines.extend(f"  - {c}" for c in constraints[:12])
        if lessons:
            lines.append("Prior failure lessons (absorb before regenerating):")
            for lesson in lessons[-4:]:
                if not isinstance(lesson, dict):
                    continue
                digest = lesson.get("lesson_digest") or "?"
                kind = lesson.get("gap_kind") or "?"
                method = lesson.get("escalation_method") or "?"
                lines.append(
                    f"  - lesson={digest} gap_kind={kind} method={method} "
                    f"attempt={lesson.get('attempt')}"
                )
                for reason in list(lesson.get("gap_reasons") or [])[:4]:
                    lines.append(f"      gap: {reason}")
        for gap in self._gaps[:8]:
            lines.append(f"[{gap.code}] {gap.detail}")
            if gap.artifact_snippet:
                lines.append(gap.artifact_snippet[:2_000])
        if len(lines) == 1:
            return ""
        return _clip("\n".join(lines), _BUDGETS["recent_failures"])
