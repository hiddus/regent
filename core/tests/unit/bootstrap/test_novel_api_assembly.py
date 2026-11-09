"""Novel / combined API assembly smoke tests (no DB required)."""

from __future__ import annotations

import pytest

from regent.config import get_settings
from regent.novel.application.directing_budget import (
    effective_call_cap,
    remaining_calls,
)


def test_budget_caps_include_grants() -> None:
    production = {"call_count": 10, "budget_grant_calls": 5, "committed_minor": 100}
    assert effective_call_cap(production, base_calls=120) == 125
    assert remaining_calls(production, base_calls=120) == 115


def test_novel_mode_openapi_excludes_goals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGENT_SERVICE_MODE", "novel")
    get_settings.cache_clear()
    from regent.api.main import create_app

    get_settings.cache_clear()
    app = create_app()
    paths = list((app.openapi().get("paths") or {}))
    assert any(p.startswith("/v1/novel") for p in paths)
    assert not any(p.startswith("/v1/goals") for p in paths)
    assert any("/v1/novel/works" in p for p in paths)


def test_combined_mode_keeps_goals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGENT_SERVICE_MODE", "combined")
    get_settings.cache_clear()
    from regent.api.main import create_app

    get_settings.cache_clear()
    app = create_app()
    paths = list((app.openapi().get("paths") or {}))
    assert any(p.startswith("/v1/goals") for p in paths)
    assert any(p.startswith("/v1/novel") for p in paths)
