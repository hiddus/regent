#!/usr/bin/env python3
"""Tracked repo hygiene gate for Novel Engine / Regent Core.

Fails when:
- Root accumulates one-off debug scripts
- Ad-hoc DB mutation scripts appear outside Alembic
- deploy/novel/ piles up disposable _tmp* scripts
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ROOT_PY_ALLOWLIST = {
    "graduation_harness.py",
    "setup_cron.py",
    "sync_docs_to_server.py",
}

FORBIDDEN_ROOT_NAMES = {
    "find_paths.py",
    "persistence_test.py",
    "acceptance_test.py",
    "acceptance_playwright.py",
    "test_full_flow.py",
    "fix_and_test.py",
}

FORBIDDEN_ROOT_PREFIXES = (
    "check_",
    "fix_",
    "verify_",
    "final_",
    "quick_",
    "mini_",
    "fast_",
    "deep_",
    "detailed_",
    "comprehensive_",
    "debug_",
    "deploy_",
    "sftp_",
    "q",
)

DB_MUTATION_HINTS = re.compile(
    r"(alembic\.op\.|op\.execute\(|CREATE TABLE|ALTER TABLE|DROP TABLE|"
    r"UPDATE\s+\w+\s+SET|DELETE\s+FROM|INSERT\s+INTO)",
    re.I,
)

# Durable deploy helpers allowed beside one-off probes.
NOVEL_DEPLOY_ALLOW = {
    "_ssh.py",
    "README.md",
}


def check_root_scripts() -> list[str]:
    errors: list[str] = []
    for path in sorted(ROOT.glob("*.py")):
        name = path.name
        if name in ROOT_PY_ALLOWLIST:
            continue
        if name in FORBIDDEN_ROOT_NAMES or name.startswith(FORBIDDEN_ROOT_PREFIXES):
            errors.append(
                f"root one-off script not allowed: {name} "
                f"(move to ops/archive/oneoff/)"
            )
        else:
            errors.append(
                f"unexpected root python file: {name} "
                f"(add to allowlist only if it is a durable harness)"
            )
    return errors


def check_ad_hoc_db_scripts() -> list[str]:
    errors: list[str] = []
    migrations = ROOT / "core" / "migrations" / "versions"
    for path in ROOT.glob("*.py"):
        if path.name in ROOT_PY_ALLOWLIST:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if DB_MUTATION_HINTS.search(text) and "alembic" not in text.lower()[:400]:
            errors.append(
                f"ad-hoc DB mutation script at repo root: {path.name} "
                f"(use Alembic migration instead; see docs/migration-policy.md)"
            )
    if not migrations.is_dir():
        errors.append("missing core/migrations/versions directory")
    return errors


def check_novel_deploy_tmp() -> list[str]:
    """Disposable probes must not accumulate in the active deploy folder."""
    errors: list[str] = []
    novel = ROOT / "deploy" / "novel"
    if not novel.is_dir():
        return errors
    tmp_files = sorted(
        p for p in novel.glob("_tmp*") if p.is_file() and p.name not in NOVEL_DEPLOY_ALLOW
    )
    # Soft ceiling: a few in-flight probes OK; piles mean process debt.
    if len(tmp_files) > 5:
        sample = ", ".join(p.name for p in tmp_files[:8])
        errors.append(
            f"deploy/novel has {len(tmp_files)} _tmp* files (max 5); "
            f"archive evidence then delete probes. sample: {sample}"
        )
    return errors


def main() -> int:
    errors = (
        check_root_scripts()
        + check_ad_hoc_db_scripts()
        + check_novel_deploy_tmp()
    )
    if errors:
        print("REPO HYGIENE FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1
    print("repo hygiene OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
