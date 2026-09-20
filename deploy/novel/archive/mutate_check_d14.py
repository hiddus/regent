"""反证：缺陷 14（RETAKE brief 未改变校验必须在自修通道里）必须被测试真守住。

用法：python deploy/novel/mutate_check_d14.py
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
T = "tests/unit/novel/test_d14_retake.py"

# 1 把 brief 校验从 _validate_action 里拿掉 → 走回 _retake 老路（直接判死）
M1_REAL = """    if (
        commands[result.action] is CommandKind.RETAKE_SCENE
        and result.revised_brief is not None
        and take.get("brief") is not None
    ):"""
M1_MUTANT = """    if False:"""

# 2 只改一个字段不视为"改变"——这条修改等于把 _retake 的语义"全部字段相同才算未改"退化成"任一字段相同就算未改"，模型永远过不去
M2_REAL = """        if len(same) == len(proposed):"""
M2_MUTANT = """        if len(same) > 0:"""

MUTATIONS = (
    ("1 brief 校验挪出 _validate_action", M1_REAL, M1_MUTANT,
     T + "::test_retake_with_identical_brief_is_rejected_with_actionable_feedback",
     (T + "::test_retake_with_one_changed_field_is_accepted",)),
    ("2 改成任一字段相同就拒", M2_REAL, M2_MUTANT,
     T + "::test_retake_with_one_changed_field_is_accepted",
     (T + "::test_retake_with_identical_brief_is_rejected_with_actionable_feedback",)),
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
                        failures.append(f"{label}: 复原失败")
                        return 1

            if probe_rc == 0:
                failures.append(f"{label}: 探针仍绿")
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
