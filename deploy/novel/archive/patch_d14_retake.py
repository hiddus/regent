"""D-14：RETAKE 给出与现行 brief 一致的 ``revised_brief`` 时由 Runtime 拒掉——走
自修通道而不是直接判死。

真机死法（run17）：6 次 WATCH_TAKE——前 5 次 CONTINUE 全过；第 6 次 RETAKE，
给了 ``revised_brief``，但 ``brief == take["brief"]``，被
``_retake`` 直接判 ``ProductionStopped("重演未改变场景指令")``，整章消失。
这个检查在 ``produce_tick`` 里、不在 ``_grounded_judgment`` 的 ``command_check``
通道里——所以命令自修接不住。

修法：把同一逻辑**提到 ``_validate_action``**（它已经被 ``_grounded_judgment``
串起来），再让 ``_retake`` 的旧检查变成不可达。模型能看到反馈——"和现行 brief
相同的字段是：setting、conflict"——就能改对，不用猜。

R-4：归并到同一条校验路径上，而不是开第二个。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/direction.py"

# 1. _validate_action 在 Runtime 校验通过后再加 brief 相等检查
OLD_VALIDATE = """    state = _runtime_state(production, run, take)
    target = _RUNTIME.validate(
        director_command(
            commands[result.action],
            command_id=_command_id(production, run, phase),
            input_version=_input_version(run),
            scene_index=production["scene_index"],
            take_no=take["take_no"],
            evidence=result.evidence,
            actors=actors,
            revised_brief=(
                result.revised_brief.model_dump(mode="json")
                if result.revised_brief is not None
                else None
            ),
        ),
        state,
    )
    return state, target
"""
NEW_VALIDATE = """    state = _runtime_state(production, run, take)
    command = director_command(
        commands[result.action],
        command_id=_command_id(production, run, phase),
        input_version=_input_version(run),
        scene_index=production["scene_index"],
        take_no=take["take_no"],
        evidence=result.evidence,
        actors=actors,
        revised_brief=(
            result.revised_brief.model_dump(mode="json")
            if result.revised_brief is not None
            else None
        ),
    )
    target = _RUNTIME.validate(command, state)
    # RETAKE 必须给出**确实修改**的场景指令，否则等于重演白演。真机死法：
    # 给出 revised_brief 但字段全和现行 brief 一样，被 _retake 直接判死——而
    # 那条检查不在自修通道里，模型拿不到反馈。提到这里走同一条 command_check
    # 通道，模型能看到"哪些字段没改"。
    if (
        commands[result.action] is CommandKind.RETAKE_SCENE
        and result.revised_brief is not None
        and take.get("brief") is not None
    ):
        proposed = command.revised_brief or {}
        current = take["brief"]
        same = sorted(
            k for k in proposed if isinstance(proposed.get(k), (str, list, dict))
            and proposed.get(k) == current.get(k)
        )
        if len(same) == len(proposed):
            # 全部字段都等于现行 brief——"未改变"才成立；只改了一项就不算。
            from regent.novel.domain.errors import CommandRejected
            raise CommandRejected(
                str(commands[result.action].value),
                "重演未改变场景指令；与现行 brief 相同的字段："
                + "、".join(same),
                command.fingerprint(),
            )
    return state, target
"""

# 2. _retake 里的旧检查变成不可达（前面的 validate 一定会先抓住）
OLD_RETAKE = """def _retake(production: dict[str, Any], take: dict[str, Any], decision: Any) -> None:
    if decision.revised_brief is None:
        raise ProductionStopped("重演必须提供修改后的场景指令")
    _check_brief(decision.revised_brief, production["cast"])
    brief = decision.revised_brief.model_dump(mode="json")
    if brief == take["brief"]:
        raise ProductionStopped("重演未改变场景指令")
    # Rejected events never modify working_state or subsequent actor knowledge.
    _new_take(production, brief)
    take["status"] = "REJECTED"
"""
NEW_RETAKE = """def _retake(production: dict[str, Any], take: dict[str, Any], decision: Any) -> None:
    if decision.revised_brief is None:
        raise ProductionStopped("重演必须提供修改后的场景指令")
    _check_brief(decision.revised_brief, production["cast"])
    # brief 是否与现行 brief 完全相同，已在 _validate_action 走命令校验通道。
    # 走到这里说明至少有字段被改过：直接落新 brief。
    brief = decision.revised_brief.model_dump(mode="json")
    # Rejected events never modify working_state or subsequent actor knowledge.
    _new_take(production, brief)
    take["status"] = "REJECTED"
"""

EDITS = [
    ("validate", OLD_VALIDATE, NEW_VALIDATE),
    ("retake", OLD_RETAKE, NEW_RETAKE),
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
