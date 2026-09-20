"""D-11b：把 coerce 与 command_check 拆成两个闭包。

``patch_d11`` 把两件事塞进同一个闭包，导致 _grounded_judgment 里 coerce 跑完
command_check 再跑一遍（coercion 执行两次，留痕被 clear 两次），且 WATCH_TAKE
的 coerce= 拿到的是遗留名 ``_check_take_wrapper``（未定义 → NameError）。

按 _grounded_judgment 的契约拆开：coerce 只做就地替换，command_check 只做校验。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/direction.py"

OLD_TAKE_DEF = '''        def _check_take(result: Any) -> None:
            coerced.clear()
            coerced.extend(_coerce_illegal_action(phase, result, take))
            checked["state"], checked["target"] = _validate_action(
                production, run, take, phase, result, _TAKE_COMMANDS, brief["actors"]
            )
'''
NEW_TAKE_DEF = '''        def _coerce_take(result: Any) -> None:
            coerced.clear()
            coerced.extend(_coerce_illegal_action(phase, result, take))

        def _check_take(result: Any) -> None:
            checked["state"], checked["target"] = _validate_action(
                production, run, take, phase, result, _TAKE_COMMANDS, brief["actors"]
            )
'''

OLD_TAKE_CALL = '''            coerce=_check_take_wrapper,
            command_check=_check_take,
'''
NEW_TAKE_CALL = '''            coerce=_coerce_take,
            command_check=_check_take,
'''

OLD_PROSE_DEF = '''        def _check_prose(result: Any) -> None:
            coerced.clear()
            coerced.extend(_coerce_illegal_action(phase, result, take))
            checked["state"], checked["target"] = _validate_action(
                production, run, take, phase, result, _PROSE_COMMANDS, brief["actors"]
            )
'''
NEW_PROSE_DEF = '''        def _coerce_prose(result: Any) -> None:
            coerced.clear()
            coerced.extend(_coerce_illegal_action(phase, result, take))

        def _check_prose(result: Any) -> None:
            checked["state"], checked["target"] = _validate_action(
                production, run, take, phase, result, _PROSE_COMMANDS, brief["actors"]
            )
'''

OLD_PROSE_CALL = '''            coerce=_check_prose,
            command_check=_check_prose,
'''
NEW_PROSE_CALL = '''            coerce=_coerce_prose,
            command_check=_check_prose,
'''

EDITS = [
    ("take.def", OLD_TAKE_DEF, NEW_TAKE_DEF),
    ("take.call", OLD_TAKE_CALL, NEW_TAKE_CALL),
    ("prose.def", OLD_PROSE_DEF, NEW_PROSE_DEF),
    ("prose.call", OLD_PROSE_CALL, NEW_PROSE_CALL),
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
