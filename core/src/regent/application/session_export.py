"""O2: deterministic, redacted session/goal export (Markdown + manifest)."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from regent.application.agent_loop_exit import get_exit, utc_now_iso

SESSION_EXPORT_SCHEMA = "regent.session-export"
SESSION_EXPORT_VERSION = 1

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*\S+"
)


def _redact(text: str) -> str:
    return _SECRET_RE.sub(r"\1=***", text or "")


def build_session_export(
    *,
    goal_id: str,
    metadata: dict[str, Any] | None,
    conversation: list[dict[str, Any]] | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    meta = dict(metadata or {})
    exit_row = get_exit(meta) or {}
    messages = []
    for row in conversation or []:
        if not isinstance(row, dict):
            continue
        messages.append(
            {
                "role": str(row.get("role") or ""),
                "type": str(row.get("message_type") or row.get("type") or ""),
                "content": _redact(str(row.get("content") or ""))[:4000],
            }
        )
    md_lines = [
        f"# Regent session export — goal `{goal_id}`",
        "",
        f"- project: `{project_id or 'n/a'}`",
        f"- exit: `{exit_row.get('exit_kind') or 'n/a'}` / `{exit_row.get('stop_reason') or ''}`",
        f"- execution_mode: `{meta.get('execution_mode') or 'ask'}`",
        f"- quarantine: `{bool(meta.get('quarantine_active'))}`",
        "",
        "## Messages",
        "",
    ]
    for m in messages[-80:]:
        md_lines.append(f"### {m['role']} ({m['type']})")
        md_lines.append("")
        md_lines.append(m["content"] or "_(empty)_")
        md_lines.append("")
    markdown = "\n".join(md_lines)
    manifest = {
        "schema": SESSION_EXPORT_SCHEMA,
        "v": SESSION_EXPORT_VERSION,
        "goal_id": goal_id,
        "project_id": project_id,
        "exit_kind": exit_row.get("exit_kind"),
        "stop_reason": exit_row.get("stop_reason"),
        "message_count": len(messages),
        "open_items": list(dict(exit_row.get("result_bundle") or {}).get("open_items") or [])[:12],
        "at": utc_now_iso(),
    }
    manifest["markdown_sha256"] = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    manifest["digest"] = hashlib.sha256(
        json.dumps({k: v for k, v in manifest.items() if k != "digest"}, sort_keys=True).encode()
    ).hexdigest()
    return {"markdown": markdown, "manifest": manifest}
