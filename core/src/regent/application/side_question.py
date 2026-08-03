"""O2: structurally isolated side question (oh-my-cli side-question).

Runner receives only a bounded read-only message snapshot + question.
No workspace writer, Permit, Goal mutator, or tool schemas.
"""

from __future__ import annotations

from typing import Any

SIDE_QUESTION_SCHEMA = "regent.side-question"
SIDE_QUESTION_VERSION = 1
DEFAULT_SIDE_MAX_MESSAGES = 12
DEFAULT_SIDE_MAX_CHARS = 4000


def build_side_context(
    messages: list[dict[str, Any]] | None,
    *,
    max_messages: int = DEFAULT_SIDE_MAX_MESSAGES,
    max_chars: int = DEFAULT_SIDE_MAX_CHARS,
) -> dict[str, Any]:
    rows = [m for m in (messages or []) if isinstance(m, dict)]
    system = rows[0] if rows and str(rows[0].get("role") or "") == "system" else None
    rest = rows[1:] if system else list(rows)
    truncated = len(rest) > max_messages
    recent = rest[-max_messages:] if truncated else rest
    snapshot: list[dict[str, Any]] = []
    if system:
        snapshot.append(
            {
                "role": "system",
                "content": _clamp(str(system.get("content") or ""), max_chars),
            }
        )
    for m in recent:
        snapshot.append(
            {
                "role": str(m.get("role") or "user"),
                "content": _clamp(str(m.get("content") or ""), max_chars),
            }
        )
    return {
        "schema": SIDE_QUESTION_SCHEMA,
        "v": SIDE_QUESTION_VERSION,
        "messages": snapshot,
        "source_message_count": len(rows),
        "included": len(recent),
        "truncated": truncated,
        "system_present": system is not None,
        "tools_disabled": True,
        "workspace_mutable": False,
    }


def side_boundary_note() -> str:
    return (
        "This is a side question asked alongside an in-progress main task. "
        "Answer directly and concisely from the context given. Do not request "
        "or run any tool, do not change files, and do not assume the main "
        "task's plan, goal, or state. The main conversation is not affected."
    )


def build_side_provider_messages(context: dict[str, Any], question: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in list(context.get("messages") or []):
        if isinstance(m, dict):
            out.append(
                {
                    "role": str(m.get("role") or "user"),
                    "content": str(m.get("content") or ""),
                }
            )
    out.append({"role": "system", "content": side_boundary_note()})
    out.append({"role": "user", "content": str(question or "")[:2000]})
    return out


def format_side_context_summary(context: dict[str, Any]) -> str:
    included = int(context.get("included") or 0)
    total = int(context.get("source_message_count") or 0)
    scope = (
        f"last {included} of {total} messages"
        if context.get("truncated")
        else f"{included} message(s)"
    )
    return (
        f"Context (read-only): {scope}. "
        "Tools and workspace changes are disabled; the main task is unaffected."
    )


def _clamp(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


async def run_side_question(
    *,
    question: str,
    context_messages: list[dict[str, Any]] | None,
    answerer: Any | None = None,
) -> dict[str, Any]:
    """Answer without tools. ``answerer`` is optional async (messages) -> str.

    When no answerer is provided, returns a deterministic offline stub so the
    isolation contract remains testable without a provider.
    """
    context = build_side_context(context_messages)
    provider_messages = build_side_provider_messages(context, question)
    text = ""
    reason = "completed"
    ok = True
    if answerer is not None:
        try:
            text = str(await answerer(provider_messages))[:8000]
        except Exception as exc:  # noqa: BLE001 — side path must never raise into Goal
            ok = False
            reason = "provider_error"
            text = str(exc)[:400]
    else:
        text = (
            f"[side-question offline] {format_side_context_summary(context)}\n"
            f"Q: {(question or '')[:400]}"
        )
        reason = "offline_stub"
    return {
        "ok": ok,
        "reason": reason,
        "text": text,
        "context_summary": format_side_context_summary(context),
        "context": {
            "included": context.get("included"),
            "truncated": context.get("truncated"),
            "tools_disabled": True,
        },
        # Explicit isolation markers for audits.
        "mutated_goal": False,
        "mutated_work_plan": False,
        "tools_invoked": False,
    }
