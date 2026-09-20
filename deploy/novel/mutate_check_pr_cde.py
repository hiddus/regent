"""变异反证：soft 关键词误放 / resume 伪 RUNNING / 连续创作默认开启。

用法：`$env:PYTHONUTF8='1'; $env:PYTHONPATH='core/src'; python deploy/novel/mutate_check_pr_cde.py`
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST = ROOT / "tests/unit/novel/test_pr_cde_behavior.py"

MUTATIONS = [
    (
        "core/src/regent/novel/domain/prose_front_gates.py",
        "review_issues_may_coerce",
        """
def review_issues_may_coerce(issues):
    # mutant: allow keyword soft
    return True
""",
        ["test_review_coerce_structured_only"],
    ),
    (
        "core/src/regent/novel/application/works_continuation.py",
        "get_continuation_policy",
        """
def get_continuation_policy(work):
    from dataclasses import replace
    return ContinuationPolicy(enabled=True, target_chapter_no=None, max_chapters=None)
""",
        ["test_continuation_policy_default_off", "test_ensure_next_run_requires_policy"],
    ),
]


def _inject(path: Path, func_name: str, new_src: str) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            start, end = node.lineno - 1, node.end_lineno
            return "".join(lines[:start]) + new_src + "\n" + "".join(lines[end:])
    raise SystemExit(f"anchor {func_name} not found in {path}")


def main() -> int:
    failed = []
    for rel, func, mutant, need_red in MUTATIONS:
        path = ROOT / rel
        backup = path.read_text(encoding="utf-8")
        path.write_text(_inject(path, func, mutant), encoding="utf-8")
        print(f"[MUTANT] {rel}::{func}")
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    str(TEST),
                    "-p",
                    "no:cacheprovider",
                    "-q",
                    "--tb=line",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            print(out[-2000:])
            red = proc.returncode != 0 and any(n in out for n in need_red)
            if not red:
                failed.append(func)
                print(f"[FAIL] {func} 变异未被咬住")
            else:
                print(f"[OK] {func} 变异被咬住")
        finally:
            path.write_text(backup, encoding="utf-8")
            print(f"[RESTORE] {rel}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
