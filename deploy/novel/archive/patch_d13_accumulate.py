"""D-13：自修反馈**累积**，且被拒时**枚举还有哪些动作执行得了**。

两条都是真机打出来的，不是推演：

1. **反馈被覆盖**。第一版引文不合格 → 修；第二版引文改好了、命令被拒 → 修；
   第三版按命令反馈改了动作，又把引文退回第一版的坏样子（``draft: '…'``），
   最后两边都没过。原因是 ``repair = [...]`` 是**赋值**：后一次失败把前一次的
   约束顶掉了。改成累积。

2. **模型不知道还剩哪个动作合法**。真机终局：修订次数用尽后 REWRITE 非法、
   存在硬失败时 ACCEPT 非法，导演连试三个全被拒。可执行性是代码能**确定性算
   出来**的（Runtime 是纯函数），不该让模型猜。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/direction.py"

OLD_DOC = """    自修次数用尽才判死——判据一次都没放松，只是给模型看见错误的机会。"""
NEW_DOC = """    自修次数用尽才判死——判据一次都没放松，只是给模型看见错误的机会。

    **反馈是累积的**：第二次失败不能把第一次的约束顶掉。真机就是这样死的——引文
    改好了、命令却被拒，按命令改完动作又把引文退回坏样子，最后一次两边都没过。"""

OLD_SIG = """    *,
    coerce: Any = None,
    command_check: Any = None,
) -> T:"""
NEW_SIG = """    *,
    coerce: Any = None,
    command_check: Any = None,
    command_report: Any = None,
) -> T:"""

OLD_BODY = """    repair: list[str] = []
    # 耗尽时必须带上**最后一次被拒的原因**：只报"次数用尽"会把 Runtime 的真实
    # 理由吞掉，既没法诊断，也会让依赖该理由的判据静默失效。
    last_reason: str | None = None
    attempts = 1 + MAX_JUDGE_REPAIRS + MAX_COMMAND_REPAIRS
    for repair_no in range(attempts):
        result = await call(
            schema,
            system,
            {**payload, "repair_instructions": repair} if repair else payload,
            repair_no=repair_no,
        )
        try:
            _quote_check(result.evidence, stage_text)
        except ProductionStopped as exc:
            last_reason = str(exc)
            repair = [f"上一版判断不能采用：{exc}", EVIDENCE_REPAIR_RULE, stage_text[:1500]]
            continue
        if coerce is not None:
            coerce(result)
        if command_check is not None:
            try:
                command_check(result)
            except ProductionStopped as exc:
                # 不是引文问题：得让导演**改选一个动作**，光给原文没用。
                last_reason = str(exc)
                repair = [f"上一版动作不能执行：{exc}", COMMAND_REPAIR_RULE]
                continue
        return result"""
NEW_BODY = """    repair: list[str] = []
    # 耗尽时必须带上**最后一次被拒的原因**：只报"次数用尽"会把 Runtime 的真实
    # 理由吞掉，既没法诊断，也会让依赖该理由的判据静默失效。
    last_reason: str | None = None
    attempts = 1 + MAX_JUDGE_REPAIRS + MAX_COMMAND_REPAIRS
    for repair_no in range(attempts):
        result = await call(
            schema,
            system,
            {**payload, "repair_instructions": repair} if repair else payload,
            repair_no=repair_no,
        )
        try:
            _quote_check(result.evidence, stage_text)
        except ProductionStopped as exc:
            last_reason = str(exc)
            # 累积：之前已经告诉过它的约束不能丢。
            repair = repair + [f"上一版判断不能采用：{exc}", EVIDENCE_REPAIR_RULE]
            if not any(item == stage_text[:1500] for item in repair):
                repair.append(stage_text[:1500])
            continue
        if coerce is not None:
            coerce(result)
        if command_check is not None:
            try:
                command_check(result)
            except ProductionStopped as exc:
                # 不是引文问题：得让导演**改选一个动作**，光给原文没用。
                last_reason = str(exc)
                repair = repair + [f"上一版动作不能执行：{exc}"]
                if command_report is not None:
                    repair.append(
                        "当前状态下各动作的可执行性：\\n"
                        + "\\n".join(command_report(result))
                    )
                repair.append(COMMAND_REPAIR_RULE)
                continue
        return result"""

# ---------------------------------------------------------------- 可执行性枚举
OLD_LEGAL = """def _validate_action("""
NEW_LEGAL = '''def _legal_action_report(
    production: dict[str, Any],
    run: Any,
    take: dict[str, Any],
    phase: str,
    result: Any,
    commands: dict[str, Any],
    actors: list[dict[str, Any]],
) -> list[str]:
    """枚举**按这份判断的内容**每个动作能不能执行，并给出理由。

    模型看不见状态机：它只知道自己的动作被拒了，不知道还能选什么。真机终局就是
    这样来的——修订次数用尽后 REWRITE 非法、存在硬失败时 ACCEPT 非法，导演连试
    三个全被拒，整章判死。可执行性是 Runtime 能**确定性**算出来的（``validate``
    是纯函数），不该让模型猜。

    注意是"按这份判断的内容"：RETAKE 需要 revised_brief，模型没给就不算可执行
    ——那正是它下一步要补的东西，报"不可执行（重演必须给出…）"比报"可执行"有用。
    """
    lines: list[str] = []
    for action in commands:
        probe = result.model_copy(update={"action": action})
        try:
            _validate_action(production, run, take, phase, probe, commands, actors)
        except ProductionStopped as exc:
            lines.append(f"- {action}：不可执行（{exc}）")
        else:
            lines.append(f"- {action}：可执行")
    return lines


def _validate_action('''

# ---------------------------------------------------------------- 调用点接上
OLD_TAKE_REPORT = """            coerce=_coerce_take,
            command_check=_check_take,
        )"""
NEW_TAKE_REPORT = """            coerce=_coerce_take,
            command_check=_check_take,
            command_report=_report_take,
        )"""

OLD_TAKE_DEF = """        def _check_take(result: Any) -> None:
            checked["state"], checked["target"] = _validate_action(
                production, run, take, phase, result, _TAKE_COMMANDS, brief["actors"]
            )
