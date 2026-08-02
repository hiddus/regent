"""DiagnosticDelivery v1 — user-facing failure handoff (not a permission card).

When generation soft-pauses or exhausts budget, promote sandbox leftovers into a
durable, goal-scoped delivery object the console can render and resume from.
Never expose file:// to the frontend; use snapshot_id + artifact://.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from regent.agent.accepted_workspace import write_recoverable_workspace_snapshot

DIAGNOSTIC_DELIVERY_SCHEMA = "diagnostic-delivery/v1"


def _path_from_uri(uri: str | None) -> Path | None:
    raw = str(uri or "").strip()
    if not raw:
        return None
    if raw.startswith("file:"):
        parsed = urlparse(raw)
        path = unquote(parsed.path)
        import os

        if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        elif os.name == "nt" and parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        return Path(path)
    p = Path(raw)
    return p if p.exists() else None


def _count_workspace_files(root: Path) -> int:
    n = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(root).as_posix()
            if rel.startswith(".regent"):
                continue
            n += 1
    return n


def _read_budget_sidecar(workspace: Path | None) -> dict[str, Any]:
    if workspace is None:
        return {}
    for name in (".regent_budget_exhausted.json", ".regent_run_ledger.json"):
        path = workspace / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if name.endswith("budget_exhausted.json"):
            ledger = dict(data.get("ledger") or {})
            return {
                "turns_used": ledger.get("turns_used") or data.get("turns_used"),
                "turns_limit": ledger.get("turns_limit") or data.get("turns_limit"),
                "tokens_used": ledger.get("tokens_used") or data.get("tokens_used"),
                "tokens_limit": ledger.get("tokens_limit") or data.get("tokens_limit"),
                "elapsed_seconds": ledger.get("elapsed_seconds") or data.get("elapsed_seconds"),
                "primary_failure_code": data.get("primary_failure_code") or "BUDGET_EXHAUSTED",
            }
        return {
            "turns_used": data.get("turns_used") or data.get("turn"),
            "tokens_used": data.get("prompt_tokens") or data.get("tokens_used"),
            "tokens_limit": data.get("tokens_limit"),
        }
    return {}


def default_recommendations(*, terminal_reason: str) -> list[dict[str, str]]:
    code = (terminal_reason or "").upper()
    options = [
        {
            "id": "continue_current",
            "label": "继续修复当前版本",
            "description": "保留现有文件，只修阻止交付的问题",
            "action": "CONTINUE_FROM_SNAPSHOT",
        },
        {
            "id": "narrow_scope",
            "label": "缩小范围后继续",
            "description": "先交付核心页面和主流程",
            "action": "REVISE_SCOPE",
        },
        {
            "id": "inspect",
            "label": "查看并下载当前成果",
            "description": "打开源码树与诊断包（不启动新一轮生成）",
            "action": "INSPECT_CURRENT_RESULT",
        },
    ]
    if code == "BUDGET_EXHAUSTED":
        options.insert(
            0,
            {
                "id": "continue_budget",
                "label": "继续修复当前版本",
                "description": "从已保存快照续跑；当前草稿不会被当作正式交付",
                "action": "CONTINUE_FROM_SNAPSHOT",
            },
        )
        # Deduplicate continue_current
        options = [options[0], options[2], options[3]]
    return options


def build_diagnostic_delivery(
    *,
    goal_id: uuid.UUID | str,
    generation_run_id: uuid.UUID | str | None = None,
    terminal_reason: str,
    gap_kind: str | None = None,
    reasons: list[str] | None = None,
    draft_uri: str | None = None,
    preview_endpoint: str | None = None,
    workspace_root: Path | str | None = None,
    summary: str | None = None,
    attempts: int | None = None,
) -> dict[str, Any]:
    """Build + optionally persist a recoverable snapshot; return Console-safe payload."""
    reasons = [str(r).strip() for r in (reasons or []) if str(r).strip()][:12]
    terminal = (terminal_reason or gap_kind or "DELIVERY_SOFT_PAUSE").upper()
    if any(r.upper().startswith("BUDGET_EXHAUSTED") for r in reasons):
        terminal = "BUDGET_EXHAUSTED"

    source = _path_from_uri(draft_uri)
    snapshot_id: str | None = None
    snapshot_uri: str | None = None
    file_count = 0
    if source is not None and source.is_dir():
        file_count = _count_workspace_files(source)
        dest_root = Path(workspace_root) if workspace_root else source.parent
        try:
            snapshot_uri = write_recoverable_workspace_snapshot(
                source,
                dest_root,
                reason=terminal.lower(),
                include_diagnostics=True,
            )
            # extract id from .../recoverable_workspace_snapshots/{id}
            snap_path = _path_from_uri(snapshot_uri)
            if snap_path is not None:
                snapshot_id = snap_path.name
                file_count = max(file_count, _count_workspace_files(snap_path))
        except OSError:
            snapshot_uri = draft_uri
            snapshot_id = None

    budget = _read_budget_sidecar(source) or _read_budget_sidecar(
        _path_from_uri(snapshot_uri)
    )
    if budget.get("primary_failure_code"):
        terminal = str(budget["primary_failure_code"]).upper()

    findings = [
        {
            "code": (r.split(":", 1)[0].strip() if ":" in r else r)[:64],
            "title": r[:120],
            "detail": r[:400],
            "severity": "blocking",
        }
        for r in reasons[:3]
    ]

    preview_state = "UNAVAILABLE"
    preview_reason = "本轮未生成可运行 Preview"
    if preview_endpoint:
        preview_state = "VERIFIED"
        preview_reason = "上一验证版仍可打开"
    elif file_count > 0:
        preview_reason = "应用未通过启动/交付验证；源码已保存为未验证草稿"

    if not summary:
        if terminal == "BUDGET_EXHAUSTED":
            summary = "本轮资源已用尽，当前代码和诊断已保存，不会被当作正式交付。"
        else:
            summary = (
                "自动修复已暂停。当前成果已保存为未验证草稿；"
                "可查看代码、下载诊断，或补充方向后继续。"
            )

    artifacts: list[dict[str, Any]] = []
    if snapshot_id:
        artifacts.append(
            {
                "kind": "source_snapshot",
                "snapshot_id": snapshot_id,
                "download_hint": "workspace",
                "file_count": file_count,
            }
        )
    for kind, fname in (
        ("diagnostic_report", ".regent_budget_exhausted.json"),
        ("run_ledger", ".regent_run_ledger.json"),
        ("transcript", ".regent_agent_transcript.json"),
    ):
        base = _path_from_uri(snapshot_uri) or source
        if base is None:
            continue
        path = base / fname
        if path.is_file():
            raw = path.read_bytes()
            artifacts.append(
                {
                    "kind": kind,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size": len(raw),
                    "name": fname,
                }
            )

    delivery_id = str(uuid.uuid4())
    return {
        "id": delivery_id,
        "schema_version": DIAGNOSTIC_DELIVERY_SCHEMA,
        "goal_id": str(goal_id),
        "generation_run_id": str(generation_run_id) if generation_run_id else None,
        "terminal_reason": terminal,
        "status": "DELIVERED_FOR_REVIEW",
        "resumable": True,
        "promote_allowed": False,
        "summary": summary,
        "gap_kind": gap_kind,
        "attempts": attempts,
        "created_at": datetime.now(UTC).isoformat(),
        "budget": {
            k: v
            for k, v in budget.items()
            if k != "primary_failure_code" and v is not None
        },
        "artifacts": artifacts,
        "preview": {
            "state": preview_state,
            "reason": preview_reason,
            "last_verified_endpoint": preview_endpoint or None,
        },
        "findings": findings,
        "recommendations": default_recommendations(terminal_reason=terminal),
        "resume": {
            "base_snapshot_id": snapshot_id,
            "allowed_actions": [
                "CONTINUE_FROM_SNAPSHOT",
                "REVISE_SCOPE",
                "INSPECT_CURRENT_RESULT",
                "STOP",
            ],
        },
        # Internal only — stripped from API responses if needed; Console uses snapshot_id.
        "_snapshot_uri": snapshot_uri,
    }


def public_diagnostic_delivery(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop internal keys before conversation / API projection."""
    out = {k: v for k, v in payload.items() if not str(k).startswith("_")}
    return out
