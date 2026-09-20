"""反证：R21 补丁/核验/预算硬门必须被测试真守住。

用法：python deploy/novel/mutate_check_r21.py
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATCH = os.path.join(ROOT, "core", "src", "regent", "novel", "domain", "prose_patch.py")
DIRECTION = os.path.join(ROOT, "core", "src", "regent", "novel", "application", "direction.py")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
T = "tests/unit/novel/test_r21_protocol.py"

# 1. 去掉 hash 校验 → 过期补丁仍合并
M1_REAL = """    if patch_hash != base_hash:
        raise PatchError(
            f"补丁 base_content_hash 过期：期望 {base_hash[:12]}…，收到 {patch_hash[:12]}…"
        )"""
M1_MUTANT = """    if False:
        raise PatchError(
            f"补丁 base_content_hash 过期：期望 {base_hash[:12]}…，收到 {patch_hash[:12]}…"
        )"""

# 2. 合并时丢掉未修改段落（整场只剩补丁文本）
M2_REAL = """    if _has_usable_offsets(ordered, base_text):
        return _merge_by_offsets(base_text, ordered, spans)
    return _merge_by_join(ordered, spans)"""
M2_MUTANT = """    return "\\n\\n".join(text for _a, _b, text in spans)"""

# 2b. 允许非连续 replacement（恢复误删间隙）
M2B_REAL = '''        if not _indices_contiguous(indices):
            raise PatchError(
                f"replacement[{r_index}] 段落 id 不连续；"
                "非连续修改请拆成多个 replacement"
            )'''
M2B_MUTANT = """        if False:
            raise PatchError("noop")"""

# 3. 动作报告退化成二元「可执行/不可执行」
M3_REAL = """            if action == "RETAKE" and _retake_would_pass_with_brief(
                production, run, take, phase, result, commands, actors
            ):
                lines.append(
                    f"- {action}：补参后可执行（须提供与现行 brief 不同的 revised_brief；{exc}）"
                )
            else:
                lines.append(f"- {action}：禁止（{exc}）")
        else:
            lines.append(f"- {action}：直接可执行")"""
M3_MUTANT = """            lines.append(f"- {action}：不可执行（{exc}）")
        else:
            lines.append(f"- {action}：可执行")"""


# 4. SUPERSEDED 仍计入重演预算
M4_REAL = (
    '        if take.get("scene_index") == scene_index and take.get("status") != "SUPERSEDED"'
)
M4_MUTANT = '        if take.get("scene_index") == scene_index'

# 5. 空核验报告放行（去掉覆盖强制）
M5_REAL = '''        if enforce_coverage and expected:
            if not result.state_changes:
                defects.append(
                    "核验报告未覆盖状态要求：requirements 与 state_changes 均为空"
                )'''
M5_MUTANT = """        if False and enforce_coverage and expected:
            if not result.state_changes:
                defects.append(
                    "核验报告未覆盖状态要求：requirements 与 state_changes 均为空"
                )"""


MUTATIONS = (
    ("1 去掉 hash 校验", PATCH, M1_REAL, M1_MUTANT,
     T + "::test_stale_hash_and_unknown_id_rejected_without_merge",
     (T + "::test_apply_patch_preserves_prefix_from_run21_sample",)),
    ("2 合并丢掉未改段落", PATCH, M2_REAL, M2_MUTANT,
     T + "::test_apply_patch_preserves_prefix_from_run21_sample",
     (T + "::test_stale_hash_and_unknown_id_rejected_without_merge",)),
    ("2b 允许非连续补丁", PATCH, M2B_REAL, M2B_MUTANT,
     T + "::test_noncontiguous_replacement_rejected_and_gap_preserved_via_split",
     (T + "::test_apply_patch_preserves_prefix_from_run21_sample",)),
    ("3 动作报告退化二元", DIRECTION, M3_REAL, M3_MUTANT,
     T + "::test_legal_action_report_distinguishes_param_vs_forbidden",
     (T + "::test_validation_report_rejects_fabricated_quotes",)),
    ("4 SUPERSEDED 仍占预算", DIRECTION, M4_REAL, M4_MUTANT,
     T + "::test_superseded_takes_do_not_consume_retake_budget",
     (T + "::test_catastrophic_loss_rejects_run21_half_scene",)),
    ("5 空报告放行", DIRECTION, M5_REAL, M5_MUTANT,
     T + "::test_empty_validation_report_is_defect_when_requirements_need_evidence",
     (T + "::test_validation_report_rejects_fabricated_quotes",)),
)


def run(test_id: str) -> int:
    proc = subprocess.run(
        [PY, "-m", "pytest", test_id, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode


def main() -> int:
    failures: list[str] = []
    originals: dict[str, str] = {}
    for path in {m[1] for m in MUTATIONS}:
        with open(path, encoding="utf-8") as fh:
            originals[path] = fh.read()

    try:
        for label, path, real, mutant, probe, guards in MUTATIONS:
            original = originals[path]
            if original.count(real) != 1:
                failures.append(f"{label}: 锚点命中 {original.count(real)} 次，应为 1")
                continue
            mutated = original.replace(real, mutant, 1)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(mutated)
            if run(probe) == 0:
                failures.append(f"{label}: 目标测试未变红")
            for guard in guards:
                if run(guard) != 0:
                    failures.append(f"{label}: 正交保护 {guard} 意外变红")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(original)
        if failures:
            print("MUTATION CHECK FAILED:")
            for line in failures:
                print(" -", line)
            return 1
        print(f"OK: {len(MUTATIONS)} mutations killed")
        return 0
    finally:
        for path, text in originals.items():
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)


if __name__ == "__main__":
    sys.exit(main())
