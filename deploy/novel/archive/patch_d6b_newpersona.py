"""缺陷 6b：角色表不应是封闭集合——导演可声明新人物，系统负责登记。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/direction.py"

EDITS: list[tuple[str, str, str]] = []


def sub(label: str, old: str, new: str) -> None:
    EDITS.append((label, old, new))


# ------------------------------------------------------------------ 1. import uuid
sub(
    "import uuid",
    "import hashlib\nimport json\nimport re\n",
    "import hashlib\nimport json\nimport re\nimport uuid\n",
)

# ------------------------------------------------------- 2. 每章新增人物上限常量
sub(
    "MAX_NEW_PERSONAS_PER_CHAPTER",
    '# 末尾括号注释，模型给角色名加注的习惯写法；只用于**确定性**归一，不做模糊匹配。\n'
    '_TRAILING_PAREN = re.compile(r"[（(][^（()）]*[)）]\\s*$")\n',
    '# 末尾括号注释，模型给角色名加注的习惯写法；只用于**确定性**归一，不做模糊匹配。\n'
    '_TRAILING_PAREN = re.compile(r"[（(][^（()）]*[)）]\\s*$")\n'
    "# 每章可新增的人物数。角色表是**起点不是牢笼**：一本小说不可能只有开局那几个\n"
    "# 人，导演当然要能按叙事需要带新人进场。上限卡的不是「能不能造人」，而是\n"
    "# 「一次造多少」——角色表会被带进后续每一章的上下文和每个角色的信息集，\n"
    "# 无上限等于让一次随手声明永久抬高后续所有章节的成本。\n"
    "MAX_NEW_PERSONAS_PER_CHAPTER = 2\n",
)

# ---------------------------------------------------------- 3. NewPersonaSpec 模型
NEW_PERSONA_BLOCK = '''class NewPersonaSpec(BaseModel):
    """导演申请进场的新人物。

    角色图谱是**起点不是牢笼**：一本小说不可能只有开局那几个人。但新人必须
    **声明**，不能偷偷把新名字写进 ``persona``——人物名下游是当身份用的
    （信息隔离、声纹、正典），只躺在 JSON 里的名字会在更后面炸，而且是那种
    查不出原因的炸。声纹必填：没有声纹的新角色等于没有角色，而声纹分离正是
    多角色独立表演的全部价值所在。
    """

    name: str = Field(min_length=1, max_length=120)
    voice: str = Field(min_length=1, description="说话方式：句式、用词、语气；这是该人物的声纹")
    identity: str = Field(default="", description="一句话身份，如「码头搬工」")
    drives: str = Field(default="", description="他此刻想要什么")
    reason: str = Field(min_length=1, description="为什么现有角色撑不起这场戏")


class ChapterDirection(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    reader_intent: str = Field(min_length=1)
    ending_reason: str = Field(min_length=1)
    scenes: list[SceneBrief] = Field(min_length=1, max_length=4)
    # 声明数量**不在 schema 上设 max_length**：超了要能被自修反馈纠正，而不是
    # 变成一次读不懂的校验失败。
    new_personas: list[NewPersonaSpec] = Field(default_factory=list)
'''

sub(
    "NewPersonaSpec",
    "class ChapterDirection(BaseModel):\n"
    "    title: str = Field(min_length=1, max_length=200)\n"
    "    reader_intent: str = Field(min_length=1)\n"
    "    ending_reason: str = Field(min_length=1)\n"
    "    scenes: list[SceneBrief] = Field(min_length=1, max_length=4)\n",
    NEW_PERSONA_BLOCK,
)

# ------------------------------------------------------- 4. _declared_cast 辅助函数
sub(
    "_declared_cast",
    "def _check_brief(brief: SceneBrief, cast: dict[str, Any]) -> None:\n",
    "def _declared_cast(\n"
    "    direction: ChapterDirection, cast: dict[str, Any]\n"
    ") -> tuple[dict[str, Any], list[str]]:\n"
    '    """把导演声明的新人物并入候选角色表，返回（候选表，声明本身的问题）。\n'
    "\n"
    "    声明过的新名字是合法的引用——这一点是刻意的：角色表不是封闭集合。但声明\n"
    "    本身要受约束（不得顶替既有角色、不得自我重复、不得超量），否则「新增人物」\n"
    "    会变成绕过角色图谱的暗门。\n"
    '    """\n'
    "    if len(direction.new_personas) > MAX_NEW_PERSONAS_PER_CHAPTER:\n"
    "        return cast, [\n"
    '            f"一章最多新增 {MAX_NEW_PERSONAS_PER_CHAPTER} 个人物，"\n'
    '            f"本次声明了 {len(direction.new_personas)} 个"\n'
    "        ]\n"
    "    issues: list[str] = []\n"
    "    merged = dict(cast)\n"
    "    seen: set[str] = set()\n"
    "    for spec in direction.new_personas:\n"
    "        if spec.name in cast:\n"
    '            issues.append(f"「{spec.name}」已是既有角色，不能重复声明")\n'
    "        elif spec.name in seen:\n"
    '            issues.append(f"「{spec.name}」被重复声明")\n'
    "        else:\n"
    "            seen.add(spec.name)\n"
    '            merged[spec.name] = {\n'
    '                "identity": {"role": spec.identity},\n'
    '                "drives": {"primary": spec.drives},\n'
    '                "voice": {"style": spec.voice},\n'
    "            }\n"
    "    return merged, issues\n"
    "\n"
    "\n"
    "async def _register_new_personas(\n"
    "    session: AsyncSession,\n"
    "    work: StoryWorkModel,\n"
    "    cast: dict[str, Any],\n"
    "    direction: ChapterDirection,\n"
    ") -> dict[str, Any]:\n"
    '    """登记导演声明且**实际出场**的新人物，返回并入后的角色表。\n'
    "\n"
    "    只登记出场的那几个：声明了没用上的不带入角色表——它会被带进后续每一章\n"
    "    的上下文和每个角色的信息集，一次随手声明不该永久抬高后续成本。\n"
    '    """\n'
    "    used = {actor.persona for scene in direction.scenes for actor in scene.actors}\n"
    "    merged = dict(cast)\n"
    "    added: list[PersonaSpecModel] = []\n"
    "    for spec in direction.new_personas:\n"
    "        if spec.name not in used or spec.name in merged:\n"
    "            continue\n"
    "        entry = {\n"
    '            "identity": {"role": spec.identity},\n'
    '            "drives": {"primary": spec.drives},\n'
    '            "voice": {"style": spec.voice},\n'
    "        }\n"
    "        merged[spec.name] = entry\n"
    "        added.append(\n"
    "            PersonaSpecModel(\n"
    "                id=uuid.uuid4(),\n"
    "                work_id=work.id,\n"
    "                name=spec.name,\n"
    "                stable_traits=[spec.drives, spec.voice],\n"
    "                **entry,\n"
    "            )\n"
    "        )\n"
    "    if added:\n"
    "        session.add_all(added)\n"
    "        await session.flush()\n"
    "    return merged\n"
    "\n"
    "\n"
    "def _check_brief(brief: SceneBrief, cast: dict[str, Any]) -> None:\n",
)

