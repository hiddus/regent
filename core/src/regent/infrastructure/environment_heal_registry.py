"""Allowlisted environment-heal actions (safe execution surface).

LLM / harness LESSONS may reorder or prefer actions — they must never invent
shell. New repair powers land only as new registered actions (capability bump).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from regent.infrastructure.host_resources import (
    HostResources,
    preview_runtime_root,
    prune_preview_venvs,
    reap_stale_preview_processes,
)

DetectFn = Callable[[HostResources, dict[str, Any]], bool]
RepairFn = Callable[[Path, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class HealAction:
    id: str
    title: str
    risk_tier: str  # low | medium | high
    description: str
    detect: DetectFn
    repair: RepairFn


def _ctx_int(ctx: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(ctx.get(key, default))
    except (TypeError, ValueError):
        return default


def _ctx_float(ctx: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(ctx.get(key, default))
    except (TypeError, ValueError):
        return default


def _detect_venv_excess(resources: HostResources, ctx: dict[str, Any]) -> bool:
    keep = _ctx_int(ctx, "prune_keep_newest", 8)
    return resources.preview_venv_count > keep


def _detect_disk(resources: HostResources, ctx: dict[str, Any]) -> bool:
    return resources.disk_percent >= _ctx_float(ctx, "prune_disk_percent", 80.0)


def _detect_mem(resources: HostResources, ctx: dict[str, Any]) -> bool:
    if resources.mem_percent is None:
        return False
    return resources.mem_percent >= _ctx_float(ctx, "prune_mem_percent", 85.0)


def _detect_pressure(resources: HostResources, ctx: dict[str, Any]) -> bool:
    return (
        _detect_venv_excess(resources, ctx)
        or _detect_disk(resources, ctx)
        or _detect_mem(resources, ctx)
    )


def _repair_reap(workspace_root: Path, ctx: dict[str, Any]) -> dict[str, Any]:
    keep = _ctx_int(ctx, "prune_keep_newest", 8)
    return reap_stale_preview_processes(
        preview_runtime_root(workspace_root), keep_newest=keep
    )


def _repair_prune(workspace_root: Path, ctx: dict[str, Any]) -> dict[str, Any]:
    keep = _ctx_int(ctx, "prune_keep_newest", 8)
    return prune_preview_venvs(preview_runtime_root(workspace_root), keep_newest=keep)


BUILTIN_HEAL_ACTIONS: tuple[HealAction, ...] = (
    HealAction(
        id="reap_stale_previews",
        title="Reap stale preview processes",
        risk_tier="medium",
        description="SIGTERM/KILL Flask/preview PIDs under old runtime dirs.",
        detect=_detect_pressure,
        repair=_repair_reap,
    ),
    HealAction(
        id="prune_preview_venvs",
        title="Prune old preview venvs",
        risk_tier="low",
        description="Delete .preview-venv under older runtime deployments.",
        detect=_detect_pressure,
        repair=_repair_prune,
    ),
)


def list_heal_actions() -> list[dict[str, Any]]:
    return [
        {
            "id": a.id,
            "title": a.title,
            "risk_tier": a.risk_tier,
            "description": a.description,
        }
        for a in BUILTIN_HEAL_ACTIONS
    ]


def _preferred_order(lessons_text: str, memory_prefs: list[dict[str, Any]]) -> list[str]:
    """Order action ids using learned memory then LESSONS keywords."""
    order = [a.id for a in BUILTIN_HEAL_ACTIONS]
    # Memory: bump actions that succeeded for any reason to the front (stable).
    preferred: list[str] = []
    for pref in memory_prefs:
        for aid in pref.get("prefer_actions") or []:
            if aid in order and aid not in preferred:
                preferred.append(str(aid))
    lower = (lessons_text or "").lower()
    if "reap" in lower and "before" in lower and "prune" in lower:
        preferred = ["reap_stale_previews"] + [
            x for x in preferred if x != "reap_stale_previews"
        ]
    if "prune" in lower and "first" in lower:
        preferred = ["prune_preview_venvs"] + [
            x for x in preferred if x != "prune_preview_venvs"
        ]
    rest = [x for x in order if x not in preferred]
    return preferred + rest


def run_selected_heal_actions(
    *,
    workspace_root: Path,
    resources: HostResources,
    ctx: dict[str, Any],
    lessons_text: str = "",
    memory_prefs: list[dict[str, Any]] | None = None,
    enabled_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Run detect→repair for allowlisted actions in evolved preference order."""
    by_id = {a.id: a for a in BUILTIN_HEAL_ACTIONS}
    order = _preferred_order(lessons_text, memory_prefs or [])
    results: list[dict[str, Any]] = []
    for action_id in order:
        action = by_id.get(action_id)
        if action is None:
            continue
        if enabled_ids is not None and action.id not in enabled_ids:
            continue
        if not action.detect(resources, ctx):
            continue
        try:
            outcome = action.repair(workspace_root, ctx)
        except Exception as exc:  # noqa: BLE001 — never abort other actions
            results.append(
                {
                    "id": action.id,
                    "ok": False,
                    "error": str(exc)[:400],
                }
            )
            continue
        results.append({"id": action.id, "ok": True, "outcome": outcome})
    return results
