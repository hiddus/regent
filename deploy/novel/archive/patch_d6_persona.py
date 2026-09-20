"""缺陷 6：导演角色引用不合规导致整章判死。一次性读改写，锚点唯一性断言。"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/direction.py"

EDITS: list[tuple[str, str, str]] = []


def sub(label: str, old: str, new: str) -> None:
    EDITS.append((label, old, new))


# ---------------------------------------------------------------- 1. import re
sub(
    "import re",
    "import hashlib\nimport json\n",
    "import hashlib\nimport json\nimport re\n",
)

# ------------------------------------------------- 2. ActorDirection persona/role
sub(
    "ActorDirection",
    '''class ActorDirection(BaseModel):
    persona: str
    objective: str = Field(min_length=1)''',
    '''class ActorDirection(BaseModel):
    # ``persona`` 是角色表的**键**，不是自由文本。原先它是个没有 description 的裸
    # ``str``，模型于是把「陈渡（记忆观察者）」这种「名字 + 本场作用」写进来，下游
    # 拿它当身份查人，一查就空，整章判死。与其在提示词里反复喊「不要加括号」，
    # 不如给「本场作用」一个正当去处——模型想表达的东西要有地方写。
    persona: str = Field(
        min_length=1,
        description="必须是给定角色表里的确切名字，一字不差；"
        "不得添加括号、头衔、别称或本场说明，也不得新造人物",
    )
    role: str = Field(default="", description="该人物在本场的作用，如「旁观者」；不要写进 persona")
    objective: str = Field(min_length=1)''',
)

# ------------------------------------------------------ 3. SceneEvent 字段说明
sub(
    "SceneEvent",
    '''class SceneEvent(BaseModel):
    statement: str = Field(min_length=1)
    known_by: list[str] = Field(default_factory=list)
    reader_visible: bool
    state_changes: dict[str, str] = Field(default_factory=dict)
    dialogue_by_character: dict[str, list[str]] = Field(default_factory=dict)''',
    '''class SceneEvent(BaseModel):
    statement: str = Field(min_length=1)
    known_by: list[str] = Field(
        default_factory=list,
        description="实际获知者的角色名，必须取自给定角色表且一字不差；缺席人物不能自动获知",
    )
    reader_visible: bool
    state_changes: dict[str, str] = Field(default_factory=dict)
    dialogue_by_character: dict[str, list[str]] = Field(
        default_factory=dict, description="键为角色表中的确切名字，值为该人物实际说出的台词"
    )''',
)

# -------------------------------------------------------- 4. 自修预算常量
sub(
    "MAX_PLAN_REPAIRS",
    "MAX_CHAPTER_REPAIRS = 1\n",
    "MAX_CHAPTER_REPAIRS = 1\n"
    "# 角色引用自修次数。之所以需要「带反馈的自修」而不是交给步骤级重试：规划用\n"
    "# temperature=0，入参不变的重试会以近乎确定的方式产出同一个坏名字，重试等于\n"
    "# 白烧钱。自修必须改入参——把违规原因和在册名单回传给导演。\n"
    "MAX_PLAN_REPAIRS = 2\n"
    "# 末尾括号注释，模型给角色名加注的习惯写法；只用于**确定性**归一，不做模糊匹配。\n"
    "_TRAILING_PAREN = re.compile(r\"[（(][^（()）]*[)）]\\s*$\")\n",
)

# ---------------------------------------------- 5. 归一 + 问题收集 + _check_brief
sub(
    "_check_brief",
    '''def _check_brief(brief: SceneBrief, cast: dict[str, Any]) -> None:
    names = [actor.persona for actor in brief.actors]
    if len(names) != len(set(names)) or any(name not in cast for name in names):
        raise ProductionStopped("场景包含重复或未定义的角色")''',
    '''def _canonical_persona(name: str, cast: dict[str, Any]) -> str | None:
    """把角色引用归一到 ``cast`` 的键；归一不了返回 None。

    只在两种**确定性**情形下归一：① 原样就是键（角色表里本来就带括号的名字，
    如「陈默父亲（陈远舟）」，必须原样保留）；② 去掉末尾整段括号后正好是键。
    不做模糊匹配：猜错等于把一个人的戏记到另一个人头上，而这是不可逆的。
    """
    if name in cast:
        return name
    stripped = _TRAILING_PAREN.sub("", name).strip()
    if stripped and stripped in cast:
        return stripped
    return None


def _brief_issues(brief: SceneBrief, cast: dict[str, Any]) -> list[str]:
    """返回本场角色引用的问题（人类可读），无问题返回空表。

    副作用：把可归一的引用**就地**改写为 ``cast`` 的键。模型写「陈渡（记忆
    观察者）」时意图明确，为此重做整章规划毫无意义；归一即可。
    """
    issues: list[str] = []
    resolved_names: list[str] = []
    for actor in brief.actors:
        resolved = _canonical_persona(actor.persona, cast)
        if resolved is None:
            issues.append(f"「{actor.persona}」不是角色表中的名字")
            continue
        if resolved in resolved_names:
            issues.append(f"「{actor.persona}」与本场其他角色指向同一个人")
            continue
        resolved_names.append(resolved)
        actor.persona = resolved
    return issues


def _check_brief(brief: SceneBrief, cast: dict[str, Any]) -> None:
    """校验角色引用；违规即判死（调用方负责在判死前给过自修机会）。"""
    issues = _brief_issues(brief, cast)
    if issues:
        raise ProductionStopped("场景包含重复或未定义的角色：" + "；".join(issues))''',
)

# --------------------------------------------------------------- 6. 自修循环
# 提示词收紧（新增 persona 约束句）合并在本处的新文本里，不单独再改一次：
# 两处改的是同一段字符串，分成两步会让后一个锚点永远匹配不上。
sub(
    "plan call loop",
    '''    result = await _call(
        session,
        provider,
        work,
        run,
        production,
        ChapterDirection,
        "你是持续负责小说创作的导演。先设计本章阅读体验，再安排1至4个必要场景。"
        "每场明确人物欲望冲突、表演指导、视角、信息差和结束理由。允许舒缓和关系戏，"
        "不要机械升级冲突或套用三章结构。人物合理的选择可以改变未锁定计划。"
        "只使用给定角色；角色指令不泄露本人未知秘密，不预写结果和台词。"
        "不擅自决定用户锁定的重大节点。正文总目标1800至2500字。",
        {
            # D-03：导演计划请求拿**导演视图**的长期记忆（含未兑现承诺与导演
            # 笔记），不再把召回的全量 memory 原样塞进请求——那是 C-03 投影
            # 纪律，六步旧流程早已如此，director_v2 不得成为例外。
            "context": {
                k: v
                for k, v in run.generation_context.items()
                if k not in ("production", "memory")
            },
            "cast": cast,
            "user_guidance": run.user_guidance or {},
            "director_memory": _memory_view(
                run.generation_context.get("memory", []), "director"
            ),
        },
        "plan",
        f"v{_input_version(run)}:plan",
    )
    for brief in result.scenes:
        _check_brief(brief, cast)
''',
    '''    system_prompt = (
        "你是持续负责小说创作的导演。先设计本章阅读体验，再安排1至4个必要场景。"
        "每场明确人物欲望冲突、表演指导、视角、信息差和结束理由。允许舒缓和关系戏，"
        "不要机械升级冲突或套用三章结构。人物合理的选择可以改变未锁定计划。"
        "只使用给定角色；角色指令不泄露本人未知秘密，不预写结果和台词。"
        "不擅自决定用户锁定的重大节点。正文总目标1800至2500字。"
        "scenes[].actors[].persona必须是给定角色表里的确切名字，一字不差，"
        "不得添加括号、头衔、别称或本场说明，也不得新造人物；"
        "人物在本场的作用写在actors[].role。"
    )
    plan_command = f"v{_input_version(run)}:plan"
    repair: list[str] = []
    for repair_no in range(MAX_PLAN_REPAIRS + 1):
        payload: dict[str, Any] = {
            # D-03：导演计划请求拿**导演视图**的长期记忆（含未兑现承诺与导演
            # 笔记），不再把召回的全量 memory 原样塞进请求——那是 C-03 投影
            # 纪律，六步旧流程早已如此，director_v2 不得成为例外。
            "context": {
                k: v
                for k, v in run.generation_context.items()
                if k not in ("production", "memory")
            },
            "cast": cast,
            "user_guidance": run.user_guidance or {},
            "director_memory": _memory_view(
                run.generation_context.get("memory", []), "director"
            ),
        }
        if repair:
            payload["repair_instructions"] = repair
        result = await _call(
            session,
            provider,
            work,
            run,
            production,
            ChapterDirection,
            system_prompt,
            payload,
            "plan",
            # 自修是**新的逻辑调用**：沿用原 command_id 会被幂等键挡住（或更糟，
            # 复用上一次的坏结果），所以带序号另起一条。
            plan_command if not repair_no else f"{plan_command}:r{repair_no}",
        )
        issues: list[str] = []
        for brief in result.scenes:
            issues += _brief_issues(brief, cast)
        if not issues:
            break
        # 反馈必须具体：说清哪个名字不能用、能用的是哪些，而不是再说一遍规则。
        repair = [
            "上一版规划的角色引用无法使用：" + "；".join(issues) + "。",
            "角色表里的确切名字只有：" + "、".join(sorted(cast)) + "。"
            "actors[].persona 必须一字不差地取用这些名字，不得添括号、头衔或本场说明；"
            "本场作用写在 actors[].role。缺人就在这几个角色里选，不得新造人物。",
        ]
    else:
        raise ProductionStopped("角色引用自修次数已用尽：" + "；".join(issues))
''',
)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    for label, old, new in EDITS:
        count = text.count(old)
        if count != 1:
            print(f"[FAIL] 锚点 {label} 命中 {count} 次（期望 1）")
            return 1
        text = text.replace(old, new, 1)
    # 表格/语法校验：改动后必须仍是合法 Python
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
