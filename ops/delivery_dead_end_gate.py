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

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "core" / "src" / "regent" / "application"
TARGETS = [
    APP / "execution_orchestrator.py",
    APP / "delivery_gap_recovery.py",
    APP / "delivery_state.py",
    APP / "execution_service.py",
    APP / "release_service.py",
    APP / "delivery_review_service.py",
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
    "decide_delivery_verdict",
)

_TERMINAL_EXHAUST_TRUE = re.compile(r"terminal_exhaust\s*=\s*True")
_INCOMPLETE_TERMINAL = re.compile(
    r"(raise\s+\w*(Error|Exception)\([^)]*incomplete|incomplete[^\n]*(FAILED|STOP|EXHAUST))",
    re.IGNORECASE,
)


def split_methods(source: str) -> list[tuple[str, str]]:
    """Return (qualified_name, body_text) for every function via AST (includes nested)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SystemExit(f"AC1 gate: cannot parse: {exc}") from exc
    lines = source.splitlines()
    methods: list[tuple[str, str]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def _visit_fn(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            name = ".".join([*self.stack, node.name])
            start = node.lineno - 1
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            body = "\n".join(lines[start:end])
            methods.append((name, body))
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            self._visit_fn(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            self._visit_fn(node)

    Visitor().visit(tree)
    return methods


def check_file(path: Path) -> list[tuple[Path, str]]:
    if not path.exists():
        return []
    source = path.read_text(encoding="utf-8")
    violations: list[tuple[Path, str]] = []
    for name, text in split_methods(source):
        is_terminal = bool(_TERMINAL_EXHAUST_TRUE.search(text)) or bool(
            _INCOMPLETE_TERMINAL.search(text)
        )
        if not is_terminal:
            continue
        if any(tok in text for tok in HANDOFF_TOKENS):
            continue
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
