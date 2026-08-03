"""A0 Agent Loop exit contract: COMPLETE | STOP | ASK_HUMAN (no RETRY_FOREVER).

Every Agent lease must persist ``goal.metadata_json["agent_loop_exit"]`` with
exactly one exit_kind. Session continuity is allowed only after human answer
or explicit CONTINUE — never as silent auto-resume after verification failure.

Design refs: OpenWork (artifacts / permission / abort) + Claude Code
(AskUserQuestion / hard budget / doom_loop → ask).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

ExitKind = Literal["COMPLETE", "STOP", "ASK_HUMAN"]

META_EXIT_KEY = "agent_loop_exit"
META_DOOM_STREAK = "agent_loop_doom_streak"
META_WORKSPACE_HASH = "agent_loop_workspace_hash"

# Heuristics (Phase 3)
DOOM_SAME_GAP_STREAK = 2
DOOM_RESUME_WITHOUT_COMPLETE = 3


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_result_bundle(
    *,
    summary: str,
    preview_url: str | None = None,
    artifact_uri: str | None = None,
    evidence_summary: str | None = None,
    change_points: list[str] | None = None,
    open_items: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "summary": (summary or "")[:500],
        "preview_url": preview_url,
        "artifact_uri": artifact_uri,
        "evidence_summary": (evidence_summary or "")[:800] or None,
        "change_points": list(change_points or [])[:5],
        "open_items": list(open_items or [])[:12],
    }


def build_ask_envelope(
    *,
    question: str,
    why_blocked: str,
    options: list[dict[str, str]] | None = None,
    suggested: str | None = None,
    deny_consequence: str | None = None,
    ask_type: str = "delivery_gap",
    gap_kind: str | None = None,
    gap_reasons: list[str] | None = None,
) -> dict[str, Any]:
    opts = options or [
        {"id": "continue_fix", "label": "继续修复（补充方向后同 Session 续跑）"},
        {"id": "stop", "label": "停止本轮"},
    ]
    return {
        "ask_type": ask_type,
        "question": (question or "")[:800],
        "why_blocked": (why_blocked or "")[:400],
        "options": opts[:8],
        "suggested": suggested or "continue_fix",
        "deny_consequence": deny_consequence
        or "拒绝后本轮停止，草稿保留；不会自动再烧额度。",
        "resume_hint": "答复后将在同一 Agent Session 工作区继续。",
        "gap_kind": gap_kind,
        "gap_reasons": list(gap_reasons or [])[:12],
        "answered": False,
        "answer": None,
        "answered_at": None,
    }


def build_exit(
    *,
    exit_kind: ExitKind,
    stop_reason: str,
    lease_id: str | UUID | None = None,
    session_id: str | UUID | None = None,
    epoch: int | None = None,
    result_bundle: dict[str, Any] | None = None,
    ask_envelope: dict[str, Any] | None = None,
    draft_uri: str | None = None,
) -> dict[str, Any]:
    if exit_kind not in {"COMPLETE", "STOP", "ASK_HUMAN"}:
        raise ValueError(f"invalid exit_kind: {exit_kind}")
    payload: dict[str, Any] = {
        "exit_kind": exit_kind,
        "stop_reason": str(stop_reason or "unspecified")[:128],
        "lease_id": str(lease_id) if lease_id else None,
        "session_id": str(session_id) if session_id else None,
        "epoch": int(epoch) if epoch is not None else None,
        "at": utc_now_iso(),
        "result_bundle": result_bundle,
        "ask_envelope": ask_envelope,
        "draft_uri": draft_uri,
    }
    return payload


def apply_exit_to_metadata(
    metadata: dict[str, Any],
    exit_payload: dict[str, Any],
) -> dict[str, Any]:
    """Stamp exit onto goal metadata (single source for UI / resume gates)."""
    meta = dict(metadata or {})
    meta[META_EXIT_KEY] = exit_payload
    kind = str(exit_payload.get("exit_kind") or "")
    if kind == "ASK_HUMAN":
        meta["awaiting_human_intervention"] = True
        meta["execution_stage"] = "DELIVERY_SOFT_PAUSE"
        ask = dict(exit_payload.get("ask_envelope") or {})
        if ask:
            meta["pending_agent_loop_ask"] = ask
    elif kind == "STOP":
        meta["awaiting_human_intervention"] = False
        meta["execution_stage"] = "DELIVERY_SOFT_PAUSE"
        meta.pop("pending_agent_loop_ask", None)
    elif kind == "COMPLETE":
        meta["awaiting_human_intervention"] = False
        meta.pop("pending_agent_loop_ask", None)
        # Do not auto-ACHIEVE here — Goal state machine is separate (Q4).
        if not meta.get("execution_stage") or meta.get("execution_stage") in {
            "GENERATING",
            "DELIVERY_SOFT_PAUSE",
        }:
            meta["execution_stage"] = "AGENT_LOOP_COMPLETE"
    if exit_payload.get("draft_uri"):
        meta["last_good_draft_uri"] = exit_payload["draft_uri"]
    return meta


def get_exit(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    raw = dict(metadata or {}).get(META_EXIT_KEY)
    return dict(raw) if isinstance(raw, dict) else None


def has_unanswered_ask(metadata: dict[str, Any] | None) -> bool:
    exit_row = get_exit(metadata)
    if not exit_row or exit_row.get("exit_kind") != "ASK_HUMAN":
        return False
    ask = dict(exit_row.get("ask_envelope") or metadata.get("pending_agent_loop_ask") or {})
    return not bool(ask.get("answered"))


def mark_ask_answered(
    metadata: dict[str, Any],
    *,
    answer: str,
    option_id: str | None = None,
) -> dict[str, Any]:
    meta = dict(metadata or {})
    exit_row = dict(get_exit(meta) or {})
    ask = dict(exit_row.get("ask_envelope") or meta.get("pending_agent_loop_ask") or {})
    ask["answered"] = True
    ask["answer"] = (answer or "")[:800]
    ask["option_id"] = option_id
    ask["answered_at"] = utc_now_iso()
    exit_row["ask_envelope"] = ask
    exit_row["exit_kind"] = "ASK_HUMAN"  # historical; resume clears via new lease exit
    meta[META_EXIT_KEY] = exit_row
    meta["pending_agent_loop_ask"] = ask
    meta["awaiting_human_intervention"] = False
    meta["human_resume_nonce"] = f"ask-answered:{utc_now_iso()}"
    return meta


def detect_doom_loop(
    metadata: dict[str, Any],
    *,
    gap_kind: str,
    workspace_hash: str | None = None,
) -> tuple[bool, str]:
    """Return (is_doom, reason)."""
    meta = dict(metadata or {})
    prior_kind = str(meta.get("delivery_gap_kind") or "")
    streak = int(meta.get("delivery_gap_kind_streak") or 0)
    if prior_kind == gap_kind:
        streak += 1
    else:
        streak = 1
    resumes = int(meta.get("session_resume_attempts") or 0)
    prior_hash = str(meta.get(META_WORKSPACE_HASH) or "")
    if streak >= DOOM_SAME_GAP_STREAK and resumes >= 1:
        return True, f"doom_loop:same_gap:{gap_kind}:streak={streak}"
    if (
        workspace_hash
        and prior_hash
        and workspace_hash == prior_hash
        and resumes >= 1
    ):
        return True, "doom_loop:workspace_unchanged"
    if resumes >= DOOM_RESUME_WITHOUT_COMPLETE:
        exit_row = get_exit(meta)
        if not exit_row or exit_row.get("exit_kind") != "COMPLETE":
            return True, f"doom_loop:resumes_without_complete:{resumes}"
    return False, ""


def conversation_copy_for_exit(exit_payload: dict[str, Any]) -> tuple[str, str]:
    """Return (message_type, content) for assistant conversation append."""
    kind = str(exit_payload.get("exit_kind") or "")
    reason = str(exit_payload.get("stop_reason") or "")
    if kind == "COMPLETE":
        bundle = dict(exit_payload.get("result_bundle") or {})
        open_items = bundle.get("open_items") or []
        lines = [
            "本轮 Agent 已完成（COMPLETE）。",
            str(bundle.get("summary") or "已提交本轮结果。"),
        ]
        if bundle.get("preview_url"):
            lines.append(f"预览：{bundle['preview_url']}")
        if open_items:
            lines.append("未决项：" + "；".join(str(x) for x in open_items[:6]))
        return "AGENT_LOOP_COMPLETE", "\n".join(lines)
    if kind == "STOP":
        draft = exit_payload.get("draft_uri") or ""
        lines = [
            f"本轮已停止（STOP）：{reason or '已终止'}。",
            "不会自动再消耗额度。",
        ]
        if draft:
            lines.append("草稿已保留，可在源码/产物区查看。")
        return "AGENT_LOOP_STOP", "\n".join(lines)
    # ASK_HUMAN
    ask = dict(exit_payload.get("ask_envelope") or {})
    lines = [
        "需要你确认后才能继续（ASK_HUMAN）。",
        str(ask.get("question") or "请补充方向或选择下一步。"),
        f"原因：{ask.get('why_blocked') or reason}",
    ]
    for opt in list(ask.get("options") or [])[:4]:
        if isinstance(opt, dict):
            lines.append(f"- {opt.get('id')}: {opt.get('label')}")
    lines.append(str(ask.get("resume_hint") or "答复后将在同一 Session 继续。"))
    return "AGENT_LOOP_ASK", "\n".join(lines)
