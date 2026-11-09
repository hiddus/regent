"""Dual-track context compression: microCompact + autoCompact (P1-1)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import BudgetExhaustedError, ChatMessage, ToolCall


# Window default 128k tokens → leave 15k buffer.
# Token estimate: CJK ≈ 1.0 tok/char; other ≈ 0.25 (≈4 chars/token). W4-P0.
DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
AUTOCOMPACT_BUFFER_TOKENS = 15_000
POST_COMPACT_MAX_FILES = 5
MICRO_KEEP_RECENT_TOOLS = 8
AUTOCOMPACT_FAIL_LIMIT = 3


class Summarizer(Protocol):
    async def summarize(self, text: str) -> str: ...


@dataclass
class CompactState:
    auto_failures: int = 0
    auto_successes: int = 0
    last_summary: str = ""
    last_structured_summary: dict[str, Any] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)
    # EMA scale from provider prompt_tokens / local estimate (W4-P0).
    token_scale: float = 1.0


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0x3040 <= o <= 0x30FF
        or 0xAC00 <= o <= 0xD7AF
        or 0xF900 <= o <= 0xFAFF
        or 0x3000 <= o <= 0x303F
    )


def estimate_text_tokens(text: str) -> int:
    """CJK-aware char→token estimate (W4-P0)."""
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if _is_cjk(ch):
            cjk += 1
        else:
            other += 1
    return max(1, int(cjk * 1.0 + other * 0.25 + 0.999))


def estimate_tokens(messages: list[ChatMessage]) -> int:
    total = 0
    for msg in messages:
        if msg.content:
            total += estimate_text_tokens(msg.content)
        # Count reasoning_content tokens (retained reasoning from model thinking).
        reasoning = getattr(msg, "reasoning_content", None)
        if reasoning:
            total += estimate_text_tokens(reasoning)
        for call in msg.tool_calls:
            total += estimate_text_tokens(
                json.dumps(call.arguments, ensure_ascii=False)
            )
            total += 8
    return total


# Tool calls whose arguments often embed full file bodies (D6 / prompt-cache).
_FILE_BODY_TOOLS = frozenset({"write_file", "edit_file", "create_file", "apply_patch"})
_CONTENT_ARG_KEYS = frozenset({"content", "new_content", "old_content", "patch", "diff", "body"})


def _strip_file_body_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep path/metadata; replace large file bodies with a reload hint."""
    out: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in _CONTENT_ARG_KEYS and isinstance(value, str) and len(value) > 80:
            path = arguments.get("path") or arguments.get("file") or arguments.get("file_path")
            hint = f"[cleared — re-read via read_file{f' path={path}' if path else ''}]"
            out[key] = hint
        else:
            out[key] = value
    return out


def micro_compact(
    messages: list[ChatMessage],
    *,
    keep_recent: int = MICRO_KEEP_RECENT_TOOLS,
) -> list[ChatMessage]:
    """Clear old tool results and strip stale write/edit file bodies from history.

    File contents remain on disk; the model should call ``read_file`` when needed.
    Keeping recent N tool results (and their paired assistant write args) intact.
    """
    tool_indices = [i for i, m in enumerate(messages) if m.role == "tool"]
    clear_tool_set = (
        set(tool_indices[:-keep_recent]) if len(tool_indices) > keep_recent else set()
    )

    # Assistant turns that issued file-body tools — strip args on older ones.
    file_write_assistant_indices = [
        i
        for i, m in enumerate(messages)
        if m.role == "assistant"
        and any(c.name in _FILE_BODY_TOOLS for c in (m.tool_calls or []))
    ]
    clear_write_set = (
        set(file_write_assistant_indices[:-keep_recent])
        if len(file_write_assistant_indices) > keep_recent
        else set()
    )

    assistant_indices = [i for i, m in enumerate(messages) if m.role == "assistant"]
    stale_reasoning = set(assistant_indices[:-2])
    compacted: list[ChatMessage] = []
    for i, msg in enumerate(messages):
        if i in clear_tool_set and msg.content and msg.content != "[cleared]":
            compacted.append(
                ChatMessage(
                    role="tool",
                    content="[cleared]",
                    tool_call_id=msg.tool_call_id,
                    name=msg.name,
                    reasoning_content=None,
                )
            )
            continue
        if i in clear_write_set and msg.tool_calls:
            new_calls: list[ToolCall] = []
            changed = False
            for call in msg.tool_calls:
                if call.name in _FILE_BODY_TOOLS:
                    stripped = _strip_file_body_args(dict(call.arguments or {}))
                    if stripped != call.arguments:
                        changed = True
                        new_calls.append(
                            ToolCall(id=call.id, name=call.name, arguments=stripped)
                        )
                        continue
                new_calls.append(call)
            if changed:
                compacted.append(
                    ChatMessage(
                        role="assistant",
                        content=msg.content,
                        tool_calls=new_calls,
                        tool_call_id=msg.tool_call_id,
                        name=msg.name,
                        reasoning_content=None,
                    )
                )
                continue
        if i in stale_reasoning and getattr(msg, "reasoning_content", None):
            compacted.append(
                ChatMessage(
                    role=msg.role,
                    content=msg.content,
                    tool_calls=list(msg.tool_calls or []),
                    tool_call_id=msg.tool_call_id,
                    name=msg.name,
                    reasoning_content=None,
                )
            )
            continue
        compacted.append(msg)
    return compacted


