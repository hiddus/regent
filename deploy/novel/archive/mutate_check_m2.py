"""反证：M2 旅程在真实浏览器 + 真实 PG 上抓到的两个缺陷，必须被测试真守住。

命题与变异：
1. 「方向确认幂等」——把回放改回「一律 InvalidState」→ 幂等测试必须红；
   同时「换卡=真冲突」必须仍绿（否则测试过宽、把真冲突也放过了）。
2. 「没有章运行必须说实话」——把 get_active_run_progress 改回编造 QUEUED
   → 「absent not queued」测试必须红；同时「有运行照实回报」必须仍绿。

用法：python deploy/novel/mutate_check_m2.py
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TARGET = os.path.join(ROOT, "core", "src", "regent", "novel", "application", "works.py")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
TESTS = "tests/unit/novel/test_m2_restore.py"

# --- 变异 1：方向确认幂等 -----------------------------------------------------
CONFIRM_REAL = """        if onboarding is not None and onboarding.selected_card_id == card_id:
            return work, await get_critical_path(
                session, owner_id=owner_id, work_id=work_id
            )
        raise InvalidState("direction already confirmed", current=work.state)"""
CONFIRM_MUTANT = """        raise InvalidState("direction already confirmed", current=work.state)"""

# --- 变异 2：没有章运行不得编造进度 -------------------------------------------
ABSENT_REAL = """    run = await _latest_run(session, work=work)
    if run is None:
        return None
    return await _progress_for_run(session, run=run)"""
ABSENT_MUTANT = """    run = await _latest_run(session, work=work)
    if run is None:
        return RunProgressOut(
            work_id=str(work_id),
            chapter_no=int(work.latest_chapter_no),
            state=ChapterRunState.QUEUED,
        )
    return await _progress_for_run(session, run=run)"""


def run(test: str) -> int:
    proc = subprocess.run(
        [PY, "-m", "pytest", test, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode


def t(name: str) -> str:
    return f"{TESTS}::{name}"


def mutate(original: str, real: str, mutant: str, label: str,
           probe: str, guards: tuple[str, ...]) -> bool:
    if original.count(real) != 1:
        print(f"[FAIL] {label}: 找不到唯一锚点（count={original.count(real)}）——改动后请同步本脚本")
        return False
    try:
        with open(TARGET, "w", encoding="utf-8") as fh:
            fh.write(original.replace(real, mutant))
        rc_probe = run(t(probe))
        rc_guards = [run(t(g)) for g in guards]
    finally:
        with open(TARGET, "w", encoding="utf-8") as fh:
            fh.write(original)
    ok = rc_probe != 0 and all(rc == 0 for rc in rc_guards)
    print(f"  {label}: 变异后探针 rc={rc_probe}（期望非 0）, 护栏 {list(zip(guards, rc_guards))}（期望全 0）")
    return ok


def main() -> int:
    with open(TARGET, encoding="utf-8") as fh:
        original = fh.read()

    probes = [
        t("test_reconfirming_same_direction_is_idempotent"),
        t("test_confirming_a_different_card_after_lock_is_a_real_conflict"),
        t("test_no_run_is_reported_as_absent_not_as_queued"),
        t("test_active_run_progress_reports_the_real_run"),
    ]
    before = [run(p) for p in probes]
    if any(rc != 0 for rc in before):
        print("[FAIL] 变异前就有测试不过，反证无意义:", list(zip(probes, before)))
        return 2

    print("变异 1：方向确认幂等")
    ok1 = mutate(
        original, CONFIRM_REAL, CONFIRM_MUTANT, "confirm-idempotent",
        probe="test_reconfirming_same_direction_is_idempotent",
        guards=("test_confirming_a_different_card_after_lock_is_a_real_conflict",),
    )
    print("变异 2：没有章运行不得编造 QUEUED")
    ok2 = mutate(
        original, ABSENT_REAL, ABSENT_MUTANT, "absent-not-queued",
        probe="test_no_run_is_reported_as_absent_not_as_queued",
        guards=("test_active_run_progress_reports_the_real_run",),
    )

    after = [run(p) for p in probes]
    print("还原后 4 项探针 rc =", after, "（期望全 0）")
    ok = ok1 and ok2 and all(rc == 0 for rc in after)
    print("[PASS] 两个 M2 修复都被真守卫" if ok else "[FAIL] 反证未成立")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
