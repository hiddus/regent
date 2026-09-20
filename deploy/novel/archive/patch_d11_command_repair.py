"""缺陷 11（D-07 选定方案）：命令被 Runtime 拒绝 → 带原因自修有限次，坚持才判死。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/direction.py"

EDITS: list[tuple[str, str, str]] = []


def sub(label: str, old: str, new: str) -> None:
    EDITS.append((label, old, new))


# ---------------------------------------------------------------- 1. 常量与规则
sub(
    "MAX_COMMAND_REPAIRS",
    "MAX_JUDGE_REPAIRS = 1\n",
    "MAX_JUDGE_REPAIRS = 1\n"
    "# 命令被 Runtime 拒绝后的自修次数。真机卡点：正文漏写已结算状态 → 硬失败 →\n"
    "# 导演仍选 ACCEPT → 被拒 → 整章判死。选这个而不是「悄悄换成 REWRITE」：\n"
    "# 换动作等于替导演绕过独立核验（既有测试编码的安全属性），而自修是让它**自己**\n"
    "# 改选一个合法动作，坚持 ACCEPT 才判死。\n"
    "MAX_COMMAND_REPAIRS = 1\n"
    "COMMAND_REPAIR_RULE = (\n"
    '    "请改选一个**当前仍然合法**的动作，并说明为什么；不要重复刚才被拒绝的动作。"\n'
    ")\n",
)

# --------------------------------------------------- 2. _grounded_judgment 扩展
sub(
    "_grounded_judgment signature",
    '''async def _grounded_judgment[T: BaseModel](
    call: Any,
    schema: type[T],
    system: str,
    payload: dict[str, Any],
    stage_text: str,
    label: str,
) -> T:
    """带反馈自修的导演判断：引文不合规时把**原因**回传，而不是判死整章。

    自修必须换 command_id：沿用原 id 会被幂等键挡住，或复用上一次的坏结果。
    自修次数用尽才判死——判据不放松，只是给模型一次看见错误的机会。
    """
    repair: list[str] = []
    for repair_no in range(MAX_JUDGE_REPAIRS + 1):
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
        return result
    raise ProductionStopped(f"{label}：引文自修次数已用尽")''',
    '''async def _grounded_judgment[T: BaseModel](
    call: Any,
    schema: type[T],
    system: str,
    payload: dict[str, Any],
    stage_text: str,
    label: str,
    *,
    coerce: Any = None,
    command_check: Any = None,
) -> T:
    """带反馈自修的导演判断：不合规时把**原因**回传，而不是判死整章。

    两类问题共用一条自修通道，因为 ``CommandRejected`` 是 ``ProductionStopped``
    的子类，而两者的共同点是：**模型看不见自己错在哪**。

    - 引文不合规 → 回传「哪条引用、差多少、原文就在下面」；
    - 命令被 Runtime 拒绝 → 回传拒绝原因，请它改选一个合法动作。

    自修必须换 command_id：沿用原 id 会被幂等键挡住，或复用上一次的坏结果。
    自修次数用尽才判死——判据一次都没放松，只是给模型看见错误的机会。

    ``coerce`` 在校验**之前**就地把注定非法的动作换掉（留痕由调用方收集），
    ``command_check`` 随后校验替换后的动作，两者顺序不能颠倒。
    """
    repair: list[str] = []
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
    raise ProductionStopped(f"{label}：自修次数已用尽")''',
)

# ------------------------------------------------------- 3. 命令校验的公共构造
sub(
    "_validate_action",
    "def _coerce_illegal_action(phase: str, result: Any, take: dict[str, Any]) -> list[str]:\n",
    "def _validate_action(\n"
    "    production: dict[str, Any],\n"
    "    run: Any,\n"
    "    take: dict[str, Any],\n"
    "    phase: str,\n"
    "    result: Any,\n"
    "    commands: dict[str, Any],\n"
    "    actors: list[dict[str, Any]],\n"
    ") -> tuple[RuntimeState, tuple[str, str]]:\n"
    '    """构造并执行一次命令校验；被拒时抛 ``CommandRejected``。\n'
    "\n"
    "    WATCH_TAKE 与 WATCH_PROSE 的命令载荷结构完全一致，所以只留一份：两边都\n"
    "    要能**在自修循环里**反复校验，不能只在落库后校验一次。\n"
    '    """\n'
    "    state = _runtime_state(production, run, take)\n"
    "    target = _RUNTIME.validate(\n"
    "        director_command(\n"
    "            commands[result.action],\n"
    "            command_id=_command_id(production, run, phase),\n"
    "            input_version=_input_version(run),\n"
    "            scene_index=production[\"scene_index\"],\n"
    "            take_no=take[\"take_no\"],\n"
    "            evidence=result.evidence,\n"
    "            actors=actors,\n"
    "            revised_brief=(\n"
    "                result.revised_brief.model_dump(mode=\"json\")\n"
    "                if result.revised_brief is not None\n"
    "                else None\n"
    "            ),\n"
    "        ),\n"
    "        state,\n"
    "    )\n"
    "    return state, target\n"
    "\n"
    "\n"
    "def _coerce_illegal_action(phase: str, result: Any, take: dict[str, Any]) -> list[str]:\n",
)