@dataclass
class AutoCompactResult:
    messages: list[ChatMessage]
    did_compact: bool
    summary: str = ""
    failed: bool = False


class ContextCompactor:
    """autoCompact when near window; circuit-break after consecutive failures."""

    def __init__(
        self,
        *,
        toolkit: WorkspaceToolkit,
        summarizer: Summarizer | None = None,
        context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
        buffer_tokens: int = AUTOCOMPACT_BUFFER_TOKENS,
    ) -> None:
        self._toolkit = toolkit
        self._summarizer = summarizer
        self._window = context_window_tokens
        self._buffer = buffer_tokens
        self.state = CompactState()

    @property
    def threshold_tokens(self) -> int:
        return max(100, self._window - self._buffer)

    def calibrated_estimate(self, messages: list[ChatMessage]) -> int:
        raw = estimate_tokens(messages)
        scale = float(self.state.token_scale or 1.0)
        return max(1, int(raw * scale + 0.999))

    def observe_provider_prompt_tokens(
        self, *, estimated: int, actual_prompt_tokens: int
    ) -> None:
        """Close the loop with provider usage (EMA)."""
        if estimated <= 0 or actual_prompt_tokens <= 0:
            return
        ratio = float(actual_prompt_tokens) / float(estimated)
        # Clamp pathological spikes from tiny estimates.
        ratio = min(4.0, max(0.5, ratio))
        prev = float(self.state.token_scale or 1.0)
        self.state.token_scale = 0.7 * prev + 0.3 * ratio

    def needs_auto_compact(self, messages: list[ChatMessage]) -> bool:
        return self.calibrated_estimate(messages) >= self.threshold_tokens

    async def maybe_auto_compact(
        self,
        messages: list[ChatMessage],
        *,
        goal_anchor: str,
        todos: list[dict[str, Any]],
    ) -> AutoCompactResult:
        if not self.needs_auto_compact(messages):
            return AutoCompactResult(messages=messages, did_compact=False)

        if self.state.auto_failures >= AUTOCOMPACT_FAIL_LIMIT:
            raise BudgetExhaustedError(
                f"autoCompact failed {self.state.auto_failures} times consecutively"
            )

        try:
            summary = await self._build_summary(messages)
            restored = self._post_compact_messages(
                summary=summary,
                goal_anchor=goal_anchor,
                todos=todos,
            )
            self.state.auto_failures = 0
            self.state.auto_successes += 1
            self.state.last_summary = summary
            self.state.history.append(summary[:500])
            return AutoCompactResult(
                messages=restored,
                did_compact=True,
                summary=summary,
            )
        except Exception as exc:  # noqa: BLE001 — count toward circuit breaker
            self.state.auto_failures += 1
            if self.state.auto_failures >= AUTOCOMPACT_FAIL_LIMIT:
                raise BudgetExhaustedError(
                    f"autoCompact circuit open after {self.state.auto_failures} failures: {exc}"
                ) from exc
            return AutoCompactResult(
                messages=messages,
                did_compact=False,
                failed=True,
                summary=str(exc),
            )

    async def _build_summary(self, messages: list[ChatMessage]) -> str:
        blob_parts: list[str] = []
        for msg in messages:
            if msg.role == "system":
                continue
            if msg.role == "tool" and msg.content == "[cleared]":
                continue
            prefix = msg.role
            if msg.name:
                prefix = f"tool:{msg.name}"
            text = (msg.content or "")[:2_000]
            if text:
                blob_parts.append(f"[{prefix}] {text}")
        blob = "\n".join(blob_parts[-40:])
        if self._summarizer is not None:
            return await self._summarizer.summarize(blob)
        # Deterministic fallback summarizer (no LLM required).
        lines = blob.splitlines()
        head = lines[:20]
        tail = lines[-20:] if len(lines) > 40 else []
        return (
            "Conversation compact summary (heuristic):\n"
            + "\n".join(head)
            + ("\n...\n" + "\n".join(tail) if tail else "")
        )

    def _post_compact_messages(
        self,
        *,
        summary: str,
        goal_anchor: str,
        todos: list[dict[str, Any]],
        hard_constraints: list[str] | None = None,
        permit_state: dict[str, Any] | None = None,
        open_human_tasks: list[str] | None = None,
        produced_artifacts: list[str] | None = None,
        open_risks: list[str] | None = None,
        next_actions: list[str] | None = None,
        plan_checkpoint_ref: str | None = None,
    ) -> list[ChatMessage]:
        from regent.application.context_artifact import build_structured_compact_summary

        structured = build_structured_compact_summary(
            goal_intent=goal_anchor,
            produced_artifacts=produced_artifacts,
            open_risks=open_risks,
            next_actions=next_actions
            or [
                str(t.get("content") or t.get("id") or "")
                for t in todos
                if str(t.get("status") or "") in {"pending", "in_progress"}
            ],
            hard_constraints=hard_constraints,
            permit_state=permit_state,
            open_human_tasks=open_human_tasks,
            plan_checkpoint_ref=plan_checkpoint_ref,
            heuristic_blob=summary,
        )
        self.state.last_structured_summary = structured.as_dict()
        files_blob: list[str] = []
        for rel in self._toolkit.recent_writes[-POST_COMPACT_MAX_FILES:]:
            try:
                content = self._toolkit.read_text(rel, max_chars=3_000)
            except (OSError, ValueError, FileNotFoundError):
                continue
            files_blob.append(f"--- {rel} ---\n{content}")
        user = "\n\n".join(
            [
                "══════ POST-COMPACT REHYDRATION ══════",
                goal_anchor,
                "STRUCTURED_SUMMARY:\n"
                + json.dumps(structured.as_dict(), ensure_ascii=False, indent=2),
                "TODOS:\n" + json.dumps(todos, ensure_ascii=False, indent=2),
                "HARD_CONSTRAINTS:\n"
                + json.dumps(list(structured.hard_constraints), ensure_ascii=False),
                "PERMIT_STATE:\n"
                + json.dumps(dict(structured.permit_state), ensure_ascii=False),
                "OPEN_HUMAN_TASKS:\n"
                + json.dumps(list(structured.open_human_tasks), ensure_ascii=False),
                "OPEN_RISKS_AND_FAILURES:\n"
                + json.dumps(list(structured.open_risks), ensure_ascii=False),
                "RECENT FILES:\n" + ("\n".join(files_blob) or "(none)"),
                "PRIOR SUMMARY:\n" + summary,
                "Continue from this state. Do not re-ask already settled questions. "
                "Re-read workspace files as needed instead of relying on cleared tool args.",
            ]
        )
        return [
            ChatMessage(
                role="system",
                content=(
                    "Context was compacted to stay within the token budget. "
                    "Trust the rehydrated goal, structured summary, todos, "
                    "hard constraints, permit state, recent files, and summary."
                ),
            ),
            ChatMessage(role="user", content=user),
        ]


