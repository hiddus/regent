"""缺陷 7c：引文判据缺的不是宽松度，是**修正机会**——加带反馈的 bounded 自修。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/direction.py"

EDITS: list[tuple[str, str, str]] = []


def sub(label: str, old: str, new: str) -> None:
    EDITS.append((label, old, new))


# ------------------------------------------------------------------ 1. 常量
sub(
    "MAX_JUDGE_REPAIRS",
    "QUOTE_MIN_RUN = 12\nQUOTE_MIN_RATIO = 0.6\n",
    "QUOTE_MIN_RUN = 12\nQUOTE_MIN_RATIO = 0.6\n"
    "# 判断类调用（观看表演 / 审阅正文）的引文自修次数。\n"
    "# 为什么必须给修正机会而不是继续放宽判据：真机样本一版比一版接近\n"
    "# （重合 17/49 → 24/28），阈值再往下调就只剩形式。而模型看到「哪条引用、\n"
    "# 差多少、原文就在下面」通常能立刻改对——看不到就只能重犯。\n"
    "MAX_JUDGE_REPAIRS = 1\n",
)

# --------------------------------------------------------- 2. 自修 helper
sub(
    "_grounded_judgment",
    "def _stage_text(take: dict[str, Any]) -> str:\n",
    "EVIDENCE_REPAIR_RULE = (\n"
    '    "evidence 必须**原样抄录**下面一段原文里的一句话，一个字都不要改：\\n"\n'
    '    "不要加说话人前缀、不要加字段名、不要改写、不要把多句话拼成一句。"\n'
    ")\n"
    "\n"
    "\n"
    "async def _grounded_judgment[T: BaseModel](\n"
    "    call: Any,\n"
    "    schema: type[T],\n"
    "    system: str,\n"
    "    payload: dict[str, Any],\n"
    "    stage_text: str,\n"
    "    label: str,\n"
    ") -> T:\n"
    '    """带反馈自修的导演判断：引文不合规时把**原因**回传，而不是判死整章。\n'
    "\n"
    "    自修必须换 command_id：沿用原 id 会被幂等键挡住，或复用上一次的坏结果。\n"
    "    自修次数用尽才判死——判据不放松，只是给模型一次看见错误的机会。\n"
    '    """\n'
    "    repair: list[str] = []\n"
    "    for repair_no in range(MAX_JUDGE_REPAIRS + 1):\n"
    "        result = await call(\n"
    "            schema,\n"
    "            system,\n"
    "            {**payload, \"repair_instructions\": repair} if repair else payload,\n"
    "            repair_no=repair_no,\n"
    "        )\n"
    "        try:\n"
    "            _quote_check(result.evidence, stage_text)\n"
    "        except ProductionStopped as exc:\n"
    "            repair = [f\"上一版判断不能采用：{exc}\", EVIDENCE_REPAIR_RULE, stage_text[:1500]]\n"
    "            continue\n"
    "        return result\n"
    "    raise ProductionStopped(f\"{label}：引文自修次数已用尽\")\n"
    "\n"
    "\n"
    "def _stage_text(take: dict[str, Any]) -> str:\n",
)

# ------------------------------------------------------ 3. call 支持 repair_no
sub(
    "call repair_no",
    '''    async def call[T: BaseModel](schema: type[T], system: str, payload: dict[str, Any]) -> T:
        return await _call(
            session,
            provider,
            work,
            run,
            production,
            schema,
            system,
            payload,
            prefix,
            _command_id(production, run, phase),
        )''',
    '''    async def call[T: BaseModel](schema: type[T], system: str, payload: dict[str, Any],
                                 *, repair_no: int = 0) -> T:
        command_id = _command_id(production, run, phase)
        if repair_no:
            command_id = f"{command_id}:r{repair_no}"
        return await _call(
            session,
            provider,
            work,
            run,
            production,
            schema,
            system,
            payload,
            prefix,
            command_id,
        )''',
)

