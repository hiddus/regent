"""反证：缺陷 11 的命令自修必须被测试真守住。

用法：python deploy/novel/mutate_check_d11.py

每条变异 = 把一个已修好的点改回坏样子；探针测试必须变红，护栏测试必须仍绿。
护栏选的是**正交**的那条：例如"自修通道被删"的护栏不能选"预算用尽判死"，
因为预算测试在通道被删时也会红，那样反证是假的。
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
T = "tests/unit/novel/test_d11_command_repair.py"

# 1 命令被拒不再给改选机会，直接判死（回到缺陷 11 原状）
M1_REAL = """            except ProductionStopped as exc:
                # 不是引文问题：得让导演**改选一个动作**，光给原文没用。
                last_reason = str(exc)
                repair = [f"上一版动作不能执行：{exc}", COMMAND_REPAIR_RULE]
                continue"""
M1_MUTANT = """            except ProductionStopped as exc:
                raise"""
# 7 耗尽时只报"次数用尽"，吞掉 Runtime 的真实拒绝理由
M7_REAL = """    raise ProductionStopped(
        f"{label}：自修次数已用尽"
        + (f"，最后一次未通过的原因：{last_reason}" if last_reason else "")
    )"""
M7_MUTANT = """    raise ProductionStopped(f"{label}：自修次数已用尽")"""

# 2 反馈不点名原因——模型不知道自己错在哪，会换回同一个动作
M2_REAL = """                repair = [f"上一版动作不能执行：{exc}", COMMAND_REPAIR_RULE]"""
M2_MUTANT = """                repair = [COMMAND_REPAIR_RULE]"""

# 3 自修沿用原 command_id：幂等键挡住或复用上一次的坏结果
M3_REAL = """        result = await call(
            schema,
            system,
            {**payload, "repair_instructions": repair} if repair else payload,
            repair_no=repair_no,
        )"""
M3_MUTANT = """        result = await call(
            schema,
            system,
            {**payload, "repair_instructions": repair} if repair else payload,
            repair_no=0,
        )"""

# 4 命令类反馈里塞原文——那是引文问题的解法，对"要模型改主意"没用
M4_REAL = """                repair = [f"上一版动作不能执行：{exc}", COMMAND_REPAIR_RULE]"""
M4_MUTANT = """                repair = [
                    f"上一版动作不能执行：{exc}",
                    COMMAND_REPAIR_RULE,
                    stage_text[:1500],
                ]"""

# 5 收束与校验顺序颠倒：校验的是模型给的动作，收束完没人再验
M5_REAL = """        if coerce is not None:
            coerce(result)
        if command_check is not None:
            try:
                command_check(result)
            except ProductionStopped as exc:
                # 不是引文问题：得让导演**改选一个动作**，光给原文没用。
                last_reason = str(exc)
                repair = [f"上一版动作不能执行：{exc}", COMMAND_REPAIR_RULE]
                continue"""
M5_MUTANT = """        if command_check is not None:
            try:
                command_check(result)
            except ProductionStopped as exc:
                # 不是引文问题：得让导演**改选一个动作**，光给原文没用。
                last_reason = str(exc)
                repair = [f"上一版动作不能执行：{exc}", COMMAND_REPAIR_RULE]
                continue
        if coerce is not None:
            coerce(result)"""

# 6 预算被放宽：引文与命令各修各的，整章可以无限重试
M6_REAL = """    attempts = 1 + MAX_JUDGE_REPAIRS + MAX_COMMAND_REPAIRS"""
M6_MUTANT = """    attempts = 1 + MAX_JUDGE_REPAIRS + MAX_COMMAND_REPAIRS + 5"""

# 引文自修的探针在别的文件，护栏一起带上，确认它没被这批改动碰坏
EVIDENCE_PROBE = (
    "tests/unit/novel/test_d7_quote.py"
    "::test_bad_evidence_gets_one_feedback_retry_instead_of_killing_the_chapter"
)

MUTATIONS = (
    ("1 命令被拒直接判死（无改选机会）", M1_REAL, M1_MUTANT,
     T + "::test_rejected_command_gets_a_feedback_retry_instead_of_killing_the_chapter",
     (EVIDENCE_PROBE,)),
    ("2 反馈不点名拒绝原因", M2_REAL, M2_MUTANT,
     T + "::test_command_feedback_names_the_rejection_reason",
     (T + "::test_rejected_command_gets_a_feedback_retry_instead_of_killing_the_chapter",)),
    # 护栏不能选「改选成功」：repair_no 恒为 0 时它必然也红，那样反证是假的。
    # 选「坚持非法动作仍然判死」——它不依赖 repair_no 分支。
    ("3 自修沿用原 command_id", M3_REAL, M3_MUTANT,
     T + "::test_command_repair_uses_a_fresh_command_id",
     (T + "::test_persisting_on_an_illegal_action_still_kills_the_chapter",)),
    ("4 命令反馈里塞原文", M4_REAL, M4_MUTANT,
     T + "::test_command_feedback_does_not_dump_the_stage_text",
     (T + "::test_command_feedback_names_the_rejection_reason",)),
    ("5 收束在校验之后", M5_REAL, M5_MUTANT,
     T + "::test_coercion_runs_before_command_check",
     (T + "::test_rejected_command_gets_a_feedback_retry_instead_of_killing_the_chapter",)),
    ("6 自修预算被放宽", M6_REAL, M6_MUTANT,
     T + "::test_evidence_and_command_repairs_share_one_budget",
     (T + "::test_rejected_command_gets_a_feedback_retry_instead_of_killing_the_chapter",)),
    ("7 耗尽时吞掉真实拒绝理由", M7_REAL, M7_MUTANT,
     T + "::test_budget_exhaustion_reports_the_last_rejection_reason",
     (T + "::test_persisting_on_an_illegal_action_still_kills_the_chapter",)),
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
                    restored = fh.read()
                if restored != original:
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

    print(f"\n引文自修护栏（确认未被碰坏）: {'OK' if run(EVIDENCE_PROBE) == 0 else 'RED'}")
    if failures:
        print("\n".join(failures))
        return 1
    print("全部变异均被抓住。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
