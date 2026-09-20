"""反证：缺陷 7 的引文判据必须被测试真守住。

每处注入一个「退回旧行为 / 松掉判据」的突变，探针必须变红，护栏必须仍绿：

1. 退回字节级逐字相同 → 真机那条代词还原又被判死。
2. 最长重合恒等于引文长度 → 凭空捏造的引文被放行（检查形同取消）。
3. 把最小重合长度降到 1 → 拼贴式引文被放行。
4. 去掉空证据检查 → 导演可以不给证据就下判断。
5. 去掉空白引用检查 → 一串空格可以冒充证据。

用法：python deploy/novel/mutate_check_d7.py
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
T = "tests/unit/novel/test_d7_quote.py"

# ---- 突变 1：退回字节级逐字相同 ----
M1_REAL = """    run = _longest_common_run(quote, text)
    need = min(len(quote), max(QUOTE_MIN_RUN, int(QUOTE_MIN_RATIO * len(quote))))
    return run >= need"""
M1_MUTANT = """    return False"""

# ---- 突变 2：最长重合恒等于引文长度（等于取消检查）----
M2_REAL = """    prev = [0] * (len(text) + 1)
    best = 0"""
M2_MUTANT = """    return len(quote)
    prev = [0] * (len(text) + 1)
    best = 0"""

# ---- 突变 3：最小重合长度降到 1 ----
M3_REAL = "QUOTE_MIN_RUN = 12\n"
M3_MUTANT = "QUOTE_MIN_RUN = 1\n"

# ---- 突变 4：去掉空证据检查 ----
M4_REAL = """    if not quotes:
        raise ProductionStopped("导演判断缺少可核对的原文证据")"""
M4_MUTANT = """    if False:
        raise ProductionStopped("导演判断缺少可核对的原文证据")"""

# ---- 突变 5：去掉空白引用检查 ----
M5_REAL = """        if not stripped:
            raise ProductionStopped("导演判断缺少可核对的原文证据：存在空白引用")"""
M5_MUTANT = """        if False:
            raise ProductionStopped("导演判断缺少可核对的原文证据：存在空白引用")"""

# ---- 突变 6：可引用文本里去掉 rule_issues ----
M6_REAL = """        + [str(issue) for issue in take.get("rule_issues") or []]
    )"""
M6_MUTANT = """    )"""

# ---- 突变 7：自修次数归零（等于不给修正机会）----
M7_REAL = "MAX_JUDGE_REPAIRS = 1\n"
M7_MUTANT = "MAX_JUDGE_REPAIRS = 0\n"

# ---- 突变 8：自修沿用原 command_id ----
M8_REAL = """            repair_no=repair_no,
        )"""
M8_MUTANT = """            repair_no=0,
        )"""

# ---- 突变 9：反馈里不给可抄录的原文 ----
M9_REAL = 'repair = [f"上一版判断不能采用：{exc}", EVIDENCE_REPAIR_RULE, stage_text[:1500]]'
M9_MUTANT = 'repair = [f"上一版判断不能采用：{exc}", EVIDENCE_REPAIR_RULE]'

# ---- 突变 10：退回字节级（跳读引用又被判成捏造）----
M10_REAL = """    segments = [seg for seg in _QUOTE_ELLIPSIS.split(stripped) if seg.strip()]
    if len(segments) > 1:"""
M10_MUTANT = """    segments = []
    if False:"""

# ---- 突变 11：省略号片段只要求「有一段对得上」----
M11_REAL = "        return all(_covered(seg.strip(), text) for seg in segments)"
M11_MUTANT = "        return any(_covered(seg.strip(), text) for seg in segments)"

PROBE_TESTS = (
    "test_pronoun_resolved_quote_is_accepted",
    "test_exact_quote_is_accepted",
    "test_short_quote_must_match_in_full",
    "test_mosaic_quote_is_rejected",
    "test_fabricated_quote_is_still_rejected",
    "test_one_bad_quote_poisons_the_whole_judgment",
    "test_empty_evidence_is_rejected",
    "test_blank_quote_is_rejected",
    "test_longest_common_run_counts_contiguous_characters_only",
    "test_rule_issues_are_quotable",
    "test_stage_text_covers_events_actions_dialogue_and_issues",
    "test_bad_evidence_gets_one_feedback_retry_instead_of_killing_the_chapter",
    "test_repair_budget_is_bounded",
    "test_elided_quote_is_accepted_when_every_kept_fragment_is_verbatim",
    "test_elided_quote_with_one_fabricated_fragment_is_rejected",
    "test_ellipsis_does_not_rescue_a_fabricated_quote",
    "test_elided_fragment_may_be_slightly_rewritten_but_must_stay_grounded",
)

MUTATIONS = (
    ("1 退回字节级逐字相同", M1_REAL, M1_MUTANT,
     "test_pronoun_resolved_quote_is_accepted",
     ("test_exact_quote_is_accepted", "test_fabricated_quote_is_still_rejected")),
    ("2 最长重合恒等于引文长度", M2_REAL, M2_MUTANT,
     "test_fabricated_quote_is_still_rejected",
     ("test_exact_quote_is_accepted", "test_pronoun_resolved_quote_is_accepted")),
    ("3 最小重合长度降到 1", M3_REAL, M3_MUTANT,
     "test_mosaic_quote_is_rejected",
     ("test_exact_quote_is_accepted", "test_pronoun_resolved_quote_is_accepted")),
    ("4 去掉空证据检查", M4_REAL, M4_MUTANT,
     "test_empty_evidence_is_rejected",
     ("test_exact_quote_is_accepted", "test_fabricated_quote_is_still_rejected")),
    ("5 去掉空白引用检查", M5_REAL, M5_MUTANT,
     "test_blank_quote_is_rejected",
     ("test_exact_quote_is_accepted", "test_empty_evidence_is_rejected")),
    ("6 可引用文本去掉 rule_issues", M6_REAL, M6_MUTANT,
     "test_rule_issues_are_quotable",
     ("test_exact_quote_is_accepted", "test_fabricated_quote_is_still_rejected")),
    ("7 自修次数归零", M7_REAL, M7_MUTANT,
     "test_bad_evidence_gets_one_feedback_retry_instead_of_killing_the_chapter",
     ("test_fabricated_quote_is_still_rejected", "test_repair_budget_is_bounded")),
    ("8 自修沿用原 command_id", M8_REAL, M8_MUTANT,
     "test_bad_evidence_gets_one_feedback_retry_instead_of_killing_the_chapter",
     ("test_fabricated_quote_is_still_rejected", "test_repair_budget_is_bounded")),
    ("9 反馈不给可抄录原文", M9_REAL, M9_MUTANT,
     "test_bad_evidence_gets_one_feedback_retry_instead_of_killing_the_chapter",
     ("test_fabricated_quote_is_still_rejected", "test_repair_budget_is_bounded")),
    ("10 退回字节级比对", M10_REAL, M10_MUTANT,
     "test_elided_quote_is_accepted_when_every_kept_fragment_is_verbatim",
     ("test_fabricated_quote_is_still_rejected",
      "test_pronoun_resolved_quote_is_accepted")),
    ("11 跳读片段只要求一段对得上", M11_REAL, M11_MUTANT,
     "test_elided_quote_with_one_fabricated_fragment_is_rejected",
     ("test_elided_quote_is_accepted_when_every_kept_fragment_is_verbatim",
      "test_fabricated_quote_is_still_rejected")),
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
        print(f"[FAIL] {label}: 找不到唯一锚点（count={original.count(real)}）"
              "——改动后请同步本脚本")
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
        bad = [p for p, rc in zip(probes, before) if rc != 0]
        print("[FAIL] 变异前就有测试不过，反证无意义:", bad)
        return 2

    results = [mutate(r, m, label, probe, guards)
               for label, r, m, probe, guards in MUTATIONS]

    after = [run(p) for p in probes]
    print("还原后探针 rc =", set(after), "（期望 {0}）")
    ok = all(results) and all(rc == 0 for rc in after)
    print("[PASS] 缺陷 7 的引文判据被真守卫" if ok else "[FAIL] 反证未成立")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
