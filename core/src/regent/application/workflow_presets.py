"""O4: named workflow stage presets (hang on Work Plan; not a new Agent loop)."""

from __future__ import annotations

from typing import Any

WORKFLOW_PRESET_SCHEMA = "regent.workflow-preset"
WORKFLOW_PRESET_VERSION = 1

# Admitted sequences only (oh-my-cli autopilot profile spirit; Regent Work Plan).
_ADMITTED: dict[str, list[str]] = {
    "plan-exec": ["ralplan", "execution"],
    "plan-exec-ralph": ["ralplan", "execution", "ralph"],
    "plan-build-qa": ["ralplan", "execution", "qa"],
    "plan-build-ralph-qa": ["ralplan", "execution", "ralph", "qa"],
}

META_WORKFLOW_PRESET = "workflow_preset"


def list_workflow_presets() -> list[dict[str, Any]]:
    return [
        {
            "schema": WORKFLOW_PRESET_SCHEMA,
            "v": WORKFLOW_PRESET_VERSION,
            "name": name,
            "stages": list(stages),
        }
        for name, stages in _ADMITTED.items()
    ]


def resolve_workflow_preset(name: str) -> dict[str, Any]:
    key = str(name or "").strip()
    if key not in _ADMITTED:
        raise ValueError(f"unknown workflow preset: {key}")
    return {
        "schema": WORKFLOW_PRESET_SCHEMA,
        "v": WORKFLOW_PRESET_VERSION,
        "name": key,
        "stages": list(_ADMITTED[key]),
    }


def apply_workflow_preset(metadata: dict[str, Any], name: str) -> dict[str, Any]:
    preset = resolve_workflow_preset(name)
    meta = dict(metadata or {})
    meta[META_WORKFLOW_PRESET] = preset
    # Soft hint for plan UI — does not invent ExecutionPlanItems by itself.
    meta["workflow_preset_name"] = preset["name"]
    return meta


def get_workflow_preset(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    raw = dict(metadata or {}).get(META_WORKFLOW_PRESET)
    return dict(raw) if isinstance(raw, dict) else None
