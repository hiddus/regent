"""反证：缺陷 9 的命令收束必须被测试真守住。

用法：python deploy/novel/mutate_check_d9.py
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
T = "tests/unit/novel/test_d9_coerce.py"

M1_REAL = """        if MAX_TURNS - int(take.get("turn", 0)) - 1 <= 0:"""
M1_MUTANT = """        if False:"""

# ACCEPT 一旦被"顺手修好"成 REWRITE，就是替导演绕过独立核验——必须有守卫
M2_REAL = """            notes.append("节拍已用尽，CONTINUE 不可执行，按 RENDER 收束本场")
    return notes"""
M2_MUTANT = """            notes.append("节拍已用尽，CONTINUE 不可执行，按 RENDER 收束本场")
    if phase == "WATCH_PROSE" and result.action == "ACCEPT":
        result.action = "REWRITE"
        notes.append("顺手改写")
    return notes"""

# 收束被放大成"一律改成 RENDER"——护栏是「还有节拍时不许替导演收束」
M3_REAL = """        if MAX_TURNS - int(take.get("turn", 0)) - 1 <= 0:"""
M3_MUTANT = """        if True:"""

PROBE_TESTS = (
    "test_continue_is_coerced_to_render_when_beats_are_exhausted",
    "test_continue_is_left_alone_while_beats_remain",
    "test_accept_is_never_coerced_even_on_hard_failure",
    "test_accept_is_left_alone_when_validation_passed",
    "test_other_phases_are_untouched",
)

MUTATIONS = (
    ("1 节拍用尽不再收束", M1_REAL, M1_MUTANT,
     "test_continue_is_coerced_to_render_when_beats_are_exhausted",
     ("test_accept_is_never_coerced_even_on_hard_failure",
      "test_other_phases_are_untouched")),
    ("2 ACCEPT 被顺手收束成 REWRITE", M2_REAL, M2_MUTANT,
     "test_accept_is_never_coerced_even_on_hard_failure",
     ("test_continue_is_coerced_to_render_when_beats_are_exhausted",
      "test_continue_is_left_alone_while_beats_remain")),
    ("3 收束被放大成无条件改写", M3_REAL, M3_MUTANT,
     "test_continue_is_left_alone_while_beats_remain",
     ("test_accept_is_never_coerced_even_on_hard_failure",
      "test_other_phases_are_untouched")),
)


def run(test_id: str) -> int:
    proc = subprocess.run(
        [PY, "-m", "pytest", test_id, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode


def mutate(real: str, mutant: str, label: str, probe: str, guards: tuple[str, ...]) -> bool:
    with open(DIRECTION, encoding="utf-8") as fh:
        original = fh.read()
    if original.count(real) != 1:
        print(f"[FAIL] {label}: 找不到唯一锚点（count={original.count(real)}）")
        return False
    try:
        with open(DIRECTION, "w", encoding="utf-8") as fh:
            fh.write(original.replace(real, mutant))
        rc_probe = run(f"{T}::{probe}")
        rc_guards = [run(f"{T}::{g}") for g in guards]
    finally:
        with open(DIRECTION, "w", encoding="utf-8") as fh:
            fh.write(original)
    ok = rc_probe != 0 and all(rc == 0 for rc in rc_guards)
    print(f"  {label}: 探针 rc={rc_probe}（期望非 0） 护栏 {rc_guards}（期望全 0）"
          f" → {'OK' if ok else 'BAD'}")
    return ok


def main() -> int:
    probes = [f"{T}::{p}" for p in PROBE_TESTS]
    before = [run(p) for p in probes]
    if any(rc != 0 for rc in before):
        print("[FAIL] 变异前就有测试不过，反证无意义:",
              [p for p, rc in zip(probes, before) if rc != 0])
        return 2
    results = [mutate(r, m, label, probe, guards)
               for label, r, m, probe, guards in MUTATIONS]
    after = [run(p) for p in probes]
    print("还原后探针 rc =", set(after), "（期望 {0}）")
    ok = all(results) and all(rc == 0 for rc in after)
    print("[PASS] 缺陷 9 的命令收束被真守卫" if ok else "[FAIL] 反证未成立")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
