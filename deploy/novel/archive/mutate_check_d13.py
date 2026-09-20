"""反证：缺陷 13（反馈累积 + 可执行性枚举）必须被测试真守住。

用法：python deploy/novel/mutate_check_d13.py
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIRECTION = os.path.join(
    ROOT, "core", "src", "regent", "novel", "application", "direction.py"
)
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
T = "tests/unit/novel/test_d13_accumulate.py"
D11 = "tests/unit/novel/test_d11_command_repair.py"

# 引文失败：反馈改成覆盖（回到缺陷 13 原状）
M1_REAL = """            repair = repair + [f"上一版判断不能采用：{exc}", EVIDENCE_REPAIR_RULE]
            if not any(item == stage_text[:1500] for item in repair):"""
M1_MUTANT = """            repair = [f"上一版判断不能采用：{exc}", EVIDENCE_REPAIR_RULE]
            if True:"""

# 命令失败：反馈改成覆盖
M2_REAL = """                repair = repair + [f"上一版动作不能执行：{exc}"]"""
M2_MUTANT = """                repair = [f"上一版动作不能执行：{exc}"]"""

# 不再枚举可执行动作——模型又得猜
M3_REAL = """                if command_report is not None:
                    repair.append("""
M3_MUTANT = """                if False:
                    repair.append("""

# 原文每轮都塞一遍
M4_REAL = """            if not any(item == stage_text[:1500] for item in repair):
                repair.append(stage_text[:1500])"""
M4_MUTANT = """            repair.append(stage_text[:1500])"""

MUTATIONS = (
    ("1 引文反馈被覆盖", M1_REAL, M1_MUTANT,
     T + "::test_repeated_evidence_failures_accumulate",
     (T + "::test_stage_text_is_not_repeated_on_every_evidence_failure",)),
    ("2 命令反馈被覆盖", M2_REAL, M2_MUTANT,
     T + "::test_second_failure_keeps_the_first_constraint",
     (D11 + "::test_command_feedback_names_the_rejection_reason",)),
    ("3 不再枚举可执行动作", M3_REAL, M3_MUTANT,
     T + "::test_rejection_feedback_lists_which_actions_are_still_executable",
     (D11 + "::test_command_feedback_names_the_rejection_reason",)),
    ("4 原文每轮重复塞入", M4_REAL, M4_MUTANT,
     T + "::test_stage_text_is_not_repeated_on_every_evidence_failure",
     (T + "::test_second_failure_keeps_the_first_constraint",)),
)


def run(test_id: str) -> int:
    proc = subprocess.run(
        [PY, "-m", "pytest", test_id, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode


def main() -> int:
    with open(DIRECTION, encoding="utf-8") as fh:
        original = fh.read()

    failures: list[str] = []
    try:
        for label, real, mutant, probe, guards in MUTATIONS:
            if original.count(real) != 1:
                failures.append(f"{label}: 锚点命中 {original.count(real)} 次，应为 1")
                continue
            mutated = original.replace(real, mutant, 1)
            if mutated == original:
                failures.append(f"{label}: 变异未生效")
                continue
            try:
                with open(DIRECTION, "w", encoding="utf-8") as fh:
                    fh.write(mutated)
                probe_rc = run(probe)
                guard_rcs = {g: run(g) for g in guards}
            finally:
                with open(DIRECTION, "w", encoding="utf-8") as fh:
                    fh.write(original)
                with open(DIRECTION, encoding="utf-8") as fh:
                    if fh.read() != original:
                        failures.append(f"{label}: 复原失败，源码被留在变异态！")
                        return 1

            if probe_rc == 0:
                failures.append(f"{label}: 探针仍绿（变异没被抓住）")
            for g, rc in guard_rcs.items():
                if rc != 0:
                    failures.append(f"{label}: 护栏变红 -> {g}")
            status = "OK" if probe_rc != 0 and all(v == 0 for v in guard_rcs.values()) else "FAIL"
            print(f"  [{status}] {label}")
    finally:
        with open(DIRECTION, "w", encoding="utf-8") as fh:
            fh.write(original)

    if failures:
        print("\n".join(failures))
        return 1
    print("全部变异均被抓住。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
