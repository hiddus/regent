#!/usr/bin/env python3
"""AC1 gate: no delivery/execution terminal state without an explicit exit.

Static check (CON-5 sibling). For every method that references ``terminal_exhaust``
or raises an ``incomplete`` terminal, assert it also routes to a human/escalation
handoff (WAIT_FOR_HUMAN / HUMAN_TASK_REQUIRED / _apply_delivery_verdict /
HANDED_OFF / ESCALATED). This makes the "no dead-end" rule grep-enforceable
instead of relying on review.

Exit code 0 = clean; 1 = violations found.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    ROOT / "core" / "src" / "regent" / "application" / "execution_orchestrator.py",
    ROOT / "core" / "src" / "regent" / "application" / "delivery_gap_recovery.py",
]

HANDOFF_TOKENS = (
    "WAIT_FOR_HUMAN",
    "WAITING_HUMAN",
    "HUMAN_TASK_REQUIRED",
    "_apply_delivery_verdict",
    "HANDED_OFF",
    "ESCALATED",
    "DeliveryState.DELIVERED_FOR_REVIEW",
    "DeliveryState.ESCALATED",
)

# Only True exhaustions are terminal; ``terminal_exhaust=False`` is a non-event.
_TERMINAL_EXHAUST_TRUE = re.compile(r"terminal_exhaust\s*=\s*True")
# Positional ``..., True)`` on DeliveryGapRecoveryResult is fragile; also catch
# bare ``incomplete`` terminal phrases used in raise/error paths.
_INCOMPLETE_TERMINAL = re.compile(
    r"(raise\s+\w*(Error|Exception)\([^)]*incomplete|incomplete[^\n]*(FAILED|STOP|EXHAUST))",
    re.IGNORECASE,
)


def split_methods(lines: list[str]) -> list[list[str]]:
    """Yield method bodies by tracking ``def``/``async def`` indent."""
    methods: list[list[str]] = []
    current: list[str] | None = None
    base_indent: int | None = None
    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if stripped.startswith(("def ", "async def ")):
            if current is not None:
                methods.append(current)
            current = [line]
            base_indent = indent
        elif current is not None:
            if stripped == "":
                current.append(line)
                continue
            if indent >= base_indent:
                current.append(line)
            else:
                methods.append(current)
                current = None
    if current is not None:
        methods.append(current)
    return methods


def check_file(path: Path) -> list[tuple[Path, str]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    violations: list[tuple[Path, str]] = []
    for method in split_methods(lines):
        text = "\n".join(method)
        is_terminal = bool(_TERMINAL_EXHAUST_TRUE.search(text)) or bool(
            _INCOMPLETE_TERMINAL.search(text)
        )
        if not is_terminal:
            continue
        if any(tok in text for tok in HANDOFF_TOKENS):
            continue
        name = next(
            (ln.strip() for ln in method if ln.strip().startswith(("def ", "async def "))),
            "?",
        )
        violations.append((path, name))
    return violations


def main() -> int:
    all_violations: list[tuple[Path, str]] = []
    for target in TARGETS:
        all_violations.extend(check_file(target))
    if all_violations:
        print("AC1 VIOLATION: terminal state without explicit exit:")
        for path, name in all_violations:
            print(f"  - {path}: {name}")
        return 1
    print("AC1 OK: all delivery/execution terminal states have an explicit exit (CON-5 sibling).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