# ---------------------------------------------------------- 4. WATCH_TAKE 站点
sub(
    "watch take site",
    '''        result = await call(TakeDirection, "你是导演，观看实际演绎，判断人物选择和场景效果。"
            "决定CONTINUE推进下一节拍、RETAKE改变调度重演、RENDER结束表演进入小说呈现。"
            "evidence必须逐字引用事件或可见行动。重演必须给出不同的revised_brief。"
            "有rule_issues必须重演；不要把自己变成打分编辑。"
            "若 user_decision 非空，用户已就这件事作出裁决：必须按其 near_term_consequence "
            "推进，不得执行其他选项的走向，也不得就同一件事再次请求裁决。", watch.payload)
        _record_manifest(take, watch)
        _quote_check(result.evidence, _stage_text(take))''',
    '''        result = await _grounded_judgment(
            call,
            TakeDirection,
            "你是导演，观看实际演绎，判断人物选择和场景效果。"
            "决定CONTINUE推进下一节拍、RETAKE改变调度重演、RENDER结束表演进入小说呈现。"
            "evidence必须逐字引用事件或可见行动。重演必须给出不同的revised_brief。"
            "有rule_issues必须重演；不要把自己变成打分编辑。"
            "若 user_decision 非空，用户已就这件事作出裁决：必须按其 near_term_consequence "
            "推进，不得执行其他选项的走向，也不得就同一件事再次请求裁决。",
            watch.payload,
            _stage_text(take),
            "观看表演",
        )
        _record_manifest(take, watch)''',
)

# --------------------------------------------------------- 5. WATCH_PROSE 站点
sub(
    "watch prose site",
    '''        result = await call(
            ProseDirection,
            "你是导演，观看小说呈现是否实现本场阅读体验、潜台词和人物情绪。"
            "给出具体观察和逐字正文evidence，决定ACCEPT、REWRITE表达或RETAKE表演。"
            "REWRITE必须给出具体不同的呈现指令；RETAKE必须给出不同的revised_brief。"
            "独立审校指出硬问题时不得ACCEPT。不要以文风评分代替创作决策。"
            "若 user_decision 非空，用户已就这件事作出裁决：必须按其 near_term_consequence "
            "取舍，不得执行其他选项的走向，也不得就同一件事再次请求裁决。",
            {
                "brief": brief,
                "draft": take["content"],
                "events": take["events"],
                "validation": take.get("validation"),
                "remaining_revisions": MAX_REVISIONS - take["revisions"],
                "user_decision": _pending_user_decision(run),
                # D-03：导演审阅正文同样只拿导演视图记忆。
                "director_memory": _memory_view(memory_payloads, "director"),
            },
        )
        _quote_check(result.evidence, take["content"])''',
    '''        result = await _grounded_judgment(
            call,
            ProseDirection,
            "你是导演，观看小说呈现是否实现本场阅读体验、潜台词和人物情绪。"
            "给出具体观察和逐字正文evidence，决定ACCEPT、REWRITE表达或RETAKE表演。"
            "REWRITE必须给出具体不同的呈现指令；RETAKE必须给出不同的revised_brief。"
            "独立审校指出硬问题时不得ACCEPT。不要以文风评分代替创作决策。"
            "若 user_decision 非空，用户已就这件事作出裁决：必须按其 near_term_consequence "
            "取舍，不得执行其他选项的走向，也不得就同一件事再次请求裁决。",
            {
                "brief": brief,
                "draft": take["content"],
                "events": take["events"],
                "validation": take.get("validation"),
                "remaining_revisions": MAX_REVISIONS - take["revisions"],
                "user_decision": _pending_user_decision(run),
                # D-03：导演审阅正文同样只拿导演视图记忆。
                "director_memory": _memory_view(memory_payloads, "director"),
            },
            take["content"],
            "审阅正文",
        )''',
)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    for label, old, new in EDITS:
        count = text.count(old)
        if count != 1:
            print(f"[FAIL] 锚点 {label} 命中 {count} 次（期望 1）")
            return 1
        text = text.replace(old, new, 1)
    try:
        compile(text, str(TARGET), "exec")
    except SyntaxError as exc:
        print(f"[FAIL] 语法错误 {exc}")
        return 1
    TARGET.write_text(text, encoding="utf-8")
    print(f"[OK] {TARGET.name} 已改写（{len(EDITS)} 处）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
