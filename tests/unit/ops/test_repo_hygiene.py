"""P2 hygiene / migration policy assertions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load_hygiene():
    path = ROOT / "ops" / "check_repo_hygiene.py"
    spec = importlib.util.spec_from_file_location("check_repo_hygiene", path)
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


def test_delivery_regression_goal_count() -> None:
    path = ROOT / "ops" / "archive" / "oneoff" / "graduation_harness.py"
    spec = importlib.util.spec_from_file_location("graduation_harness", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert len(mod.DELIVERY_REGRESSION_GOALS) == 10
