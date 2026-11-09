"""CD-4.4: capabilities → ToolSpec discovery (minimal, not runner-wired).

Scans ``capabilities/**/capability.json`` for packages that declare a
``parameters`` JSON-schema field, and turns each into a ``ToolSpec`` the way a
provider tool-calling loop would expect. Capabilities without ``parameters``
are certification/verification packages, not callable tools, and are skipped.

This module is intentionally standalone: it is not yet injected into
``AgentRunner`` or any chat+tools provider. It exists so capability authors can
opt in to tool-calling by adding a ``parameters`` schema, and so the discovery
logic has test coverage ahead of that wiring.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from regent.model.chat import ToolSpec

_ENV_OVERRIDE = "REGENT_CAPABILITIES_DIR"


def default_capabilities_root() -> Path:
    """Resolve the repo-root ``capabilities/`` directory.

    Honors ``REGENT_CAPABILITIES_DIR`` for tests/alternate layouts; otherwise
    walks up from this file (core/src/regent/agent/capability_tools.py) to the
    repo root.
    """
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[4] / "capabilities"


def _iter_capability_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(root.rglob("capability.json"))


def _tool_name(raw_name: str, fallback: str) -> str:
    base = (raw_name or fallback).strip().lower() or fallback
    slug = "".join(ch if ch.isalnum() else "_" for ch in base)
    while "__" in slug:
        slug = slug.replace("__", "_")
    slug = slug.strip("_") or fallback
    return f"capability_{slug}"


def load_capability_tool_specs(root: Path | None = None) -> list[ToolSpec]:
    """Build ``ToolSpec`` entries for capabilities that declare ``parameters``.

    Silently skips capability.json files that are missing, malformed, or that
    have no ``parameters`` dict — this is a best-effort discovery helper, not a
    validation gate.
    """
    capabilities_root = root if root is not None else default_capabilities_root()
    specs: list[ToolSpec] = []
    for path in _iter_capability_files(capabilities_root):
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        parameters = raw.get("parameters")
        if not isinstance(parameters, dict):
            continue
        name = str(raw.get("name") or path.parent.name)
        description = str(raw.get("description") or f"Capability {name}")
        specs.append(
            ToolSpec(
                name=_tool_name(name, path.parent.name),
                description=description,
                parameters=parameters,
            )
        )
    return specs