# ------------------------------------------------------------- 4. WATCH_TAKE
sub(
    "watch take site",
    '''        result = await _grounded_judgment(
            call,
            TakeDirection,''',
    '''        checked: dict[str, Any] = {}
        coerced: list[str] = []

        def _check_take(result: Any) -> None:
            coerced.clear()
            coerced.extend(_coerce_illegal_action(phase, result, take))
            checked["state"], checked["target"] = _validate_action(
                production, run, take, phase, result, _TAKE_COMMANDS, brief["actors"]
            )

        result = await _grounded_judgment(
            call,
            TakeDirection,''',
)

sub(
    "watch take tail",
    '''            watch.payload,
            _stage_text(take),
            "观看表演",
        )
        _record_manifest(take, watch)
        coerced = _coerce_illegal_action(phase, result, take)
        production["decisions"].append(''',
    '''            watch.payload,
            _stage_text(take),
            "观看表演",
            coerce=_check_take_wrapper,
            command_check=_check_take,
        )
        _record_manifest(take, watch)
        production["decisions"].append(''',
)

# ------------------------------------------------------------ 5. WATCH_PROSE
sub(
    "watch prose site",
    '''        result = await _grounded_judgment(
            call,
            ProseDirection,''',
    '''        checked: dict[str, Any] = {}
        coerced: list[str] = []

        def _check_prose(result: Any) -> None:
            coerced.clear()
            coerced.extend(_coerce_illegal_action(phase, result, take))
            checked["state"], checked["target"] = _validate_action(
                production, run, take, phase, result, _PROSE_COMMANDS, brief["actors"]
            )

        result = await _grounded_judgment(
            call,
            ProseDirection,''',
)

sub(
    "watch prose tail",
    '''            take["content"],
            "审阅正文",
        )
        coerced = _coerce_illegal_action(phase, result, take)
        production["decisions"].append(''',
    '''            take["content"],
            "审阅正文",
            coerce=_check_prose,
            command_check=_check_prose,
        )
        production["decisions"].append(''',
)

# -------------------------------------------- 6. 两处改用已校验的 state / target
sub(
    "take uses checked",
    '''        state = _runtime_state(production, run, take)
        target = _RUNTIME.validate(
            director_command(
                _TAKE_COMMANDS[result.action],
                command_id=_command_id(production, run, phase),
                input_version=_input_version(run),
                scene_index=production["scene_index"],
                take_no=take["take_no"],
                evidence=result.evidence,
                actors=brief["actors"],
                revised_brief=(
                    result.revised_brief.model_dump(mode="json")
                    if result.revised_brief is not None
                    else None
                ),
            ),
            state,
        )
        if result.action == "RETAKE":''',
    '''        state, target = checked["state"], checked["target"]
        if result.action == "RETAKE":''',
)

sub(
    "prose uses checked",
    '''        state = _runtime_state(production, run, take)
        target = _RUNTIME.validate(
            director_command(
                _PROSE_COMMANDS[result.action],
                command_id=_command_id(production, run, phase),
                input_version=_input_version(run),
                scene_index=production["scene_index"],
                take_no=take["take_no"],
                evidence=result.evidence,
                actors=brief["actors"],
                revised_brief=(
                    result.revised_brief.model_dump(mode="json")
                    if result.revised_brief is not None
                    else None
                ),
            ),
            state,
        )
        if result.action == "RETAKE":''',
    '''        state, target = checked["state"], checked["target"]
        if result.action == "RETAKE":''',
)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    # coerce 与 command_check 合并成一个回调即可，去掉多余的包装名
    text = text.replace(
        "            coerce=_check_take_wrapper,\n            command_check=_check_take,\n",
        "            coerce=_check_take,\n            command_check=_check_take,\n",
    )
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
