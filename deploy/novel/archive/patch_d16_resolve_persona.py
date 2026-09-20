"""D-16：RESOLVE 阶段 events 里的 known_by / dialogue_by_character 含未归一的
角色名时直接判死——不走自修通道。

真机死法（run19）：模型在 `dialogue_by_character` 里写 ``"父亲"``，cast 里的键是
``"陈父（陈远舟）"``。与缺陷 6 同型——身份键不匹配——但这次出在 RESOLVE 阶段
的 events 字段，且检查在 `produce_tick` 里直接判死。

修法：
1. 先用 `_canonical_persona` 做确定性归一（去掉末尾括号）——"陈父（陈远舟）"
   在 cast 里存在，模型写"陈父"或"陈父（陈远舟）"都能归一。
2. 归一不了的才判死，且把**哪个名字对不上**告诉模型。
3. 把检查提到 `_validate_action` 是不合适的（events 是 RESOLVE 输出，不是命令
   参数），所以改成：归一后仍判死时，把原因带进 ProductionStopped 的消息里。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/direction.py"

OLD_KNOWN = '''        if any(
            name not in production["cast"] for event in result.events for name in event.known_by
        ):
            raise ProductionStopped("结算事件含未知的知情人物")
        if any(
            name not in production["cast"]
            for event in result.events
            for name in event.dialogue_by_character
        ):
            raise ProductionStopped("台词归属含未知人物")'''
NEW_KNOWN = """        # 归一：模型可能写"陈父"而 cast 键是"陈父（陈远舟）"——去掉末尾括号
        # 就能确定性匹配。不归一直接判死会把可修的问题变成不可逆的整章消失。
        cast = production["cast"]
        for event in result.events:
            event.known_by = [
                _canonical_persona(name, cast) or name for name in event.known_by
            ]
            event.dialogue_by_character = {
                (_canonical_persona(name, cast) or name): lines
                for name, lines in event.dialogue_by_character.items()
            }
        unknown_known = sorted(
            {
                name
                for event in result.events
                for name in event.known_by
                if name not in cast
            }
        )
        if unknown_known:
            raise ProductionStopped(
                "结算事件含未知的知情人物：" + "、".join(unknown_known)
            )
        unknown_dialogue = sorted(
            {
                name
                for event in result.events
                for name in event.dialogue_by_character
                if name not in cast
            }
        )
        if unknown_dialogue:
            raise ProductionStopped(
                "台词归属含未知人物：" + "、".join(unknown_dialogue)
            )"""

EDITS = [
    ("known_by", OLD_KNOWN, NEW_KNOWN),
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
