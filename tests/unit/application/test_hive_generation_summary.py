"""Unit test for Hive QA generation summary helper."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

from regent.application.execution_orchestrator import ExecutionOrchestrator


def test_hive_generation_summary_lists_paths(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "index.html").write_text("<h1>x</h1>", encoding="utf-8")
    (tmp_path / "agents_store.py").write_text("class CountryLawAgent: ...\n", encoding="utf-8")
    snap = SimpleNamespace(
        file_count=3,
        workspace_locator=str(tmp_path),
        manifest_uri="",
    )
    raw = ExecutionOrchestrator._hive_generation_summary(
        run_id=uuid.uuid4(), snapshot=snap
    )
    data = json.loads(raw)
    assert data["file_count"] == 3
    assert "app.py" in data["paths"]
    assert data["markers"]["has_app_py"] is True
    assert data["markers"]["has_templates"] is True
    assert data["markers"]["has_agents_or_models"] is True
