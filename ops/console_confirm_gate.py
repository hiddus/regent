#!/usr/bin/env python3
"""CON-5 gate: forbid bare blocking confirm/input patterns in product code.

Scans core/ and apps/ (excluding node_modules / dist / tests) for CLI-style
dead-wait primitives. Web HumanTask must carry timeout + default_on_timeout
via ConfirmationRequest (see docs/console-dialog-prd-2026-07-31.md).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = [ROOT / "core" / "src" / "regent", ROOT / "apps" / "regent-console" / "src"]

# Statement-like calls only (avoid prose "stored input (...)" in docstrings).
FORBIDDEN = [
    (re.compile(r"(?:^|[=\(,;])\s*input\s*\("), "bare input()"),
    (re.compile(r"Confirm\.ask\s*\("), "rich Confirm.ask"),
    (re.compile(r"click\.confirm\s*\("), "click.confirm"),
    (re.compile(r"typer\.confirm\s*\("), "typer.confirm"),
    (re.compile(r"getpass\.getpass\s*\("), "getpass"),
]

SKIP_DIR_NAMES = {
    "node_modules",
    "dist",
    "__pycache__",
    ".git",
    "coverage",
}


def main() -> int:
    hits: list[str] = []
    for root in SCAN_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            if path.suffix not in {".py", ".ts", ".tsx", ".js", ".jsx"}:
                continue
            if path.name.startswith("test_"):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                if stripped.startswith("*") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                for pattern, label in FORBIDDEN:
                    if pattern.search(line):
                        hits.append(
                            f"{path.relative_to(ROOT)}:{i}: {label}: {stripped}"
                        )
    if hits:
        print("CON-5 FAIL: bare confirm/input patterns found:")
        for h in hits:
            print(" ", h)
        return 1
    print("CON-5 OK: no bare input/Confirm.ask/click.confirm in product code")
    print("Rollback: REGENT_DECISION_PREFERENCE=balanced; clear ALLOW/DENY action lists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
