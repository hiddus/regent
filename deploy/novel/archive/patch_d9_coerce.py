"""缺陷 9：命令被 Runtime 拒绝即判死整章——可判定为非法的动作应换成唯一可执行的那个。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/direction.py"

EDITS: list[tuple[str, str, str]] = []


def sub(label: str, old: str, new: str) -> None:
    EDITS.append((label, old, new))


APPEND_TAKE = '''        production["decisions"].append(
            {
                "phase": phase,
                "scene_index": production["scene_index"],
                "take_no": take["take_no"],
                **result.model_dump(mode="json"),
            }
        )'''


def _append_with_note() -> str:
    return '''        coerced = _coerce_illegal_action(phase, result, take)
        production["decisions"].append(
            {
                "phase": phase,
                "scene_index": production["scene_index"],
                "take_no": take["take_no"],
                **({"coerced": coerced} if coerced else {}),
                **result.model_dump(mode="json"),
            }
        )'''


# ------------------------------------------------------------------ 1. helper
sub(
    "_coerce_illegal_action",
    "def _apply_state(runtime: CommandRuntime, take: dict[str, Any], state: RuntimeState,\n",
    "def _coerce_illegal_action(phase: str, result: Any, take: dict[str, Any]) -> list[str]:\n"
    '    """把**注定被 Runtime 拒绝**的动作换成唯一还能执行的那个，并返回留痕说明。\n'
    "\n"
    "    真机两种死法：节拍用尽时导演仍选 CONTINUE；存在硬失败时导演仍选 ACCEPT。\n"
    "    两者都是**可判定的**非法——不需要再问模型一次（规划用 temperature=0，入参不变的\n"
    "    重试会以近乎确定的方式产出同一个非法动作）。换动作也不是替导演做创作决定：\n"
    "    留下来的 instruction / revised_brief 仍然是导演自己给的内容，只是走一条还能走的\n"
    "    路径。留痕随之入库，不静默。\n"
    '    """\n'
    "    notes: list[str] = []\n"
    "    if phase == \"WATCH_TAKE\" and result.action == \"CONTINUE\":\n"
    "        if MAX_TURNS - int(take.get(\"turn\", 0)) - 1 <= 0:\n"
    '            result.action = "RENDER"\n'
    '            notes.append("节拍已用尽，CONTINUE 不可执行，按 RENDER 收束本场")\n'
    '    elif phase == "WATCH_PROSE" and result.action == "ACCEPT":\n'
    '        if (take.get("validation") or {}).get("passed") is False:\n'
    '            result.action = "REWRITE"\n'
    '            notes.append("存在硬失败，ACCEPT 不可执行，按 REWRITE 退回重写")\n'
    "    return notes\n"
    "\n"
    "\n"
    "def _apply_state(runtime: CommandRuntime, take: dict[str, Any], state: RuntimeState,\n",
)

# ------------------------------------------------------------- 2. WATCH_TAKE
sub(
    "watch take decisions",
    '        _record_manifest(take, watch)\n' + APPEND_TAKE,
    "        _record_manifest(take, watch)\n" + _append_with_note(),
)

# ------------------------------------------------------------ 3. WATCH_PROSE
PROSE_TAIL = '''\n        if result.request_decision is not None:
            await _request_user_decision(
                session,
                work=work,
                run=run,
                production=production,
                spec=result.request_decision,
                phase=phase,
            )
            return False
        # 与 WATCH_TAKE 同理：裁决在此被导演执行一次后消费。'''

sub(
    "watch prose decisions",
    APPEND_TAKE + PROSE_TAIL,
    _append_with_note()
    + '''\n        if result.request_decision is not None:
            await _request_user_decision(
                session,
                work=work,
                run=run,
                production=production,
                spec=result.request_decision,
                phase=phase,
            )
            return False
        # 与 WATCH_TAKE 同理：裁决在此被导演执行一次后消费。''',
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
