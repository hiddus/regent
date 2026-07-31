"""CD-4.4: capabilities → ToolSpec discovery."""

from __future__ import annotations

import json
from pathlib import Path

from regent.agent.capability_tools import load_capability_tool_specs


def _write_capability(root: Path, slug: str, payload: dict) -> None:
    directory = root / slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "capability.json").write_text(json.dumps(payload), encoding="utf-8")


def test_capability_with_parameters_becomes_tool_spec(tmp_path: Path) -> None:
    _write_capability(
        tmp_path,
        "http-fetch-v1",
        {
            "name": "http-fetch-v1",
            "description": "Fetch an allowlisted URL.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    )
    specs = load_capability_tool_specs(root=tmp_path)
    assert len(specs) == 1
    spec = specs[0]
    assert spec.name == "capability_http_fetch_v1"
    assert spec.description == "Fetch an allowlisted URL."
    assert spec.parameters["required"] == ["url"]


def test_capability_without_parameters_is_skipped(tmp_path: Path) -> None:
    _write_capability(
        tmp_path,
        "delivery-review-v1",
        {"name": "delivery-review-v1", "description": "Verification-only capability."},
    )
    assert load_capability_tool_specs(root=tmp_path) == []


def test_missing_root_returns_empty(tmp_path: Path) -> None:
    assert load_capability_tool_specs(root=tmp_path / "does-not-exist") == []


def test_malformed_capability_json_is_skipped(tmp_path: Path) -> None:
    directory = tmp_path / "broken-v1"
    directory.mkdir(parents=True)
    (directory / "capability.json").write_text("{not json", encoding="utf-8")
    assert load_capability_tool_specs(root=tmp_path) == []


def test_multiple_capabilities_mixed(tmp_path: Path) -> None:
    _write_capability(
        tmp_path,
        "a-v1",
        {"name": "a-v1", "description": "a", "parameters": {"type": "object", "properties": {}}},
    )
    _write_capability(tmp_path, "b-v1", {"name": "b-v1", "description": "b (no params)"})
    specs = load_capability_tool_specs(root=tmp_path)
    assert [s.name for s in specs] == ["capability_a_v1"]


def test_repo_product_surface_capability_is_discoverable() -> None:
    """CD-4.4: product-surface-v1 now declares parameters so discovery is non-empty."""
    specs = load_capability_tool_specs()
    names = {s.name for s in specs}
    assert "capability_product_surface_v1" in names