class HeuristicSummarizer:
    """Cheap deterministic summarizer used when no LLM summarizer is injected."""

    async def summarize(self, text: str) -> str:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        keep = lines[:30] + (["..."] + lines[-15:] if len(lines) > 50 else [])
        return "Heuristic summary:\n" + "\n".join(keep)


class LLMSummarizer:
    """LLM-based context summarizer (Codex Harness context compaction pattern).

    Uses the same ChatProvider as the main agent loop.  Falls back to
    HeuristicSummarizer on any failure so compaction never blocks the loop.
    """

    _SUMMARY_PROMPT = (
        "You are a context compression assistant.  Given a conversation log from "
        "a coding agent, produce a structured summary that preserves:\n"
        "1. The user's original goal/intent\n"
        "2. Key decisions already made and their rationale\n"
        "3. Files created or modified (with paths)\n"
        "4. Errors encountered and how they were resolved\n"
        "5. Current work-in-progress and next steps\n"
        "6. Hard constraints discovered (e.g. API limits, format requirements)\n"
        "7. Any reasoning chains that led to important conclusions\n\n"
        "Output a concise structured summary.  Omit routine tool calls and "
        "boilerplate.  Keep it under 2000 characters."
    )

    def __init__(self, provider: Any, *, fallback: HeuristicSummarizer | None = None) -> None:
        self._provider = provider
        self._fallback = fallback or HeuristicSummarizer()

    async def summarize(self, text: str) -> str:
        from regent.agent.types import ChatMessage

        try:
            response = await self._provider.chat(
                messages=[
                    ChatMessage(role="system", content=self._SUMMARY_PROMPT),
                    ChatMessage(role="user", content=f"Summarize this conversation:\n\n{text[:12_000]}"),
                ],
                tools=None,
                temperature=0.0,
            )
            summary = (response.message.content or "").strip()
            if summary:
                return summary
        except Exception:  # noqa: BLE001 — never block compaction
            pass
        return await self._fallback.summarize(text)
