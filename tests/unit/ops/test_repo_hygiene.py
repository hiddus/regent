"""Novel Engine / Regent Core repo hygiene assertions."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load_hygiene():
    path = ROOT / "ops" / "repo_hygiene.py"
    assert path.is_file(), f"tracked hygiene gate missing: {path}"
    spec = importlib.util.spec_from_file_location("repo_hygiene", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_root_has_no_oneoff_debug_scripts() -> None:
    mod = _load_hygiene()
    errors = mod.check_root_scripts()
    assert errors == [], errors


def test_migration_policy_doc_exists() -> None:
    assert (ROOT / "docs" / "migration-policy.md").is_file()


def test_agent_transcripts_migration_exists() -> None:
    versions = ROOT / "core" / "migrations" / "versions"
    files = list(versions.glob("*agent_transcripts*.py"))
    assert files, "expected agent_transcripts alembic migration"


def test_hygiene_main_ok() -> None:
    mod = _load_hygiene()
    assert mod.main() == 0


def test_novel_deploy_tmp_ceiling() -> None:
    mod = _load_hygiene()
    errors = mod.check_novel_deploy_tmp()
    assert errors == [], errors
