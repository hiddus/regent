"""D-11c：自修耗尽时必须把**最后一次被拒的原因**带出来。

原实现只报「自修次数已用尽」，把 Runtime 的真实拒绝理由吞了：
- 真机诊断价值没了（只知道"改了两次没改对"，不知道"为什么不能执行"）；
- 既有测试 ``match="不能接受"`` 会失守——它守的正是那条拒绝理由。

同时更正模块 docstring 里「每 tick 最多一次模型调用」的说法：自修让一个 tick
可能发出多次调用。被守住的不是"一次调用"，而是**一次提交的决策**——预算本身
按模型调用计数（``_call`` 里 ``call_count += 1`` 并受 ``MAX_CALLS`` 约束），
金额另有 ``MAX_COST_MINOR`` 兜底，所以自修不会捅穿预算。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/direction.py"

OLD_DOC = """Each tick makes at most one model call. The caller commits the checkpoint with
the call ledger. A crash before that commit can still leave an UNKNOWN external
call; this module does not claim provider-side exactly-once execution.
"""
NEW_DOC = """Each tick commits at most one **decision**; the caller commits the checkpoint
with the call ledger. A tick may issue more than one model call when a judgment is
repaired in place (evidence or command rejection, bounded by
``MAX_JUDGE_REPAIRS``/``MAX_COMMAND_REPAIRS``). Repair calls are ordinary calls for
budget purposes: ``call_count`` counts them against ``MAX_CALLS`` and money is
capped separately by ``MAX_COST_MINOR``. A crash before the commit can still leave
an UNKNOWN external call; this module does not claim provider-side exactly-once
execution.
"""

OLD_TAIL = """    repair: list[str] = []
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
            repair = [f"上一版判断不能采用：{exc}", EVIDENCE_REPAIR_RULE, stage_text[:1500]]
            continue
        if coerce is not None:
            coerce(result)
        if command_check is not None:
            try:
                command_check(result)
            except ProductionStopped as exc:
                # 不是引文问题：得让导演**改选一个动作**，光给原文没用。
                repair = [f"上一版动作不能执行：{exc}", COMMAND_REPAIR_RULE]
                continue
        return result
    raise ProductionStopped(f"{label}：自修次数已用尽")
"""
NEW_TAIL = """    repair: list[str] = []
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
        return result
    raise ProductionStopped(
        f"{label}：自修次数已用尽"
        + (f"，最后一次未通过的原因：{last_reason}" if last_reason else "")
    )
"""

EDITS = [
    ("docstring", OLD_DOC, NEW_DOC),
    ("last_reason", OLD_TAIL, NEW_TAIL),
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
