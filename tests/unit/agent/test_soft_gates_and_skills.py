"""Soft product gates: deliver non-empty artifacts without test/smoke death-loops."""

from __future__ import annotations

from pathlib import Path

import pytest

from regent.agent.skills import (
    load_skill_catalog,
    load_skill_manifest,
    select_skills_for_goal,
)
from regent.agent.tools import WorkspaceToolkit
from regent.agent.verification import VerificationAgent
from regent.application.delivery_success_policy import (
    is_blocking_delivery_gap_code,
    partition_delivery_gap_codes,
)


@pytest.fixture()
def soft_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGENT_DELIVERY_PRODUCT_GATES_MODE", "soft")
    from regent.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_partition_blocking_vs_soft() -> None:
    blocking, soft = partition_delivery_gap_codes(
        ["TEST_FAILED", "forbid-demo-shell", "min-visible-text", "SMOKE_FAILED"]
    )
    assert "forbid-demo-shell" in blocking
    assert "TEST_FAILED" in soft
    assert "SMOKE_FAILED" in soft
    assert is_blocking_delivery_gap_code("forbid-unrendered-templates")
    assert not is_blocking_delivery_gap_code("TEST_FAILED")


@pytest.mark.asyncio
async def test_verification_soft_skips_tests_and_demotes_gaps(
    soft_settings: None, tmp_path: Path
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "index.html").write_text(
        "<!doctype html><html><head><title>Demo</title>"
        '<link rel="stylesheet" href="/static/a.css"></head>'
        '<body><h1>Hello product</h1>'
        '<button data-regent-event="click">Go</button></body></html>',
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text("flask\n", encoding="utf-8")
    (root / "README.md").write_text("# Demo\nA minimal demo project.\n", encoding="utf-8")
    (root / "test_demo.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    toolkit = WorkspaceToolkit(root)
    agent = VerificationAgent(toolkit)
    verdict = await agent.verify(run_smoke=True)
    stages = (verdict.smoke or {}).get("stages") or {}
    assert stages.get("product_gates_mode") == "soft"
    assert (stages.get("tests") or {}).get("soft_skipped") is True
    # Soft mode should not hard-fail on non-blocking product gaps alone.
    assert all(is_blocking_delivery_gap_code(g.code) for g in verdict.gaps) or verdict.passed


def test_skill_catalog_progressive_disclosure() -> None:
    catalog = load_skill_catalog()
    assert len(catalog) >= 5
    assert all(e.skill_id and e.description for e in catalog)
    # Catalog rows must not embed guidance bodies.
    for e in catalog:
        assert not hasattr(e, "guidance") or not getattr(e, "guidance", None)


def test_skill_md_compatible(tmp_path: Path) -> None:
    pack = tmp_path / "demo-skill"
    pack.mkdir()
    (pack / "SKILL.md").write_text(
        "---\n"
        "name: demo-skill\n"
        "version: 0.1.0\n"
        "title: Demo\n"
        "description: demo only\n"
        "applies_when: [demo, 演示]\n"
        "gap_codes: [TEST_FAILED]\n"
        "---\n\n"
        "# Demo guidance\n\nDo the thing.\n",
        encoding="utf-8",
    )
    # Root with only this pack + no index → fall back scan.
    m = load_skill_manifest("demo-skill", root=tmp_path)
    assert m.skill_id == "demo-skill"
    assert "Do the thing" in m.guidance
    assert "demo" in m.applies_when


def test_select_skills_uses_catalog() -> None:
    skills = select_skills_for_goal("做一个待办网站")
    ids = {s.skill_id for s in skills}
    assert "web-app-scaffold" in ids or "runtime-contract" in ids
    assert all(s.guidance for s in skills)
