"""一次性补丁：P1-1 命令绑定校验与 PLANNING/ASSEMBLE/FINISH 接入 Runtime。"""
from __future__ import annotations

import pathlib

rt = pathlib.Path("core/src/regent/novel/application/runtime.py")
text = rt.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"runtime.py count={text.count(old)} for {old[:60]!r}"
    text = text.replace(old, new)


# 1) RuntimeState 增加绑定与产物字段
sub(
    """    cast: frozenset[str] = frozenset()
    has_rule_issues: bool = False
    has_hard_failure: bool = False
    has_visible_events: bool = False
""",
    """    cast: frozenset[str] = frozenset()
    has_rule_issues: bool = False
    has_hard_failure: bool = False
    has_visible_events: bool = False
    # scene/take 绑定：命令必须作用在它自己被提出的那一场、那一次 take 上
    scene_index: int = 0
    take_no: int = 1
    # 产物绑定：接受/成文必须基于已存在的稿件，不能凭空通过
    has_prose: bool = False
""",
)

# 2) validate 中校验绑定与产物
sub(
    """        unknown = personas_in(command.payload) - state.cast
        if unknown:
            raise reject(f"命令涉及未定义的人物：{sorted(unknown)}")
""",
    """        unknown = personas_in(command.payload) - state.cast
        if unknown:
            raise reject(f"命令涉及未定义的人物：{sorted(unknown)}")

        # 绑定校验：命令不能漂移到别的场景或别的 take 上（P1-1）
        if command.scene_index != state.scene_index:
            raise reject(
                f"命令场景绑定不符：{command.scene_index} != {state.scene_index}"
            )
        if command.take_no != state.take_no:
            raise reject(f"命令 take 绑定不符：{command.take_no} != {state.take_no}")
        if kind is CommandKind.ACCEPT_SCENE and not state.has_prose:
            raise reject("没有稿件可以接受")
        if kind is CommandKind.ASSEMBLE_CHAPTER and not state.has_prose:
            raise reject("没有场景正文可以组章")
""",
)

rt.write_text(text, encoding="utf-8")
print("patched", rt)

# ---------------------------------------------------------------------------
# direction.py
# ---------------------------------------------------------------------------
dr = pathlib.Path("core/src/regent/novel/application/direction.py")
text = dr.read_text(encoding="utf-8")


def sub2(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"direction.py count={text.count(old)} for {old[:60]!r}"
    text = text.replace(old, new)


# 3) _runtime_state 带上绑定与产物
sub2(
    """        has_visible_events=any(e.get("reader_visible") for e in take["events"]),
    )""",
    """        has_visible_events=any(e.get("reader_visible") for e in take["events"]),
        scene_index=int(production.get("scene_index", 0) or 0),
        take_no=int(take.get("take_no", 1) or 1),
        has_prose=bool(take.get("content")),
    )""",
)

# 4) manifest 完整留痕：binding 与 fingerprint 一起存
sub2(
    """    take.setdefault("manifests", []).append(
        {
            "audience": compiled.audience,
            "persona": compiled.persona,
            "manifest_hash": compiled.manifest_hash,
            "projection_hash": compiled.manifest.projection_hash,
            "sources": [item.model_dump(mode="json") for item in compiled.manifest.sources],
        }
    )""",
    """    take.setdefault("manifests", []).append(
        {
            "audience": compiled.audience,
            "persona": compiled.persona,
            "manifest_hash": compiled.manifest_hash,
            "manifest_version": compiled.manifest.version,
            # 完整绑定：只存 hash 无法回答"这次装配读的是哪一场的哪些材料"
            "binding": dict(compiled.manifest.binding),
            "projection_hash": compiled.manifest.projection_hash,
            "fingerprint": compiled.manifest.fingerprint(),
            "sources": [item.model_dump(mode="json") for item in compiled.manifest.sources],
        }
    )""",
)

# 5) plan_chapter 走 PLAN_SCENE 命令校验
sub2(
    """    result = await _call(
        session,
        provider,
        work,
        run,
        production,
        ChapterDirection,""",
    """    # 规划也是一条命令：没有场景时只允许 PLAN_SCENE（P1-1）
    _RUNTIME.validate(
        director_command(
            CommandKind.PLAN_SCENE,
            command_id=f"v{_input_version(run)}:plan",
            input_version=_input_version(run),
            scene_index=int(production.get("scene_index", 0) or 0),
            take_no=1,
            payload={"cast": sorted(cast)},
        ),
        RuntimeState(scene_state=NO_SCENE, input_version=_input_version(run)),
    )
    result = await _call(
        session,
        provider,
        work,
        run,
        production,
        ChapterDirection,""",
)

# 6) 组章走 ASSEMBLE_CHAPTER
sub2(
    """            if production["scene_index"] == len(production["plan"]["scenes"]):
                run.content = "\\n\\n".join(
                    production["takes"][i]["content"] for i in production["accepted"]
                )""",
    """            if production["scene_index"] == len(production["plan"]["scenes"]):
                # 组章是一条命令：必须在最后一场已接受、且每场都有稿件时才允许
                _RUNTIME.validate(
                    director_command(
                        CommandKind.ASSEMBLE_CHAPTER,
                        command_id=_command_id(production, run, "assemble"),
                        input_version=_input_version(run),
                        scene_index=production["scene_index"],
                        take_no=take["take_no"],
                        evidence=[t["content"][:200] for t in production["takes"] if t["content"]],
                    ),
                    _runtime_state(production, run, take),
                )
                take["assembled"] = True
                run.content = "\\n\\n".join(
                    production["takes"][i]["content"] for i in production["accepted"]
                )""",
)

# 7) 整章通过走 FINISH_CHAPTER
sub2(
    """    facts = [
        fact
        for i in production["accepted"]
        for fact in production["takes"][i]["validation"]["facts"]
    ]
    run.generation_context = {""",
    """    # 完成也是一条命令：只在整章通过独立审校后才允许（P1-1）
    _RUNTIME.validate(
        director_command(
            CommandKind.FINISH_CHAPTER,
            command_id=f"v{_input_version(run)}:finish",
            input_version=_input_version(run),
            scene_index=production["scene_index"],
            take_no=production["takes"][-1]["take_no"],
            evidence=[run.content[:200]],
        ),
        RuntimeState(
            scene_state=SceneRunState.ACCEPTED.value,
            input_version=_input_version(run),
            scene_index=production["scene_index"],
            take_no=production["takes"][-1]["take_no"],
            has_prose=bool(run.content),
        ),
    )
    facts = [
        fact
        for i in production["accepted"]
        for fact in production["takes"][i]["validation"]["facts"]
    ]
    run.generation_context = {""",
)

# 8) 导入 NO_SCENE
sub2(
    """from regent.novel.application.runtime import (
    CommandRuntime,
    RuntimeLimits,
    RuntimeState,
)""",
    """from regent.novel.application.runtime import (
    NO_SCENE,
    CommandRuntime,
    RuntimeLimits,
    RuntimeState,
)""",
)

dr.write_text(text, encoding="utf-8")
print("patched", dr)
