"""Unit tests for workspace_browser resolve order (no global agentic fallback)."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from regent.application.workspace_browser import resolve_project_workspace
from regent.infrastructure.models import GoalModel


@pytest.mark.asyncio
async def test_resolve_prefers_recoverable_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "regent.application.workspace_browser.get_settings",
        lambda: MagicMock(workspace_root=str(tmp_path)),
    )
    snap = tmp_path / "recoverable_workspace_snapshots" / "snap-abc"
    snap.mkdir(parents=True)
    (snap / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    # Poison: newer unrelated agentic sandbox must NOT win.
    poison = tmp_path / "agentic" / "other-goal"
    poison.mkdir(parents=True)
    (poison / "evil.py").write_text("print('no')\n", encoding="utf-8")

    goal = GoalModel(
        id=uuid.uuid4(),
        original_input="x",
        status="ACTIVE",
        version=1,
        created_by="test",
        correlation_id=uuid.uuid4(),
        app_project_id=uuid.uuid4(),
        metadata_json={
            "execution_stage": "DELIVERY_SOFT_PAUSE",
            "last_recoverable_workspace": {"snapshot_id": "snap-abc"},
            "diagnostic_delivery": {
                "resume": {"base_snapshot_id": "snap-abc"},
                "promote_allowed": False,
            },
        },
    )
    session = MagicMock()
    session.scalar = AsyncMock(return_value=goal)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session)

    root = await resolve_project_workspace(factory, goal.app_project_id)
    assert root is not None
    assert root.name == "snap-abc"
    assert (root / "index.html").is_file()