"""
NEW_TAKE_DEF = """        def _check_take(result: Any) -> None:
            checked["state"], checked["target"] = _validate_action(
                production, run, take, phase, result, _TAKE_COMMANDS, brief["actors"]
            )

        def _report_take(result: Any) -> list[str]:
            return _legal_action_report(
                production, run, take, phase, result, _TAKE_COMMANDS, brief["actors"]
            )
"""

OLD_PROSE_REPORT = """            coerce=_coerce_prose,
            command_check=_check_prose,
        )"""
NEW_PROSE_REPORT = """            coerce=_coerce_prose,
            command_check=_check_prose,
            command_report=_report_prose,
        )"""

OLD_PROSE_DEF = """        def _check_prose(result: Any) -> None:
            checked["state"], checked["target"] = _validate_action(
                production, run, take, phase, result, _PROSE_COMMANDS, brief["actors"]
            )
"""
NEW_PROSE_DEF = """        def _check_prose(result: Any) -> None:
            checked["state"], checked["target"] = _validate_action(
                production, run, take, phase, result, _PROSE_COMMANDS, brief["actors"]
            )

        def _report_prose(result: Any) -> list[str]:
            return _legal_action_report(
                production, run, take, phase, result, _PROSE_COMMANDS, brief["actors"]
            )
"""

EDITS = [
    ("doc", OLD_DOC, NEW_DOC),
    ("sig", OLD_SIG, NEW_SIG),
    ("body", OLD_BODY, NEW_BODY),
    ("legal", OLD_LEGAL, NEW_LEGAL),
    ("take.def", OLD_TAKE_DEF, NEW_TAKE_DEF),
    ("take.report", OLD_TAKE_REPORT, NEW_TAKE_REPORT),
    ("prose.def", OLD_PROSE_DEF, NEW_PROSE_DEF),
    ("prose.report", OLD_PROSE_REPORT, NEW_PROSE_REPORT),
]


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    orig = src
    for name, old, new in EDITS:
        count = src.count(old)
        assert count == 1, f"{name}: anchor hit {count} times, expected 1"
        src = src.replace(old, new, 1)
    assert src != orig
    compile(src, str(TARGET), "exec")
    TARGET.write_text(src, encoding="utf-8")
    print(f"[OK] direction.py 已改写（{len(EDITS)} 处）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
