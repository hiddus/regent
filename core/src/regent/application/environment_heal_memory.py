"""Persistent heal preferences — Regent grows which repairs work for which symptoms.

Does not execute code. Only records (reason → successful action ids) and renders
LESSONS.md so the next tick / harness evolution can prefer better repairs.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from regent.application.harness_evolution import lesson_file

MEMORY_NAME = ".regent-environment-heal-memory.json"
SKILL_ID = "ops-environment"
_MAX_INCIDENTS = 40


def memory_path(workspace_root: Path) -> Path:
    return Path(workspace_root).resolve() / MEMORY_NAME


def load_heal_memory(workspace_root: Path) -> dict[str, Any]:
    path = memory_path(workspace_root)
    if not path.is_file():
        return {
            "schema": "regent.environment_heal_memory.v1",
            "preferences": [],
            "incidents": [],
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "schema": "regent.environment_heal_memory.v1",
            "preferences": [],
            "incidents": [],
        }
    if not isinstance(data, dict):
        return {
            "schema": "regent.environment_heal_memory.v1",
            "preferences": [],
            "incidents": [],
        }
    data.setdefault("schema", "regent.environment_heal_memory.v1")
    data.setdefault("preferences", [])
    data.setdefault("incidents", [])
    return data


def save_heal_memory(workspace_root: Path, memory: dict[str, Any]) -> Path:
    path = memory_path(workspace_root)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def _reason_prefix(reason: str) -> str:
    text = str(reason or "").strip()
    if "=" in text:
        return text.split("=", 1)[0].strip()
    if ":" in text:
        return text.split(":", 1)[0].strip()
    return text[:48]


def record_heal_outcome(
    workspace_root: Path,
    *,
    reasons_before: list[str],
    action_ids: list[str],
    improved: bool,
    metrics_before: dict[str, Any],
    metrics_after: dict[str, Any],
) -> dict[str, Any]:
    """Update preferences when a heal actually improved the host."""
    memory = load_heal_memory(workspace_root)
    incident = {
        "at": time.time(),
        "reasons": list(reasons_before)[:8],
        "actions": list(action_ids)[:8],
        "improved": bool(improved),
        "before": metrics_before,
        "after": metrics_after,
    }
    incidents = list(memory.get("incidents") or [])
    incidents.append(incident)
    memory["incidents"] = incidents[-_MAX_INCIDENTS:]

    if improved and action_ids and reasons_before:
        prefs = list(memory.get("preferences") or [])
        for reason in reasons_before:
            prefix = _reason_prefix(reason)
            if not prefix:
                continue
            match = next((p for p in prefs if p.get("reason_prefix") == prefix), None)
            if match is None:
                match = {
                    "reason_prefix": prefix,
                    "prefer_actions": list(action_ids),
                    "successes": 1,
                }
                prefs.append(match)
            else:
                match["successes"] = int(match.get("successes") or 0) + 1
                merged = list(match.get("prefer_actions") or [])
                for aid in action_ids:
                    if aid not in merged:
                        merged.append(aid)
                match["prefer_actions"] = merged[:6]
        memory["preferences"] = prefs
        _append_lesson_if_new(
            workspace_root,
            reasons=reasons_before,
            action_ids=action_ids,
            metrics_after=metrics_after,
        )

    save_heal_memory(workspace_root, memory)
    return memory


def _append_lesson_if_new(
    workspace_root: Path,
    *,
    reasons: list[str],
    action_ids: list[str],
    metrics_after: dict[str, Any],
) -> None:
    """Deterministic LESSONS growth (no LLM). Harness evolution can refine later."""
    root = Path(workspace_root)
    path = lesson_file(root, SKILL_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    reason_key = ", ".join(_reason_prefix(r) for r in reasons[:3])
    marker = f"<!-- heal:{reason_key}|{'+'.join(action_ids)} -->"
    if marker in existing:
        return
    bullet = (
        f"\n## Learned host repair\n"
        f"{marker}\n"
        f"- MUST prefer allowlisted actions `{', '.join(action_ids)}` when host "
        f"reports `{reason_key}`.\n"
        f"- MUST re-measure after repair; refuse generation while unhealthy.\n"
        f"- FORBID inventing shell/commands outside environment-heal-v1 registry.\n"
        f"- Evidence after heal: disk={metrics_after.get('disk_percent')} "
        f"mem={metrics_after.get('mem_percent')} "
        f"venvs={metrics_after.get('preview_venv_count')}.\n"
    )
    if not existing.strip():
        header = (
            "# ops-environment LESSONS\n\n"
            "Evolved by Regent environment heal memory. "
            "Execution stays allowlisted; this file only routes preferences.\n"
        )
        path.write_text(header + bullet, encoding="utf-8")
    else:
        path.write_text(existing.rstrip() + "\n" + bullet, encoding="utf-8")


def read_ops_lessons(workspace_root: Path) -> str:
    return (
        lesson_file(Path(workspace_root), SKILL_ID).read_text(encoding="utf-8")
        if lesson_file(Path(workspace_root), SKILL_ID).is_file()
        else ""
    )