# --------------------------------------------------------------- 5. 提示词：允许造人
sub(
    "plan prompt new personas",
    '        "不得添加括号、头衔、别称或本场说明，也不得新造人物；"\n'
    '        "人物在本场的作用写在actors[].role。"\n'
    "    )\n",
    '        "不得添加括号、头衔、别称或本场说明；"\n'
    '        "人物在本场的作用写在actors[].role。"\n'
    '        "现有角色撑不起这场戏时，可以在new_personas里声明新人物并写清声纹voice；"\n'
    '        f"一章最多新增{MAX_NEW_PERSONAS_PER_CHAPTER}个。"\n'
    '        "persona要么是既有角色的确切名字，要么是本次在new_personas里声明的名字。"\n'
    "    )\n",
)

# ---------------------------------------------------- 6. 循环：并入声明 + 反馈改写
sub(
    "plan loop declared cast",
    "        issues: list[str] = []\n"
    "        for brief in result.scenes:\n"
    "            issues += _brief_issues(brief, cast)\n"
    "        if not issues:\n"
    "            break\n"
    "        # 反馈必须具体：说清哪个名字不能用、能用的是哪些，而不是再说一遍规则。\n"
    "        repair = [\n"
    '            "上一版规划的角色引用无法使用：" + "；".join(issues) + "。",\n'
    '            "角色表里的确切名字只有：" + "、".join(sorted(cast)) + "。"\n'
    '            "actors[].persona 必须一字不差地取用这些名字，不得添括号、头衔或本场说明；"\n'
    '            "本场作用写在 actors[].role。缺人就在这几个角色里选，不得新造人物。",\n'
    "        ]\n"
    "    else:\n"
    '        raise ProductionStopped("角色引用自修次数已用尽：" + "；".join(issues))\n',
    "        # 先并入导演声明的新人物再校验：声明过的新名字是合法引用。\n"
    "        candidate, declare_issues = _declared_cast(result, cast)\n"
    "        issues: list[str] = list(declare_issues)\n"
    "        for brief in result.scenes:\n"
    "            issues += _brief_issues(brief, candidate)\n"
    "        if not issues:\n"
    "            break\n"
    "        # 反馈必须具体：说清哪个名字不能用、能用的是哪些，而不是再说一遍规则。\n"
    "        repair = [\n"
    '            "上一版规划的角色引用无法使用：" + "；".join(issues) + "。",\n'
    '            "既有角色的确切名字是：" + "、".join(sorted(cast)) + "。"\n'
    '            "actors[].persona 必须一字不差地取用既有名字，或取本次在 new_personas "\n'
    '            "里声明过的名字；不得添括号、头衔或本场说明，本场作用写在 actors[].role。"\n'
    '            f"需要新人物就在 new_personas 里声明并写清声纹，一章最多新增"\n'
    '            f"{MAX_NEW_PERSONAS_PER_CHAPTER} 个。",\n'
    "        ]\n"
    "    else:\n"
    '        raise ProductionStopped("角色引用自修次数已用尽：" + "；".join(issues))\n'
    "    cast = await _register_new_personas(session, work, cast, result)\n",
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
