"""Layered context assembler for agentic generation (budgeted segments).

Prompt-cache layout (P0 / D9 fix):
  system → static user (stable within a Run) → conversation → volatile user

Volatile workspace/todos/failures must NOT precede conversation, or every write
invalidates the entire prefix cache.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import ChatMessage, VerificationGap

# Soft character budgets (~4 chars/token heuristic).
_BUDGETS = {
    "goal_anchor": 8_000,
    "skill_guidance": 6_000,
    "project_memory": 8_000,
    "retrieved_memory": 4_000,
    # Tree-only; no file fulltext (use read_file tool).
    "workspace_state": 8_000,
    "todo_state": 4_000,
    "recent_failures": 8_000,
    "conversation_context": 6_000,
    "evidence_context": 8_000,
}


# ---------------------------------------------------------------------------
# Content-aware clipping (replaces naive _clip for structured content)
# ---------------------------------------------------------------------------


def _clip(text: str, budget: int) -> str:
    """Legacy character-boundary clip — kept for segments without structure."""
    if len(text) <= budget:
        return text
    return text[: budget - 20] + "\n...[truncated]"


def _clip_lines(text: str, budget: int) -> str:
    """Clip at line boundaries so structured blocks are never split mid-line."""
    if len(text) <= budget:
        return text
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    total = 0
    for line in lines:
        if total + len(line) > budget - 40:
            break
        out.append(line)
        total += len(line)
    out.append("\n...[truncated at line boundary]")
    return "".join(out)


def _clip_json_list(text: str, budget: int) -> str:
    """Clip a JSON array or object by preserving complete entries.

    Falls back to _clip_lines when the text is not valid JSON.
    """
    if len(text) <= budget:
        return text
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return _clip_lines(text, budget)
    # If it's a dict rendered with indent, drop last-level keys.
    if isinstance(obj, dict):
        return _clip_lines(text, budget)
    if not isinstance(obj, list):
        return _clip_lines(text, budget)
    # Drop items from the end until it fits.
    while len(obj) > 1:
        try:
            candidate = json.dumps(obj, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return _clip_lines(text, budget)
        if len(candidate) <= budget - 40:
            return candidate
        obj = obj[:-1]
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n...[items truncated]"


def _clip_sentences(text: str, budget: int) -> str:
    """Clip at sentence boundaries (period / newline) for prose text."""
    if len(text) <= budget:
        return text
    # Split on sentence-ending punctuation or newlines.
    parts = re.split(r"(?<=[.。!！?？\n])\s*", text)
    out: list[str] = []
    total = 0
    for part in parts:
        if total + len(part) > budget - 40:
            break
        out.append(part)
        total += len(part)
    out.append("\n...[truncated at sentence boundary]")
    return "".join(out)


def _segment_fingerprint(text: str) -> str:
    """Stable short hash for dedup of assembled segments."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


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
        entry = "src.app:app"
        acceptance = dict(self._plan.get("acceptance_contract") or {})
        profile = self._plan.get("runtime_profile") or acceptance.get("runtime_profile") or {}
        if isinstance(profile, dict):
            mod = str(profile.get("entry_module") or "src.app").strip() or "src.app"
            obj = str(profile.get("entry_object") or "app").strip() or "app"
            entry = f"{mod}:{obj}"
        base = (
            "You are Regent's delivery agent. Build a real, runnable product — not a demo poster.\n"
            "Use tools to write files, install deps, run tests, and smoke-check endpoints.\n"
            "Rules:\n"
            "- Step 0: before write_file/edit_file/run_command, call todo_write with a concrete checklist "
            "(usually ≥3 steps). Keep at most one in_progress; mark completed as you go.\n"
            "- Before submit: every todo must be completed or cancelled; open items block delivery.\n"
            "- Work as an evidence loop: state the current hypothesis and expected observable result, "
            "take the shortest useful tool action, compare actual vs expected, then update the plan.\n"
            "- For a new or weak hypothesis, prefer 1–2 mutating actions before re-observing. "
            "Use larger batches only when prior tool evidence supports the sequence.\n"
            "- Never repeat an unchanged failed action without new evidence or a changed hypothesis.\n"
            "- If requirements are unclear or a risky choice is needed, call ask_user_question "
            "(structured options) instead of guessing.\n"
            "- Prefer persistence + empty states over fake placeholder users/cards.\n"
            "- Stay within planned_paths when possible; create supporting files as needed.\n"
            "- Workspace context only lists paths; call read_file before editing unknown content.\n"
            "- When RECENT FAILURES / failure envelopes appear, fix those exact errors before new features.\n"
        )
        goal = f"{self._goal_text} {self._first_deliverable}".lower()
        runtime_kind = str(profile.get("kind") or profile.get("runtime") or "").lower() if isinstance(profile, dict) else ""
        is_web = bool(runtime_kind in {"http", "web", "flask", "fastapi"} or any(
            token in goal for token in (
                "web", "website", "http", "flask", "fastapi", "网页", "网站", "页面"
            )
        ))
        if not is_web:
            return base + (
                "Task profile: non-Web/general delivery. Do not invent HTTP, Preview, CSS, "
                "or route requirements unless the Goal explicitly asks for them. Verify using "
                "the repository's native tests and acceptance criteria.\n"
            )
        return base + (
            "Task profile: Web/HTTP delivery.\n"
            f"- HTTP app object MUST live at `{entry}` (Profile entry_module:entry_object).\n"
            "- Smoke only declared routes; do not invent `/health` or `/ready`.\n"
            "- Preview must start a real HTTP process for Flask/FastAPI Goals.\n"
            "- Visual quality requires designed CSS, `<main>`, and working list→detail navigation.\n"
            "- Prefer relative href/src so Preview path-prefix proxy keeps assets working.\n"
            "- Verify startup and the public Preview, including styles and a content detail route.\n"
        )

    def static_prefix_text(self) -> str:
        """Within-run stable blob (must not include workspace/todos/gaps)."""
        return _join_segments(
            [
                self._goal_anchor_segment(),
                self._skill_guidance_segment(),
                self._project_memory_segment(),
                self._retrieved_memory_segment(),
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
        """Build goal anchor — success criteria JSON is PROTECTED from destructive truncation."""
        lines = ["══════ GOAL ANCHOR ══════"]
        if self._goal_text:
            lines.append(f"Direction (stable): {self._goal_text}")
        if self._first_deliverable:
            lines.append(f"Current understanding (may evolve): {self._first_deliverable}")
        if self._success_criteria:
            lines.append("Success criteria:")
            # PROTECTED: serialize fully, then clip by line boundary (never mid-JSON).
            lines.append(json.dumps(self._success_criteria, ensure_ascii=False, indent=2))
        acceptance = self._plan.get("acceptance_contract") or {}
        if acceptance.get("full_goal_success_criteria"):
            lines.append("Full goal success criteria (always visible):")
            lines.append(
                json.dumps(acceptance["full_goal_success_criteria"], ensure_ascii=False, indent=2)
            )
        if self._planned_paths:
            lines.append("Planned paths: " + ", ".join(self._planned_paths[:40]))
        # Use _clip_lines to avoid splitting JSON mid-structure.
        return _clip_lines("\n".join(lines), _BUDGETS["goal_anchor"])

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
        return _clip_lines(header + "\n" + guidance, _BUDGETS["skill_guidance"])

    def _project_memory_segment(self) -> str:
        if not self._regent_md.strip():
            return ""
        # Workspace paths and old verification logs already have dedicated,
        # fresher segments. Keep only durable project knowledge here.
        selected: list[str] = []
        keep = False
        for line in self._regent_md.splitlines():
            if line.startswith("## "):
                keep = line[3:].strip() in {
                    "Goal lessons",
                    "Tech stack",
                    "Known constraints / gaps",
                }
            if keep:
                selected.append(line)
        memory = "\n".join(selected).strip() or self._regent_md
        return _clip_lines("══════ PROJECT HARD MEMORY ══════\n" + memory, _BUDGETS["project_memory"])

    def _retrieved_memory_segment(self) -> str:
        memory = str(self._plan.get("retrieved_memory") or "").strip()
        if not memory:
            return ""
        return _clip_sentences(
            "══════ RELEVANT VERIFIED MEMORY ══════\n"
            "Use only when applicable; current user instructions win.\n"
            + memory,
            _BUDGETS["retrieved_memory"],
        )

    def segment_char_sizes(self) -> dict[str, int]:
        """Expose prompt composition for cost/quality diagnostics."""
        segments = {
            "goal": self._goal_anchor_segment(),
            "skills": self._skill_guidance_segment(),
            "project_memory": self._project_memory_segment(),
            "retrieved_memory": self._retrieved_memory_segment(),
            "conversation_context": self._conversation_segment(),
            "evidence": self._evidence_segment(),
            "workspace": self._workspace_segment(),
            "todos": self._todo_segment(),
            "failures": self._failures_segment(),
        }
        return {name: len(value) for name, value in segments.items() if value}

    def segment_fingerprints(self) -> dict[str, str]:
        """Return per-segment content fingerprints for dedup diagnostics."""
        # Rebuild segments to get text for fingerprinting.
        segments = {
            "goal": self._goal_anchor_segment(),
            "skills": self._skill_guidance_segment(),
            "project_memory": self._project_memory_segment(),
            "retrieved_memory": self._retrieved_memory_segment(),
            "conversation_context": self._conversation_segment(),
            "evidence": self._evidence_segment(),
            "workspace": self._workspace_segment(),
            "todos": self._todo_segment(),
            "failures": self._failures_segment(),
        }
        return {
            name: _segment_fingerprint(text)
            for name, text in segments.items()
            if text
        }

    def assemble_diagnostics(self, *, turn: int, conversation: list[ChatMessage]) -> dict[str, Any]:
        """Return diagnostic info for prompt observability (P2-8)."""
        sizes = self.segment_char_sizes()
        fps = self.segment_fingerprints()
        total_chars = sum(sizes.values())
        # Estimate tokens at ~4 chars/token.
        estimated_tokens = total_chars // 4
        return {
            "turn": turn,
            "segment_chars": sizes,
            "segment_fingerprints": fps,
            "total_chars": total_chars,
            "estimated_tokens": estimated_tokens,
            "conversation_messages": len(conversation),
        }

    def _conversation_segment(self) -> str:
        """CD-4.3: retrieved conversation snippets (guidance history, user
        clarifications) so the agent can reference prior human decisions.

        Reads ``plan["conversation_snippets"]`` — a list of either plain strings
        or ``{"role": ..., "content": ...}`` dicts. Absent/empty by default; the
        caller (delivery pipeline) is responsible for retrieval/ranking.

        DEDUP: snippets whose content fingerprint matches a live conversation
        message are silently dropped to avoid double-injection.
        """
        snippets = self._plan.get("conversation_snippets") or []
        if not snippets:
            return ""
        # Build fingerprint set from live conversation for dedup.
        live_fps: set[str] = set()
        # Live conversation is not directly available here; use plan hint.
        live_msgs = self._plan.get("_live_conversation_messages") or []
        for msg in live_msgs:
            content = str(msg.get("content") or "").strip()
            if content:
                live_fps.add(_segment_fingerprint(content))
        lines = ["══════ CONVERSATION CONTEXT ══════"]
        for item in snippets:
            if isinstance(item, dict):
                role = str(item.get("role") or "user").strip() or "user"
                text = str(item.get("content") or item.get("text") or "").strip()
                if text and _segment_fingerprint(text) not in live_fps:
                    lines.append(f"[{role}] {text}")
            else:
                text = str(item).strip()
                if text and _segment_fingerprint(text) not in live_fps:
                    lines.append(f"- {text}")
        if len(lines) == 1:
            return ""
        return _clip_lines("\n".join(lines), _BUDGETS["conversation_context"])

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
        return _clip_lines("\n".join(lines), _BUDGETS["evidence_context"])

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
        return _clip_lines("\n".join(lines), _BUDGETS["workspace_state"])

    def _todo_segment(self) -> str:
        if not self._toolkit.todos:
            return "══════ TODOS ══════\n(none yet — create with todo_write)"
        blob = json.dumps(self._toolkit.todos, ensure_ascii=False, indent=2)
        return _clip_json_list("══════ TODOS ══════\n" + blob, _BUDGETS["todo_state"])

    def _failures_segment(self) -> str:
        acceptance = self._plan.get("acceptance_contract") or {}
        gap_reasons = list(acceptance.get("delivery_gap_reasons") or [])
        lessons = list(acceptance.get("failure_lessons") or [])
        constraints = list(acceptance.get("learned_constraints") or [])
        envelopes = list(acceptance.get("failure_envelopes") or [])
        qa_failures = list(
            acceptance.get("live_preview_qa_failures")
            or self._plan.get("live_preview_qa_failures")
            or []
        )
        lines = ["══════ RECENT FAILURES ══════"]
        if qa_failures:
            lines.append("Live preview QA concrete failures (fix these first — do not ignore):")
            lines.extend(f"  - {item}" for item in qa_failures[:8])
        resume_brief = str(acceptance.get("session_resume_brief") or "").strip()
        if resume_brief:
            lines.append(f"Session resume: {resume_brief}")
        session_ws = str(
            acceptance.get("project_agent_session_workspace_uri")
            or self._plan.get("project_agent_session_workspace_uri")
            or ""
        ).strip()
        if session_ws:
            lines.append(f"Durable session workspace: {session_ws}")
        replan_nonce = str(acceptance.get("replan_nonce") or "").strip()
        if replan_nonce:
            lines.append(f"Replan nonce (must change plan inputs): {replan_nonce}")
        if gap_reasons:
            lines.append("Prior delivery gap reasons:")
            lines.extend(f"  - {r}" for r in gap_reasons[:12])
        if constraints:
            lines.append("Learned constraints from prior failures:")
            lines.extend(f"  - {c}" for c in constraints[:12])
        if envelopes:
            lines.append("Build/preview/smoke failure envelopes (fix these first):")
            for env in envelopes[-4:]:
                if not isinstance(env, dict):
                    continue
                stage = env.get("stage") or env.get("failure_stage") or "?"
                summary = (
                    env.get("summary")
                    or env.get("error_summary")
                    or env.get("error")
                    or env.get("message")
                    or env.get("detail")
                    or ""
                )
                lines.append(f"  - stage={stage}: {str(summary)[:400]}")
                evidence = env.get("evidence") if isinstance(env.get("evidence"), dict) else {}
                for fail in list(evidence.get("concrete_failures") or [])[:4]:
                    lines.append(f"      concrete: {str(fail)[:400]}")
                for check in list(evidence.get("failed_checks") or [])[:4]:
                    if not isinstance(check, dict):
                        continue
                    lines.append(
                        f"      check={check.get('name')}: {str(check.get('detail') or '')[:400]}"
                    )
                for key in ("stderr", "stdout", "log_tail"):
                    blob = env.get(key)
                    if isinstance(blob, str) and blob.strip():
                        lines.append(f"      {key}: {blob.strip()[:600]}")
                    elif isinstance(blob, dict) and blob.get("error"):
                        lines.append(f"      {key}.error: {str(blob.get('error'))[:400]}")
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
                avoid = str(lesson.get("avoid") or "").strip()
                if avoid:
                    lines.append(f"      avoid: {avoid[:400]}")
                last_err = str(lesson.get("last_error") or "").strip()
                if last_err:
                    lines.append(f"      last_error: {last_err[:400]}")
                for reason in list(lesson.get("gap_reasons") or [])[:4]:
                    lines.append(f"      gap: {reason}")
                for constraint in list(lesson.get("learned_constraints") or [])[:4]:
                    lines.append(f"      must: {constraint}")
        for gap in self._gaps[:8]:
            lines.append(f"[{gap.code}] {gap.detail}")
            if gap.artifact_snippet:
                lines.append(gap.artifact_snippet[:2_000])
        if len(lines) == 1:
            return ""
        return _clip_lines("\n".join(lines), _BUDGETS["recent_failures"])
