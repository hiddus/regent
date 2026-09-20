"""反证：BQ-1/BQ-2 预算暂停与无效修订止损必须被测试咬住。

用法：python deploy/novel/mutate_check_bq.py
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKS = os.path.join(ROOT, "core", "src", "regent", "novel", "application", "works.py")
DIRECTION = os.path.join(
    ROOT, "core", "src", "regent", "novel", "application", "direction.py"
)
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
T = "tests/unit/novel/test_bq_budget_quality.py"

M1_REAL = """        if isinstance(exc, BudgetExhausted):
            pending.state = StepState.PENDING.value
            pending.attempt = max(0, int(pending.attempt or 0) - 1)
            pending.error_code = \"\"
            pause_state = (
                StoryWorkState.PAUSED_QUOTA.value
                if exc.kind == \"calls\"
                else StoryWorkState.PAUSED_COST.value
            )"""
M1_MUTANT = """        if False and isinstance(exc, BudgetExhausted):
            pending.state = StepState.PENDING.value
            pending.attempt = max(0, int(pending.attempt or 0) - 1)
            pending.error_code = \"\"
            pause_state = (
                StoryWorkState.PAUSED_QUOTA.value
                if exc.kind == \"calls\"
                else StoryWorkState.PAUSED_COST.value
            )"""

M2_REAL = """    if open_hard and all(str(e.get(\"outcome\") or \"\") == \"repeat\" for e in open_hard):
        ids = \"；\".join(str(e.get(\"id\") or \"\") for e in open_hard[:8])
        return (
            \"同类硬问题无进展，禁止再 REWRITE；请 RETAKE、请求裁决或重新规划。\"
            f\"重复项：{ids}\"
        )
    return None"""
M2_MUTANT = """    if False and open_hard and all(str(e.get(\"outcome\") or \"\") == \"repeat\" for e in open_hard):
        ids = \"；\".join(str(e.get(\"id\") or \"\") for e in open_hard[:8])
        return (
            \"同类硬问题无进展，禁止再 REWRITE；请 RETAKE、请求裁决或重新规划。\"
            f\"重复项：{ids}\"
        )
    return None"""

MUTATIONS = [
    (
        "budget_still_terminal",
        WORKS,
        M1_REAL,
        M1_MUTANT,
        [f"{T}::test_advance_step_budget_pause_keeps_run_running"],
        [f"{T}::test_non_budget_production_stopped_still_terminal"],
    ),
    (
        "rewrite_allowed_without_progress",
        DIRECTION,
        M2_REAL,
        M2_MUTANT,
        [f"{T}::test_issue_ledger_marks_repeat_and_blocks_rewrite"],
        [f"{T}::test_issue_ledger_allows_rewrite_when_prose_hash_changes"],
    ),
]


def run_pytest(targets: list[str]) -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(ROOT, "core", "src")
    return subprocess.call([PY, "-m", "pytest", "-q", *targets], cwd=ROOT, env=env)


def main() -> int:
    failed = 0
    for name, path, real, mutant, probe, guard in MUTATIONS:
        original = open(path, encoding="utf-8").read()
        if real not in original:
            print(f"FAIL {name}: real anchor missing")
            failed += 1
            continue
        open(path, "w", encoding="utf-8").write(original.replace(real, mutant, 1))
        try:
            probe_rc = run_pytest(probe)
            guard_rc = run_pytest(guard)
            if probe_rc == 0:
                print(f"FAIL {name}: probe stayed green under mutation")
                failed += 1
            elif guard_rc != 0:
                print(f"FAIL {name}: orthogonal guard went red")
                failed += 1
            else:
                print(f"PASS {name}: probe red, guard green")
        finally:
            open(path, "w", encoding="utf-8").write(original)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
