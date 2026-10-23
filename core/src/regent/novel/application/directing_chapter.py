"""一次成章：写作、确定性硬门、最多一次改稿。

不选题、不分场、不让第二个模型重讲这一章。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.application.directing_calls import _call, _input_version, _save
from regent.novel.application.directing_types import H2_ARCHITECTURE, PROTOCOL_CHAPTER
from regent.novel.domain.errors import ProductionStopped
from regent.novel.domain.prose_copy import is_legitimate_repetition, longest_common_substring
from regent.novel.domain.prose_front_gates import front_gate_hard_fails, front_gate_writer_hint
from regent.novel.infrastructure.models import PersonaSpecModel, StoryWorkModel

CHAPTER_PHASES = ("PLAN", "METHOD", "WRITE", "FIT", "CHECK", "REPAIR", "DONE")
# Harness² 递归臂在 CHECK 失败或追读自评报问题后插入的阶段。
H2_PHASES = ("PROPOSE", "PROBE", "COMPOSE", "SCREEN", "LAND")

# 网文单章通行下限。提示里要的上限只比下限多 500 字。
MIN_CHAPTER_CHARS = 2000
PROMPT_MAX_CHARS = MIN_CHAPTER_CHARS + 500
# 验收上限比提示宽 500 字：照提示写的初稿也常到三千字上下，按提示上限验收要删十几段，
# 删掉的铺垫让正文前后接不上。提示里的字数不跟着放宽，否则初稿会一起变长。
MAX_CHAPTER_CHARS = PROMPT_MAX_CHARS + 500
# 改稿、重写都补不到下限时，不低于这个字数的稿只差字数，原样入正史，不为字数停章。
SHORT_ACCEPT_MIN_CHARS = 1500

CHAPTER_PLAN_SYSTEM = (
    "你只出即将要写的这一章的章纲，不出后文，也不改全书节奏。\n"
    "章纲三行：压力是这个人眼前要面对的事；选择是他这一章动手做的那一件；"
    "结果是这个选择造成的，章末仍然成立。\n"
    "选择只写他做了什么，不要列他没做什么。结果只写这一章改变了什么，"
    "没有变的处境不要再复述一遍。三行各一百字以内。\n"
    "previous_result 是上一章已经发生的结果，第 1 章给的是开场局面。"
    "压力必须是这个结果造成的新处境：上一章做完之后，这个人现在卡在哪。"
    "不要抄 stake，也不要和 earlier_plans 里任何一章的压力、选择或结果相同。"
    "先在 used_methods 里归纳 earlier_plans 里主角用过的办法：换了说法、换了地点、"
    "但还是同一种手段或同一种借口，算同一种。选择不能是其中任何一种；"
    "老办法走不通时，让他付出更多或冒更大的险。\n"
    "current_stage 是这一卷眼下正在走的那一段，它还没有发生，不要写进压力。"
    "这一段一共写 stage_chapters 给出的章数范围，chapters_in_stage 是已经写了几章。"
    "选择朝它推进一步；结果让它成立时 reaches_stage 为 true。"
    "last_chapter_of_stage 为 true 时，这一章的结果要让它成立。"
    "不要越过它去写 later_stages 或卷末。\n"
    "place 写主角本人这一章待在哪里（不是别人在哪里），只写地点名本身，具体到房间、街道或舱段；"
    "还在 earlier_plans 里那个地方，就沿用那里的叫法。"
    "place_streak 是主角已经连续待在同一个地点的章数；到了 limit，这一章要让他离开那里，"
    "place 写新的地点。\n"
    "shows_rule 表示这一章是否让全书答应读者的那件核心结果当场发生。"
    "已经发生满 payoff_limit 次时，shows_rule 必须为 false。\n"
    "若给了 rejected_plan，那份章纲已因 reject_reason 被退回，写一份和它不同的。\n"
    "editor_notes 是编辑读完前几章后写下的意见，这一章照其中的 directives 做。"
    "payoff_due 为 true 时，这一章让全书答应读者的那件核心结果当场发生，shows_rule 为 true。"
    "advance_stage 为 true 时，这一章的结果让 current_stage 成立，reaches_stage 为 true。\n"
    "人名只用给定卡司。按这部作品已经定下的方向写，不要改成另一种类型。"
)

ARC_REVIEW_SYSTEM = (
    "你是这部书的追更读者兼责任编辑。只评最近几章的章纲，不改写它们。\n"
    "reader_contract 是这部书答应读者的东西，dramatic_engine 是它靠什么往前走，"
    "book_rhythm 是这一卷要走的路。拿这三样对照 recent_chapters。\n"
    "先替读者回答：读完这几章，还会不会往下追。keeps_reading 写下判断。\n"
    "再找出让读者想放下这本书的问题，最多三条。每条写清是哪几章、问题是什么、后面怎么改。"
    "只写真正影响追读的问题，没有就留空。\n"
    "previous_review 是上一轮的意见。先核对它的 directives 在这几章里做到没有，"
    "previous_followed 写下判断；没做到的，这一轮写得更具体。\n"
    "directives 是后面几章必须做到的事，最多三条，每条要具体到只看章纲就能核对，不写空话。\n"
    "答应读者的东西已经好几章没有当场发生，payoff_due 为 true。"
    "stage_remaining 写下眼下这一段成立之前还没写到的事，最多三件，每件是一件会当场发生的具体的事。"
    "还有没写到的事时 advance_stage 为 false，把最要紧的一件写进 directives，让后面的章把它写出来。"
    "这一段该写的事都写过了、已经写到 stage_chapters 的下限、局面还停在原地，advance_stage 为 true；"
    "没写到下限时，问题写进 directives，不要催这一段成立。"
    "payoff_chapters 是已经兑现过的章，看它离现在有多远再判断 payoff_due。"
)

METHOD_JUDGE_SYSTEM = (
    "你只判断这一章的章纲，不改写它。\n"
    "一、选择是不是 used_methods 里已经用过的同一种手段。"
    "换了说法、换了地点、换了对象，只要还是同一种手段或同一种借口，same_method 为 true。"
    "这一章比用过的办法付出明显更多，或者冒了之前没有冒过的险，same_method 为 false。"
    "used_methods 为空时 same_method 为 false。\n"
    "二、结果有没有越过 current_stage。later_stages 或 arc_end 里的事在结果里已经发生，"
    "或者它们要揭开的答案在结果里已经被说破，换了说法也算，skips_ahead 为 true。"
    "只是朝 current_stage 推进一步，或者让 current_stage 成立，skips_ahead 为 false。\n"
    "三、editor_notes 是编辑对这几章的要求。章纲明显违背其中一条，或者该做的一条也没做，"
    "ignores_notes 为 true；没给 editor_notes 时为 false。\n"
    "matched 写下重复的那一种手段；skipped 写下被提前写出的那件事；ignored 写下没做到的那条要求。"
    "没有就留空。"
)

# 执笔与改稿提示首尾各放一次：只写在中间的字数要求，模型常写到三千五以上。
CHAPTER_LENGTH_RULE = f"每章字数：{MIN_CHAPTER_CHARS + 1}~{PROMPT_MAX_CHARS - 1}字。"

CHAPTER_WRITE_SYSTEM = (
    CHAPTER_LENGTH_RULE + "\n"
    "你是小说执笔。按这部作品已经定下的方向写，不要改成另一种类型。\n"
    "这一章只做章纲里的那一个选择。先写压力，再写他动手。"
    "结果必须是这个选择造成的，而且章末仍然成立。\n"
    "这个结果只出现一次，出现了就停。不要换一个人把同一个结果再演一遍。\n"
    "上一章给你的只是结尾，那就是这一章的现场。从结尾之后往下写，不要把这段结尾整段再抄一遍。"
    "已经写成的身体、身份和去留保持原样；"
    "若这一章的选择就是把某件事改回来，正文要写出是这一手造成的。\n"
    "不要用角色讲课代替已经发生的动作，也不要用格言评价这件事。"
    "承接上一章已经发生的事，不重写上一章。earlier_results 里的事都已经发生过，"
    "不要让人物再当众说一遍或再做一遍。人名只用给定卡司。"
    "只输出正文。正文两千字以上，两千五百字以内，按两千二百字写："
    "结果落地就收尾，不在结果之后另加场景、回顾或感慨。"
    "超过两千五百字的部分会被整段删掉，删掉的地方接不上。"
)

CHAPTER_REPAIR_SYSTEM = (
    CHAPTER_LENGTH_RULE + "\n"
    "你在改同一章正文。不许新写感想，不许把已经发生的事再讲一遍，不许增加新情节。\n"
    "章纲里的选择和结果要留下，章末的处境不要改回改稿前的样子。\n"
    "正文必须落在两千字到两千五百字之间。length 是 previous_draft 现在的字数和改完后该落的区间："
    "只改 check_fails 指出的地方，其余句子照原样留下，不要顺手扩写。"
    "超过就删掉多余段落并接上；不足就把这一章没写完的那一个选择补到两千字，不另起一件事。\n"
    "只输出正文。\n" + CHAPTER_LENGTH_RULE
)

# 删段的目标落在上下限中间：模型挑的段落字数合计常有出入，留出余量。
FIT_TARGET_CHARS = MAX_CHAPTER_CHARS - 200
# 章末这么多字以内的片段不许删：章纲的结果就落在这里。
FIT_PROTECTED_TAIL_CHARS = 400
FIT_UNIT_MAX_CHARS = 300

CHAPTER_FIT_SYSTEM = (
    "你在给同一章正文删片段，把它删到字数上限以内，不是在改写这一章。\n"
    "units 是按顺序编号的正文片段，chars 是每段字数。挑出要整段删掉的编号写进 drop，"
    "最该删的排在最前；其余片段一字不改、按原顺序拼回去，删够 need_cut 就停。\n"
    "删掉的 chars 合计不少于 need_cut。优先删铺陈、重复的动作、解释性的句子，"
    "以及回忆里逐个展开的细节。\n"
    "不删 protected 里的片段；不删 must_keep 里的选择与结果落在的片段；"
    "不删后文还要用到的人或物第一次出现的片段；对话里一问一答要一起删或一起留；"
    "删完前后要接得上。\n"
    "只输出 drop。"
)

LANDING_SYSTEM = (
    "你只判断这一章正文有没有写到章纲的结果，不改写正文。\n"
    "choice 是主角这一章要做的那件事，planned_result 是它该造成的结果。"
    "结果的大意在正文里当场发生了就算写到，换了说法、细节不同也算；"
    "正文停在它之前、绕开了它或者走向了别处，算没写到。\n"
    "missing 用一句话写下没写到的那部分，写到了留空。"
    "ending 用一句话写下正文最后主角实际处在什么局面，只写正文写明的。"
)

CHAPTER_FACT_SYSTEM = (
    "从已完成章节提取后续必须保持一致的客观事实。"
    "每项使用 statement、quote、entities、known_by、confidence。"
    "quote 必须是正文里的连续原文，不得改写。正文没有写明的事不要提取。\n"
    "给了 planned_result 时，它是这一章计划写到的结果：result_landed 写下正文有没有写到它"
    "（换了说法、细节不同也算写到；正文停在它之前或走向了别处算没写到）；"
    "ending 用一句话写下正文最后主角实际处在什么局面，只写正文写明的。"
)


class ChapterPlan(BaseModel):
    used_methods: list[str] = Field(
        default_factory=list,
        description="earlier_plans 里主角已经用过的办法，同一种手段或同一种借口只算一项，每项一句",
    )
    pressure: str = Field(description="上一章结果造成的新处境，这个人现在卡在哪")
    choice: str = Field(description="他这一章动手做的那一件，不能是 used_methods 里的任何一种；不列没做的事")
    result: str = Field(description="这个选择造成的结果，章末仍然成立；只写这一章改变了什么")
    place: str = Field(default="", description="主角本人这一章待的地方，只写地点名本身，具体到房间、街道或舱段")
    shows_rule: bool = Field(default=False, description="这一章是否让全书答应读者的核心结果当场发生")
    reaches_stage: bool = Field(default=False, description="这一章的结果是否让 current_stage 成立")


# 主角连续停在同一个地点这么多章，下一章的章纲就要换地方；H2 臂取旋钮 stall_place_chapters。
STALL_PLACE_CHAPTERS = 4
# 没记地点的旧章：正文里这个地名出现这么多次，就算那一章还在那里。
PLACE_MENTIONS_IN_PROSE = 2

MAX_PLAN_ROUNDS = 3
# 一章最多出 4 份章纲、判 2 次同一招。用完还撞车就收下最后一份并记警告：
# 章纲重复是质量问题，不该让整本书停在这一章。
MAX_PLAN_CALLS = 4
MAX_METHOD_ROUNDS = 2
# 改稿后硬门仍不过，按同一份章纲重写一次正文，再给一次改稿。
MAX_REWRITES = 1
# 每写完这么多章，下一章动笔前先做一次追读自评。
ARC_REVIEW_EVERY = 3
ARC_REVIEW_WINDOW = 6


class MethodVerdict(BaseModel):
    same_method: bool = Field(description="这一章的选择是否是已经用过的同一种手段")
    matched: str = Field(default="", description="重复的那一种手段；不是同一种就留空")
    skips_ahead: bool = Field(default=False, description="结果是否已经写出了后面阶段或卷末的事")
    skipped: str = Field(default="", description="被提前写出的那件事；没有就留空")
    ignores_notes: bool = Field(default=False, description="章纲是否违背或没做到 editor_notes 里的要求")
    ignored: str = Field(default="", description="没做到的那条要求；没有就留空")


class ArcProblem(BaseModel):
    chapters: list[int] = Field(default_factory=list, description="问题出在哪几章")
    problem: str = Field(description="让读者想放下这本书的问题")
    fix: str = Field(description="后面怎么改")


class ArcReview(BaseModel):
    keeps_reading: bool = Field(description="读完这几章，读者还会不会往下追")
    previous_followed: bool = Field(default=True, description="上一轮的 directives 这几章做到没有")
    problems: list[ArcProblem] = Field(default_factory=list, max_length=3)
    directives: list[str] = Field(
        default_factory=list, max_length=3, description="后面几章必须做到的事，只看章纲就能核对"
    )
    payoff_due: bool = Field(default=False, description="答应读者的东西是否已经好几章没有当场发生")
    stage_remaining: list[str] = Field(
        default_factory=list, max_length=3, description="眼下这一段成立之前还没写到的事；都写过了就留空"
    )
    advance_stage: bool = Field(default=False, description="这一段该写的都写过、局面仍停在原地，下一章应让它成立")


class PlanRepeated(RuntimeError):
    """两次章纲都和前文撞车。不写正文，过一会儿换一轮重出。"""

    failure_code = "CHAPTER_PLAN_REPEATED"


class ChapterProse(BaseModel):
    # provider 把 Schema 拼在系统提示最末，模型看到的范围要和字数规则一致；
    # 校验仍放宽到 800–12000：超长交给 FIT 删段，拒收只会多一次整章重写。
    content: str = Field(
        min_length=800,
        max_length=12000,
        description=f"这一章的正文，{MIN_CHAPTER_CHARS + 1}~{PROMPT_MAX_CHARS - 1}字",
        json_schema_extra={"minLength": MIN_CHAPTER_CHARS + 1, "maxLength": PROMPT_MAX_CHARS - 1},
    )


class ChapterFitCut(BaseModel):
    drop: list[int] = Field(default_factory=list, max_length=200, description="要整段删掉的片段编号")


MAX_CHAPTER_FACTS = 40
LANDING_ENDING_MAX_CHARS = 200


class LandingVerdict(BaseModel):
    landed: bool = Field(description="正文有没有写到 planned_result")
    missing: str = Field(default="", description="没写到的那部分，一句话；写到了留空")
    ending: str = Field(default="", description="正文最后主角实际处在什么局面，一句话")


class ChapterFacts(BaseModel):
    facts: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_CHAPTER_FACTS)
    result_landed: bool = Field(default=True, description="正文是否写到了 planned_result；没给时为 true")
    ending: str = Field(default="", description="正文最后主角实际处在什么局面，一句话")

    @field_validator("facts", mode="before")
    @classmethod
    def _keep_first_facts(cls, value: Any) -> Any:
        # 多给的截掉，不整次拒收：拒收两次这一章就零事实入正史。
        return value[:MAX_CHAPTER_FACTS] if isinstance(value, list) else value


def chapter_node_for_run(
    nodes: list[Any],
    *,
    chapter_no: int,
    chapters_per_node: int = 1,
) -> tuple[Any | None, bool]:
    """一章一个节点。超出覆盖范围时返回耗尽，不再夹到最后一个节点。"""
    if not nodes:
        return None, False
    if int(chapter_no) > len(nodes) * int(chapters_per_node):
        return None, True
    index = (int(chapter_no) - 1) // int(chapters_per_node)
    return nodes[index], False


def empty_chapter_production(run: Any, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = getattr(run, "generation_context", None) or {}
    prior = existing or {}
    state = ctx.get("actual_state") if isinstance(ctx.get("actual_state"), dict) else {}
    call_key = prior.get("call_key_version", ctx.get("call_key_version", 2))
    return {
        "schema_version": 1,
        "protocol": PROTOCOL_CHAPTER,
        "executor": ctx.get("executor") or prior.get("executor") or "",
        "call_key_version": int(call_key or 2),
        "phase": "WRITE",
        "repairs": 0,
        "check_fails": [],
        "cast": {},
        "takes": [],
        "accepted": [],
        "working_state": deepcopy(state),
        "call_count": int(prior.get("call_count") or 0),
        "committed_minor": int(prior.get("committed_minor") or 0),
    }


def _ensure_production(run: Any, production: dict[str, Any]) -> None:
    if production.get("protocol") == PROTOCOL_CHAPTER:
        return
    fresh = empty_chapter_production(run, production)
    production.clear()
    production.update(fresh)


def mainline_exhausted(ctx: dict[str, Any] | None) -> bool:
    data = ctx or {}
    if data.get("story_complete"):
        return True
    node = data.get("target_node") or {}
    return not (isinstance(node, dict) and node.get("id"))


def previous_chapter_text(ctx: dict[str, Any] | None) -> str:
    chapters = (ctx or {}).get("recent_chapters") or []
    if not chapters or not isinstance(chapters[0], dict):
        return ""
    return str(chapters[0].get("content") or "")


def open_promises(ctx: dict[str, Any] | None) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for item in (ctx or {}).get("memory") or []:
        if not isinstance(item, dict):
            continue
        if item.get("kind") != "promise":
            continue
        if str(item.get("state") or "OPEN") != "OPEN":
            continue
        if item.get("invalidated"):
            continue
        found.append(
            {
                "subject": item.get("subject") or "",
                "content": item.get("content") or item.get("statement") or "",
            }
        )
    return found


# 执笔只需要事实本身：原文摘录与溯源字段占了 canon 的大半，每次执笔、探针、
# 后继正文都整份带上。新运行在 ASSEMBLE 时打 ``write_canon`` 标记；没有标记的
# 老运行照旧整份，回放的 input_hash 不变。
WRITE_CANON_MARK = "relevant"
# 前置门提示删了不分题材的金手指条款；没有这个标记的老运行回放时照旧带上它。
FRONT_GATE_MARK = "no_power_clause"
# H2 臂超长不删段：删段只看字数不看连贯，删多了正文前后接不上。超长按硬门失败交给
# 递归，探针也按字数判。没有这个标记的运行（含默认臂）照旧删段。
OVERLONG_MARK = "harness"
WRITE_CANON_RECENT_CHAPTERS = 2
WRITE_CANON_RECENT_MAX = 40
WRITE_CANON_EARLIER_MAX = 20
_CANON_BOOKKEEPING = ("quote", "source_tracking", "confidence", "volume_no", "entities")


def _fact_view(fact: Any) -> Any:
    if not isinstance(fact, dict) or not (fact.get("statement") or fact.get("content")):
        return fact
    return {key: value for key, value in fact.items() if key not in _CANON_BOOKKEEPING}


def write_canon(data: dict[str, Any], cast: dict[str, Any]) -> list[Any]:
    """这一章用得上的事实：最近两章的最多 40 条，更早的只留实体在章纲里出现过的、最多 20 条。

    两份配额分开算：最近两章就能写满 80 条，合在一起截尾会把更早的相关事实全挤掉。
    """
    canon = list(data.get("canon") or [])
    if data.get("write_canon") != WRITE_CANON_MARK:
        return canon
    production = data.get("production") if isinstance(data.get("production"), dict) else {}
    plan = production.get("chapter_plan") or data.get("target_node") or {}
    plan_text = json.dumps(plan, ensure_ascii=False)
    names = {str(name) for name in cast or {}}
    floor = int(data.get("chapter_no") or 1) - WRITE_CANON_RECENT_CHAPTERS
    recent: list[int] = []
    earlier: list[int] = []
    for index, fact in enumerate(canon):
        if not isinstance(fact, dict) or int(fact.get("chapter_no") or 0) >= floor:
            recent.append(index)
            continue
        cues = [
            str(e) for e in fact.get("entities") or [] if len(str(e)) >= 2 and str(e) not in names
        ]
        if any(cue in plan_text for cue in cues):
            earlier.append(index)
    keep = set(recent[-WRITE_CANON_RECENT_MAX:]) | set(earlier[-WRITE_CANON_EARLIER_MAX:])
    return [_fact_view(fact) for index, fact in enumerate(canon) if index in keep]


def build_write_payload(ctx: dict[str, Any] | None, cast: dict[str, Any]) -> dict[str, Any]:
    data = ctx or {}
    return {
        "story_bible_block": data.get("story_bible_block") or "",
        "prose_style": data.get("prose_style") or {},
        "reader_contract": data.get("reader_contract") or {},
        "dramatic_engine": data.get("dramatic_engine") or {},
        "cast": cast,
        "target_node": data.get("target_node") or {},
        "previous_chapter": previous_chapter_text(data)[-500:],
        "earlier_results": [item["result"] for item in previous_plans(data)],
        "canon": write_canon(data, cast),
        "open_promises": open_promises(data),
    }


def _writer_system(
    ctx: dict[str, Any] | None,
    cast: dict[str, Any],
    *,
    base: str = CHAPTER_WRITE_SYSTEM,
    tail: str = CHAPTER_LENGTH_RULE,
) -> str:
    """执笔系统提示：基底 + 前置门提示，``tail`` 收在最末。"""
    body = _writer_body(ctx, cast, base=base)
    return body + "\n" + tail if tail else body


def _writer_body(ctx: dict[str, Any] | None, cast: dict[str, Any], *, base: str) -> str:
    data = ctx or {}
    hint = front_gate_writer_hint(
        prose_style=data.get("prose_style") if isinstance(data.get("prose_style"), dict) else {},
        chapter_no=int(data.get("chapter_no") or 1),
        cast=cast,
        world_bible=data.get("world_bible") if isinstance(data.get("world_bible"), dict) else None,
        reader_contract=(
            data.get("reader_contract") if isinstance(data.get("reader_contract"), dict) else None
        ),
        power_clause=data.get("front_gate") != FRONT_GATE_MARK,
    )
    if hint:
        return base + " " + hint
    return base


def h2_config(
    ctx: dict[str, Any] | None,
    production: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> Any:
    """钉住的基础包 ← 本书覆盖层 ← 本章递归覆盖层（← 探针候选）。"""
    from regent.novel.harnessing.surface import (
        BASE_HARNESS_ID,
        apply_overrides,
        load_harness,
        merge_overrides,
    )

    data = ctx or {}
    base = load_harness(str(data.get("harness_id") or BASE_HARNESS_ID))
    layered = merge_overrides(data.get("harness_overlay") or {}, production.get("h2_overrides") or {})
    if extra:
        layered = merge_overrides(layered, extra)
    return apply_overrides(base, layered)


def writing_view(ctx: dict[str, Any] | None, production: dict[str, Any]) -> dict[str, Any]:
    """技巧与钩子求值用的本章视图：只放程序算得出的事实。"""
    from regent.novel.harnessing.surface import ends_mid_action

    data = ctx or {}
    plan = production.get("chapter_plan") or data.get("target_node") or {}
    plan = plan if isinstance(plan, dict) else {}
    chapter_no = int(data.get("chapter_no") or 1)
    ages = [
        chapter_no - int(item["source_chapter_no"])
        for item in data.get("memory") or []
        if isinstance(item, dict)
        and item.get("kind") == "promise"
        and str(item.get("state") or "OPEN") == "OPEN"
        and not item.get("invalidated")
        and item.get("source_chapter_no") is not None
    ]
    place = str(plan.get("place") or "").strip()
    return {
        "place": place,
        "place_streak": place_streak(data, place) + 1 if place else 0,
        "chapter_no": chapter_no,
        "shows_rule": bool(plan.get("shows_rule")),
        "payoff_due": bool(latest_arc_review(data, production).get("payoff_due")),
        "choice": str(plan.get("choice") or ""),
        "result": str(plan.get("result") or ""),
        "previous_ends_mid_action": ends_mid_action(previous_chapter_text(data)[-200:]),
        "oldest_promise_age": max(ages, default=0),
        "same_method_rejects": int(production.get("same_method_rejects") or 0),
    }


def _with_extra(config: Any, key: str, fallback: str) -> str:
    """基础提示 + 覆盖层追加的指引；没有追加时与基础提示逐字相同。"""
    base = config.get_prompt(key) or fallback
    extra = config.get_prompt(f"{key}_extra")
    return base + "\n" + extra if extra else base


def h2_repair_system(config: Any) -> str:
    """追加的指引放在改稿提示正文之后，字数规则仍收在最末。"""
    base = config.get_prompt("repair") or CHAPTER_REPAIR_SYSTEM
    extra = config.get_prompt("repair_extra")
    if not extra:
        return base
    tail = config.get_prompt("write_tail")
    if tail and base.endswith(tail):
        return base[: -len(tail)].rstrip("\n") + "\n" + extra + "\n" + tail
    return base + "\n" + extra


def h2_writer_system(
    ctx: dict[str, Any] | None, cast: dict[str, Any], config: Any, production: dict[str, Any]
) -> str:
    from regent.novel.harnessing.surface import resolve_hooks, resolve_skills

    system = _writer_body(ctx, cast, base=_with_extra(config, "write", CHAPTER_WRITE_SYSTEM))
    memory = config.get_memory()
    if memory:
        system += "\n\n写作经验：\n" + memory
    view = writing_view(ctx, production)
    skills = resolve_skills(config, view)
    if skills:
        system += "\n\n" + skills
    hooks = resolve_hooks(config, view)
    if hooks:
        system += "\n\n" + "\n".join(hooks)
    # 收尾的字数规则跟着钉住的基础包走（v3 起才有），覆盖层改不到它。
    tail = config.get_prompt("write_tail")
    if tail:
        system += "\n" + tail
    return system


def h2_write_payload(ctx: dict[str, Any] | None, cast: dict[str, Any], config: Any) -> dict[str, Any]:
    """旋钮取基础包默认值时，与 build_write_payload 逐字一致。"""
    payload = build_write_payload(ctx, cast)
    payload["previous_chapter"] = previous_chapter_text(ctx)[-int(config.get_knob("previous_chapter_tail")) :]
    results = int(config.get_knob("earlier_results_count") or 0)
    if results:
        payload["earlier_results"] = payload["earlier_results"][-results:]
    promises = int(config.get_knob("open_promises_max") or 0)
    if promises:
        payload["open_promises"] = payload["open_promises"][:promises]
    return payload


def h2_soft_issues(content: str, ctx: dict[str, Any] | None, config: Any) -> list[str]:
    from regent.novel.harnessing.surface import run_soft_checks

    return run_soft_checks(content, config, previous_chapter=previous_chapter_text(ctx))


def h2_stall_issues(content: str, ctx: dict[str, Any] | None, production: dict[str, Any]) -> list[str]:
    """正文原地打转的证据：同一句式反复开头、整句照抄上一章。连续停在同一地点的章数只作背景，不单独算。"""
    from regent.novel.harnessing.surface import run_soft_checks

    from regent.novel.harnessing.surface import model_check, model_check_issue

    config = h2_config(ctx, production)
    issues = run_soft_checks(
        content,
        config,
        previous_chapter=previous_chapter_text(ctx),
        kinds=frozenset({"opener", "echo"}),
    )
    landing = production.get("h2_landing") if isinstance(production.get("h2_landing"), dict) else {}
    spec = model_check(config, "landing")
    if spec and landing.get("landed") is False and landing.get("hash") == _content_hash(content):
        issues.append(
            model_check_issue(
                spec,
                {"missing": landing.get("missing") or "章纲的结果", "ending": landing.get("ending") or ""},
            )
        )
    view = writing_view(ctx, production)
    if issues and view["place_streak"] >= 2:
        issues.append(f"主角已经连续 {view['place_streak']} 章在「{view['place']}」")
    return issues


def _plan_now(ctx: dict[str, Any] | None, production: dict[str, Any]) -> dict[str, Any]:
    plan = production.get("chapter_plan") or (ctx or {}).get("target_node") or {}
    return plan if isinstance(plan, dict) else {}


def h2_landing_due(content: str, ctx: dict[str, Any] | None, production: dict[str, Any]) -> bool:
    """这一稿该不该判一次写没写到章纲结果：配置开着、这一章还开得了 stall 轮、这一稿没判过。"""
    from regent.novel.harnessing.surface import model_check

    if int(production.get("h2_round") or 0) or production.get("h2_stall_done") or production.get("h2_stop"):
        return False
    review = production.get("arc_review")
    if isinstance(review, dict) and review.get("keeps_reading") is False and not production.get("h2_arc_done"):
        return False
    if not str(_plan_now(ctx, production).get("result") or "").strip():
        return False
    if model_check(h2_config(ctx, production), "landing") is None:
        return False
    landing = production.get("h2_landing") if isinstance(production.get("h2_landing"), dict) else {}
    return landing.get("hash") != _content_hash(content)


def drop_copied_span(
    text: str,
    previous: str,
    *,
    min_len: int = 80,
    floor: int = MIN_CHAPTER_CHARS,
) -> str:
    """删掉从上一章整段抄来的那一截。删完低于下限就保持原样，交给硬门。"""
    body = (text or "").replace("\r", "").strip()
    while True:
        copied = longest_common_substring(body, previous or "", min_len=min_len)
        if not copied or is_legitimate_repetition(copied):
            return body
        kept = body.replace(copied, "", 1).strip()
        if len(kept) < floor:
            return body
        body = kept


def drop_repeated_span(text: str, *, min_len: int = 24, floor: int = MIN_CHAPTER_CHARS) -> str:
    """同一段原文在后文再出现时，删掉后一次。删完低于下限就保持原样，交给硬门。"""
    body = (text or "").replace("\r", "").strip()
    while True:
        window = repeated_span(body, min_len=min_len)
        if not window:
            return body
        start = body.find(window)
        later = body.find(window, start + len(window))
        length = len(window)
        while (
            later + length < len(body)
            and start + length < later
            and body[start + length] == body[later + length]
        ):
            length += 1
        kept = (body[:later] + body[later + length :]).strip()
        if len(kept) < floor:
            return body
        body = kept


def dedupe_chapter_text(text: str, previous: str) -> str:
    """去掉抄上一章的整段和本章内的自我重复。

    这两处都是删句子，删完可能变短，但不负责压到上限以内：超长由 FIT 那一次
    定向删段处理，删不回来才轮到 ``fit_chapter_length`` 截断。
    """
    return drop_repeated_span(drop_copied_span(text, previous))


def length_brief(draft: str) -> dict[str, int]:
    """改稿入参里的字数：模型数不准字，给它现成的数和该落的区间。"""
    chars = len(draft or "")
    brief = {"current_chars": chars, "min_chars": MIN_CHAPTER_CHARS, "max_chars": MAX_CHAPTER_CHARS}
    if chars > MAX_CHAPTER_CHARS:
        brief["need_cut"] = chars - FIT_TARGET_CHARS
    elif chars < MIN_CHAPTER_CHARS:
        brief["need_add"] = MIN_CHAPTER_CHARS - chars
    return brief


def fit_chapter_length(
    text: str,
    *,
    limit: int = MAX_CHAPTER_CHARS,
    floor: int = MIN_CHAPTER_CHARS,
) -> str:
    """删段之后仍超长时的兜底：从最后一个句号或换行处删到上限以内，且不低于下限。

    这是最后一道，不是常规路径：章末结果正好落在被删掉的那一段里，能删段就不要砍。
    """
    body = (text or "").replace("\r", "").strip()
    if len(body) <= limit:
        return body
    cut = body[:limit]
    for sep in ("\n", "。"):
        idx = cut.rfind(sep)
        end = idx + len(sep)
        if end >= floor:
            return cut[:end].strip()
    return cut.strip()


def repeated_span(text: str, *, min_len: int = 24) -> str:
    """同一章里不重叠地再出现一次的连续原文。"""
    body = text or ""
    if len(body) < min_len * 2:
        return ""
    for start in range(0, len(body) - min_len):
        window = body[start : start + min_len]
        if len(set(window)) < 6:
            continue
        if window in body[start + min_len :] and not is_legitimate_repetition(window):
            return window
    return ""


def _cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


# 这些字出现在差的那一位上时，是说法不是人名。
_NOT_A_NAME_CHAR = frozenset(
    "的了在把是和与这那他她它们着过被从对为就也都而但一不没个名向将让给到由以及或若如其之所则以"
)
# 人名前面可以紧挨着这些字。其余汉字说明这个窗口还嵌在别的词里。
_NAME_PREFIX = frozenset("舅哥弟姐妹父母爸妈叔伯婶姨爷奶师老小阿叫姓是为名他她它")


def _span_prefix(body: str, index: int) -> str:
    start = index
    while start > 0 and _cjk(body[start - 1]):
        start -= 1
    return body[start:index]


def stray_names(text: str, cast_names: list[str]) -> list[str]:
    """和卡司人名只差一个字的叫法。正文里的本人名不算。"""
    body = text or ""
    names = [name for name in cast_names if len(name) >= 3 and all(_cjk(ch) for ch in name)]
    found: list[str] = []
    known = set(names)
    # 「林老师」对上「王老师」时，差的是卡司另一个人的姓：这是在叫那个人。
    surnames = {name[0] for name in cast_names if len(name) >= 2 and _cjk(name[0])}
    for name in names:
        width = len(name)
        for index in range(0, len(body) - width + 1):
            token = body[index : index + width]
            if token in known or not all(_cjk(ch) for ch in token):
                continue
            pairs = list(zip(token, name))
            if sum(left != right for left, right in pairs) != 1:
                continue
            if any(left in _NOT_A_NAME_CHAR for left, right in pairs if left != right):
                continue
            if token[0] != name[0] and token[0] in surnames:
                continue
            prefix = _span_prefix(body, index)
            if prefix and any(ch not in _NAME_PREFIX for ch in prefix):
                continue
            if token not in found:
                found.append(token)
    return found


def landed_plan(node: dict[str, Any], production: dict[str, Any] | None) -> dict[str, Any]:
    """入台账的章纲：正文没写到章纲的结果时，结果换成正文实际停下的局面。

    后面几章按台账规划，结果若还是没写出来的那一场，规划会一直想把它补写出来。
    """
    landing = (production or {}).get("landing")
    if not isinstance(landing, dict) or landing.get("landed") is not False:
        return node
    ending = str(landing.get("ending") or "").strip()
    if not ending:
        return node
    return {**node, "result": ending, "planned_result": node.get("result")}


def _plan_entry(node: dict[str, Any]) -> dict[str, Any]:
    entry = {
        "pressure": str(node.get("pressure") or ""),
        "choice": str(node.get("choice") or ""),
        "result": str(node.get("result") or ""),
        "shows_rule": bool(node.get("shows_rule")),
    }
    if node.get("place"):
        entry["place"] = str(node["place"])
    if node.get("stage_index") is not None:
        entry["stage_index"] = int(node.get("stage_index") or 0)
        entry["reaches_stage"] = bool(node.get("reaches_stage"))
    return entry


def _same_place(a: str, b: str) -> bool:
    """换个叫法也算同一处：「约束室」与「物理约束观察室」共有的一段占到较短一方的一半。"""
    a, b = str(a or "").strip(), str(b or "").strip()
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    shorter = min(len(a), len(b))
    return bool(longest_common_substring(a, b, min_len=max(2, -(-shorter // 2))))


def place_streak(ctx: dict[str, Any] | None, place: str) -> int:
    """主角在 ``place`` 已经连续待了几章，不含这一章。

    记了地点的章按地点比；没记地点的旧章看章纲里提没提到这个地名，或正文里出现几次。
    """
    place = str(place or "").strip()
    if not place:
        return 0
    data = ctx or {}
    plans: dict[int, dict[str, Any]] = {}
    for node in data.get("chapter_plans") or []:
        if isinstance(node, dict) and node.get("chapter_no") is not None:
            plans[int(node["chapter_no"])] = node
    # chapter_plans 只有本卷；跨卷还待在同一处，要靠不分卷的地点记录接上
    for item in data.get("place_history") or []:
        if isinstance(item, dict) and item.get("chapter_no") is not None:
            plans.setdefault(int(item["chapter_no"]), item)
    prose: dict[int, str] = {}
    for item in data.get("recent_chapters") or []:
        if not isinstance(item, dict) or item.get("chapter_no") is None:
            continue
        number = int(item["chapter_no"])
        prose[number] = str(item.get("content") or "")
        if isinstance(item.get("target_node"), dict):
            plans.setdefault(number, item["target_node"])
    streak = 0
    number = int(data.get("chapter_no") or 0) - 1
    while number >= 1:
        node = plans.get(number) or {}
        if node.get("place"):
            here = _same_place(place, str(node["place"]))
        else:
            text = "".join(str(node.get(key) or "") for key in ("pressure", "choice", "result"))
            here = place in text or prose.get(number, "").count(place) >= PLACE_MENTIONS_IN_PROSE
        if not here:
            break
        streak += 1
        number -= 1
    return streak


def place_limit(ctx: dict[str, Any] | None, production: dict[str, Any]) -> int:
    if _is_h2(production):
        return int(h2_config(ctx, production).get_knob("stall_place_chapters") or STALL_PLACE_CHAPTERS)
    return STALL_PLACE_CHAPTERS


def place_stall_reason(node: dict[str, Any], ctx: dict[str, Any] | None, production: dict[str, Any]) -> str:
    place = str(node.get("place") or "").strip()
    streak = place_streak(ctx, place)
    if not place or streak < place_limit(ctx, production):
        return ""
    return f"主角已经连续 {streak} 章在「{place}」，这一章让他离开那里（逃出去、被带走或被逼去别处），place 写新的地点"


def previous_plans(ctx: dict[str, Any] | None) -> list[dict[str, Any]]:
    """本卷已入库章节的章纲，按章节顺序。"""
    data = ctx or {}
    ledger = data.get("chapter_plans")
    if isinstance(ledger, list) and ledger:
        return [_plan_entry(node) for node in ledger if isinstance(node, dict)]
    rhythm = data.get("book_rhythm") if isinstance(data.get("book_rhythm"), dict) else {}
    start = int(rhythm.get("volume_start") or 0)
    found: list[dict[str, Any]] = []
    for chapter in reversed(list(data.get("recent_chapters") or [])):
        if not isinstance(chapter, dict) or int(chapter.get("chapter_no") or 0) < start:
            continue
        node = chapter.get("target_node") if isinstance(chapter.get("target_node"), dict) else {}
        if node.get("choice") and node.get("result"):
            found.append(_plan_entry(node))
    return found


def stage_position(
    earlier: list[dict[str, Any]], stages: list[str], *, cap: int = 0
) -> tuple[int, int, bool]:
    """眼下在第几个阶段、这一段已经写了几章、阶段是否全部走完。

    cap 大于 0 时，一段写满 cap 章就算走完，不再等章纲自己报成立。
    """
    if not stages:
        return 0, 0, False
    index = 0
    spent = 0
    for item in earlier:
        if item.get("stage_index") is not None and int(item["stage_index"]) > index:
            index = int(item["stage_index"])
            spent = 0
        spent += 1
        if item.get("reaches_stage") or (cap and spent >= cap):
            index += 1
            spent = 0
    return index, spent, index >= len(stages)


def min_chapters_per_stage(rhythm: dict[str, Any]) -> int:
    stages = list((rhythm or {}).get("stages") or [])
    chapters = int((rhythm or {}).get("volume_chapters") or 0)
    if not stages or chapters <= 0:
        return 1
    return max(1, chapters // (len(stages) * 2))


def max_chapters_per_stage(rhythm: dict[str, Any]) -> int:
    stages = list((rhythm or {}).get("stages") or [])
    chapters = int((rhythm or {}).get("volume_chapters") or 0)
    if not stages or chapters <= 0:
        return 0
    return max(min_chapters_per_stage(rhythm) + 1, -(-chapters // len(stages)))


def stage_pushed(rhythm: dict[str, Any], earlier: list[dict[str, Any]], review: dict[str, Any] | None) -> bool:
    """自评要求这一段成立，且这一段已经写到下限：编辑只能催，不能跳过章数下限。

    编辑自己还列着这一段没写到的事时，催的是把那件事写出来，不是让这一段提前成立。
    """
    if not (review or {}).get("advance_stage") or (review or {}).get("stage_remaining"):
        return False
    stages = [str(s) for s in (rhythm or {}).get("stages") or []]
    if not stages:
        return False
    _, spent, done = stage_position(earlier, stages, cap=max_chapters_per_stage(rhythm))
    return not done and spent + 1 >= min_chapters_per_stage(rhythm)


def settle_stage_claim(
    node: dict[str, Any], rhythm: dict[str, Any], earlier: list[dict[str, Any]], review: dict[str, Any] | None
) -> None:
    """带警告收下的章纲，不许借这一章把没写够的一段报成成立。"""
    stages = [str(s) for s in (rhythm or {}).get("stages") or []]
    if not stages or not node.get("reaches_stage"):
        return
    _, spent, _ = stage_position(earlier, stages, cap=max_chapters_per_stage(rhythm))
    if spent + 1 < min_chapters_per_stage(rhythm) and not stage_pushed(rhythm, earlier, review):
        node["reaches_stage"] = False


def _shared_span(a: str, b: str, floor: int) -> str:
    """两段章纲文字共有的原文。门槛随较短一段的长度放大：长结果里复述没变的处境不算同一场。"""
    shorter = min(len(a), len(b))
    return longest_common_substring(a, b, min_len=max(floor, shorter // 5))


def _overlap_share(a: str, b: str, min_len: int) -> float:
    """较短一段里有多少字落在两段共有的原文片段上。"""
    shorter = min(len(a), len(b))
    if shorter == 0:
        return 0.0
    covered = 0
    rest_a, rest_b = a, b
    while True:
        span = longest_common_substring(rest_a, rest_b, min_len=min_len)
        if not span:
            break
        covered += len(span)
        rest_a = rest_a.replace(span, "\x00", 1)
        rest_b = rest_b.replace(span, "\x01", 1)
    return covered / shorter


def _clip(text: Any, limit: int = 60) -> str:
    body = str(text or "").strip()
    return body if len(body) <= limit else body[:limit] + "……"


def repeated_scene(
    plan: dict[str, Any], earlier: list[dict[str, Any]], *, chapter_no: int | None = None
) -> str:
    """章纲的结果和前面某章是同一场时，返回那一章和它的结果。"""
    result = str(plan.get("result") or "").strip()
    if not result:
        return ""
    first = int(chapter_no or len(earlier) + 1) - len(earlier)
    for offset, item in enumerate(earlier):
        if _overlap_share(result, str(item.get("result") or ""), 10) >= 0.5:
            return f"第{first + offset}章已经写过这一场（{_clip(item.get('result'))}）"
    return ""


def rhythm_mismatch(
    plan: dict[str, Any],
    rhythm: dict[str, Any],
    earlier: list[dict[str, Any]],
    *,
    chapter_no: int | None = None,
    review: dict[str, Any] | None = None,
) -> str:
    """章纲偏离全书节奏时返回原因。对得上就返回空字符串。

    review 是这一章动笔前的追读自评：它可以要求这一章兑现看点或让眼下这一段成立。
    """
    review = review or {}
    choice = str(plan.get("choice") or "").strip()
    result = str(plan.get("result") or "").strip()
    pressure = str(plan.get("pressure") or "").strip()
    if not pressure or not choice or not result:
        return "章纲缺了压力、选择或结果"
    first = int(chapter_no or len(earlier) + 1) - len(earlier)
    for offset, item in enumerate(earlier):
        label = f"第{first + offset}章"
        # 结果里复述没变的处境不算同一场：共有原文要占到一半以上。
        if _overlap_share(result, str(item.get("result") or ""), 10) >= 0.5:
            return (
                f"这一章的结果和{label}是同一场（{label}：{_clip(item.get('result'))}），"
                "那一场已经写完，这一章写它之后发生的事"
            )
        if _shared_span(pressure, str(item.get("pressure") or ""), 20):
            return f"这一章的压力和{label}相同，要写上一章结果造成的新处境"
        if _shared_span(choice, str(item.get("choice") or ""), 12):
            return (
                f"主角在{label}已经这样做过（{_clip(item.get('choice'))}），"
                "这一章换一种做法，或者让局面逼他做一件更冒险的事"
            )
    stake = str((rhythm or {}).get("stake") or "")
    if earlier and stake and longest_common_substring(pressure, stake, min_len=12):
        return "压力照抄了这一卷的目标，要写上一章结果造成的新处境"
    stages = [str(s) for s in (rhythm or {}).get("stages") or []]
    if stages:
        index, spent, _ = stage_position(earlier, stages, cap=max_chapters_per_stage(rhythm))
        if not earlier and longest_common_substring(pressure, stages[0], min_len=12):
            return "第一段还没发生，不要写进压力；这一章从开场局面出发"
        for later in stages[index + 1 :]:
            if longest_common_substring(result, later, min_len=12):
                return "这一章越过了眼下这一段，直接写到了后面的阶段"
        floor = min_chapters_per_stage(rhythm)
        pushed = stage_pushed(rhythm, earlier, review)
        if plan.get("reaches_stage") and spent + 1 < floor and not pushed:
            return f"眼下这一段才写了 {spent + 1} 章，先写这一段里的下一步，不要这一章就让它成立"
        cap = max_chapters_per_stage(rhythm)
        last = bool(cap and spent + 1 >= cap) or pushed
        if last and index < len(stages) and not plan.get("reaches_stage"):
            why = "编辑判断这一段已经拖住了" if pushed and not (cap and spent + 1 >= cap) else "这一章是这一段的最后一章"
            return f"{why}，结果要让「{_clip(stages[index], 40)}」成立，reaches_stage 为 true"
    if review.get("payoff_due"):
        if not plan.get("shows_rule"):
            return "编辑判断答应读者的东西已经好几章没有兑现，这一章让它当场发生，shows_rule 为 true"
        return ""
    limit = int((rhythm or {}).get("payoff_limit") or 1)
    shown = sum(1 for item in earlier if item.get("shows_rule"))
    if plan.get("shows_rule") and shown >= limit:
        return "这一卷答应读者的事已经发生够了，这一章要写它逼出来的下一步"
    return ""


def _plan_node(
    plan: ChapterPlan, *, node_id: str, stage_index: int | None = None
) -> dict[str, Any]:
    promise = f"压力：{plan.pressure}\n选择：{plan.choice}\n结果：{plan.result}"
    node = {
        "id": node_id,
        "title": plan.choice[:40],
        "promise": promise,
        "pressure": plan.pressure,
        "choice": plan.choice,
        "result": plan.result,
        "shows_rule": bool(plan.shows_rule),
        "used_methods": list(plan.used_methods),
        **({"place": plan.place.strip()} if plan.place.strip() else {}),
        "preconditions": [],
        "consequences": [],
    }
    if stage_index is not None:
        node["stage_index"] = int(stage_index)
        node["reaches_stage"] = bool(plan.reaches_stage)
    return node


def plan_payload(ctx: dict[str, Any] | None, cast: dict[str, Any], production: dict[str, Any]) -> dict[str, Any]:
    data = ctx or {}
    rhythm = data.get("book_rhythm") if isinstance(data.get("book_rhythm"), dict) else {}
    earlier = previous_plans(data)
    stages = [str(s) for s in rhythm.get("stages") or []]
    cap = max_chapters_per_stage(rhythm)
    index, spent, _ = stage_position(earlier, stages, cap=cap)
    if earlier:
        previous_result = earlier[-1]["result"]
    else:
        previous_result = str(rhythm.get("opening") or "")
    payload: dict[str, Any] = {
        "chapter_no": int(data.get("chapter_no") or 1),
        "book_rhythm": {
            "stake": rhythm.get("stake") or "",
            "payoff_limit": rhythm.get("payoff_limit") or 1,
            "arc_end": rhythm.get("arc_end") or "",
        },
        "previous_result": previous_result,
        "earlier_plans": earlier,
        "previous_ending": previous_chapter_text(data)[-400:],
        "cast": list(cast),
        "reject_reason": production.get("plan_reject") or "",
        "rejected_plan": production.get("plan_rejected") or {},
    }
    review = production.get("arc_review") if isinstance(production.get("arc_review"), dict) else {}
    if stages:
        payload["current_stage"] = stages[min(index, len(stages) - 1)]
        payload["chapters_in_stage"] = spent
        payload["stage_chapters"] = [min_chapters_per_stage(rhythm), cap]
        payload["last_chapter_of_stage"] = bool(cap and spent + 1 >= cap)
        payload["later_stages"] = stages[index + 1 :]
        payload["advance_stage"] = stage_pushed(rhythm, earlier, review)
    payload["payoff_due"] = bool(review.get("payoff_due"))
    last_place = str((earlier[-1] if earlier else {}).get("place") or "")
    streak = place_streak(data, last_place)
    if streak >= 2:
        payload["place_streak"] = {
            "place": last_place,
            "chapters": streak,
            "limit": place_limit(data, production),
        }
    notes = editor_notes(data, production)
    if notes:
        payload["editor_notes"] = notes
    return payload


def latest_arc_review(ctx: dict[str, Any] | None, production: dict[str, Any]) -> dict[str, Any]:
    """这一章做过的自评；没做就取本卷最近一章留下的。"""
    own = production.get("arc_review")
    if isinstance(own, dict) and own:
        return own
    for node in reversed(list((ctx or {}).get("chapter_plans") or [])):
        if isinstance(node, dict) and isinstance(node.get("arc_review"), dict) and node["arc_review"]:
            return node["arc_review"]
    return {}


def editor_notes(ctx: dict[str, Any] | None, production: dict[str, Any]) -> dict[str, Any]:
    review = latest_arc_review(ctx, production)
    directives = [str(item).strip() for item in review.get("directives") or [] if str(item).strip()]
    problems = [
        str(item.get("problem") or "").strip()
        for item in review.get("problems") or []
        if isinstance(item, dict) and str(item.get("problem") or "").strip()
    ]
    if not directives and not problems:
        return {}
    return {"problems": problems, "directives": directives}


def arc_review_due(ctx: dict[str, Any] | None, production: dict[str, Any]) -> bool:
    if production.get("arc_checked"):
        return False
    written = len(previous_plans(ctx))
    return written >= ARC_REVIEW_EVERY and written % ARC_REVIEW_EVERY == 0


def arc_review_payload(ctx: dict[str, Any] | None, production: dict[str, Any]) -> dict[str, Any]:
    data = ctx or {}
    rhythm = data.get("book_rhythm") if isinstance(data.get("book_rhythm"), dict) else {}
    stages = [str(s) for s in rhythm.get("stages") or []]
    ledger = [node for node in data.get("chapter_plans") or [] if isinstance(node, dict)]
    recent = [
        {
            "chapter_no": node.get("chapter_no"),
            **{key: _plan_entry(node).get(key) for key in ("pressure", "choice", "result", "shows_rule")},
        }
        for node in ledger[-ARC_REVIEW_WINDOW:]
    ]
    earlier = previous_plans(data)
    index, spent, _ = stage_position(earlier, stages, cap=max_chapters_per_stage(rhythm))
    previous = latest_arc_review(data, {})
    return {
        "reader_contract": data.get("reader_contract") or {},
        "dramatic_engine": data.get("dramatic_engine") or {},
        "book_rhythm": {
            "stake": rhythm.get("stake") or "",
            "current_stage": stages[min(index, len(stages) - 1)] if stages else "",
            "chapters_in_stage": spent,
            "stage_chapters": [min_chapters_per_stage(rhythm), max_chapters_per_stage(rhythm)],
            "later_stages": stages[index + 1 :],
            "arc_end": rhythm.get("arc_end") or "",
        },
        "payoff_chapters": [
            node.get("chapter_no") for node in ledger if _plan_entry(node).get("shows_rule")
        ],
        "recent_chapters": recent,
        "previous_review": {
            "directives": previous.get("directives") or [],
            "problems": [
                item.get("problem") for item in previous.get("problems") or [] if isinstance(item, dict)
            ],
        }
        if previous
        else {},
    }


def check_chapter(
    content: str,
    *,
    ctx: dict[str, Any] | None,
    cast: dict[str, Any],
    title: str,
    ignore_max_length: bool = False,
    ignore_min_length: bool = False,
) -> list[str]:
    data = ctx or {}
    text = (content or "").strip()
    world = data.get("world_bible") if isinstance(data.get("world_bible"), dict) else None
    style = data.get("prose_style") if isinstance(data.get("prose_style"), dict) else {}
    contract = data.get("reader_contract") if isinstance(data.get("reader_contract"), dict) else None
    fails = front_gate_hard_fails(
        text,
        prose_style=style,
        title=title,
        cast=cast,
        production_packet={},
        chapter_no=int(data.get("chapter_no") or 1),
        min_chars=800,
        world_bible=world,
        reader_contract=contract,
    )
    if len(text) < MIN_CHAPTER_CHARS and not ignore_min_length:
        fails.append(f"正文不足{MIN_CHAPTER_CHARS}字")
    if len(text) > MAX_CHAPTER_CHARS and not ignore_max_length:
        fails.append(f"正文超过{MAX_CHAPTER_CHARS}字（这一稿{len(text)}字）")
    previous = previous_chapter_text(data)
    copied = longest_common_substring(text, previous, min_len=80)
    if copied and not is_legitimate_repetition(copied):
        fails.append(f"与上一章连续重复{len(copied)}字")
    replay = repeated_span(text)
    if replay:
        fails.append("同一段原文在后文又出现了一遍")
    return list(dict.fromkeys(fails))


def name_warnings(content: str, cast: dict[str, Any]) -> list[str]:
    """近似人名只是猜测：交给改稿去核对，改不掉也不拦章。"""
    stray = stray_names(content or "", _cast_name_list(cast))
    if not stray:
        return []
    return ["出现了和卡司只差一字的叫法，若是人名请改回卡司里的名字：" + "、".join(stray[:3])]


def _cast_name_list(cast: dict[str, Any]) -> list[str]:
    return [str(name).strip() for name in (cast or {}) if str(name).strip()]


def grounded_facts(content: str, facts: list[Any]) -> list[dict[str, Any]]:
    """只保留摘录能在正文中逐字找到的事实。"""
    text = content or ""
    kept: list[dict[str, Any]] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        quote = str(fact.get("quote") or "").strip()
        statement = str(fact.get("statement") or "").strip()
        if statement and quote and quote in text:
            kept.append(fact)
    return kept


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _store_prose(run: Any, production: dict[str, Any], content: str) -> None:
    text = content.strip()
    run.content = text
    run.word_count = len(text)
    versions: list[str] = []
    if production.get("takes"):
        versions = list((production["takes"][0] or {}).get("prose_versions") or [])
    versions.append(text)
    production["takes"] = [
        {
            "take_no": 1,
            "scene_index": 0,
            "content": text,
            "status": "DRAFT",
            "prose_versions": versions,
            "validation": {"passed": False, "issues": [], "facts": []},
        }
    ]
    production["accepted"] = []


_SENTENCE_END = re.compile(r"(?<=[。！？!?])(?![」”’』\"])|(?<=[。！？!?][」”’』\"])")


def fit_units(text: str) -> list[tuple[int, str]]:
    """把稿子切成可整段删的片段：(所在段号, 原文)。过长的段再按句切开。"""
    paragraphs = [p.strip() for p in (text or "").replace("\r", "").split("\n") if p.strip()]
    units: list[tuple[int, str]] = []
    for index, para in enumerate(paragraphs):
        if len(para) <= FIT_UNIT_MAX_CHARS:
            units.append((index, para))
            continue
        units.extend((index, piece) for piece in _SENTENCE_END.split(para) if piece)
    return units


def join_units(units: list[tuple[int, str]], sep: str = "\n\n") -> str:
    paragraphs: dict[int, list[str]] = {}
    for index, piece in units:
        paragraphs.setdefault(index, []).append(piece)
    return sep.join("".join(pieces) for pieces in paragraphs.values())


def _paragraph_sep(text: str) -> str:
    return "\n\n" if "\n\n" in (text or "").replace("\r", "") else "\n"


def fit_protected(units: list[tuple[int, str]]) -> list[int]:
    """章末片段：从最后一段往前数，直到覆盖 ``FIT_PROTECTED_TAIL_CHARS`` 字。"""
    protected: list[int] = []
    tail = 0
    for no in range(len(units) - 1, -1, -1):
        protected.append(no)
        tail += len(units[no][1])
        if tail >= FIT_PROTECTED_TAIL_CHARS:
            break
    return sorted(protected)


_QUESTION_END = re.compile(r"[？?][”」』\"']?\s*$")
_DIALOGUE_START = re.compile(r"^\s*[“「『\"]")


def fit_seam_break(units: list[tuple[int, str]], removed: set[int], terms: list[str]) -> str:
    """删掉 ``removed`` 后会断开的接缝；没有返回空串。

    只查代码查得准的两种：专名第一次出场的那段被删、后文还在用它；
    问句被删、紧跟的对白答句还留着。
    """
    for term in terms:
        hits = [no for no, (_, piece) in enumerate(units) if term in piece]
        if hits and hits[0] in removed and any(no not in removed for no in hits):
            return f"专名首次出场被删：{term}"
    for no in removed:
        nxt = no + 1
        if (
            nxt < len(units)
            and nxt not in removed
            and _QUESTION_END.search(units[no][1])
            and _DIALOGUE_START.match(units[nxt][1])
        ):
            return f"问句被删、答句还在：片段 {no}"
    return ""


def apply_fit_cut(
    draft: str, drop: list[int], *, terms: list[str] | tuple[str, ...] = ()
) -> tuple[str, list[int]]:
    """按模型挑的顺序删片段，删到 ``FIT_TARGET_CHARS`` 以内就停。

    跳过章末保护区、越界编号、会删到下限以下的、以及会断开接缝（``fit_seam_break``）
    的那一段；模型挑多了不照单全删，每多删一段就多一道接缝。
    """
    units = fit_units(draft)
    sep = _paragraph_sep(draft)
    protected = set(fit_protected(units))
    removed: set[int] = set()
    body = join_units(units, sep)
    for no in drop:
        if len(body) <= FIT_TARGET_CHARS:
            break
        if no in removed or no in protected or not 0 <= no < len(units):
            continue
        trial = removed | {no}
        if fit_seam_break(units, trial, list(terms)):
            continue
        shorter = join_units([u for i, u in enumerate(units) if i not in trial], sep)
        if len(shorter) < MIN_CHAPTER_CHARS:
            continue
        removed, body = trial, shorter
    return body, sorted(removed)


def fit_payload(
    ctx: dict[str, Any] | None, production: dict[str, Any], cast: dict[str, Any], draft: str
) -> dict[str, Any]:
    """删段调用的入参：编号片段、至少要删的字数、章末保护区和必须保住的选择与结果。

    模型数不准字，让它直接压到上限以内会原样交回；它只挑段，字数由代码保证。
    """
    data = ctx or {}
    plan = production.get("chapter_plan") or data.get("target_node") or {}
    plan = plan if isinstance(plan, dict) else {}
    units = fit_units(draft)
    return {
        "units": [{"no": no, "chars": len(piece), "text": piece} for no, (_, piece) in enumerate(units)],
        "need_cut": max(0, len(join_units(units, _paragraph_sep(draft))) - FIT_TARGET_CHARS),
        "protected": fit_protected(units),
        "must_keep": {
            "choice": str(plan.get("choice") or ""),
            "result": str(plan.get("result") or ""),
        },
    }


def accept_draft(
    run: Any, production: dict[str, Any], *, ctx: dict[str, Any] | None, draft: str, tag: str
) -> None:
    """收下一稿：去重后不超长就落稿进 CHECK，超长先进 FIT 删段，不砍尾。

    H2 臂打了 ``OVERLONG_MARK`` 的运行不删段，超长稿原样进 CHECK。
    """
    production.pop("fit_truncated", None)
    body = dedupe_chapter_text(draft, previous_chapter_text(ctx))
    # 首稿字数：覆盖层试用拿它和试用前比，加戏的覆盖层会先在这里露出来
    production.setdefault("draft_chars", len(body))
    if len(body) <= MAX_CHAPTER_CHARS or not cuts_overlong(ctx, production):
        _store_prose(run, production, body)
        production["phase"] = "CHECK"
        return
    # 稿子内容进命令标识：同一 tag 换一份稿子必须是新的一次调用，
    # 否则会撞上 CallBroker 的幂等键（同键不同入参 → CallConflict）。
    production["fit"] = {"draft": body, "tag": f"{tag}:{_content_hash(body)[:8]}"}
    production["phase"] = "FIT"


async def fit_tick(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: Any,
    production: dict[str, Any],
    cast: dict[str, Any],
    ctx: dict[str, Any] | None,
    version: int,
) -> None:
    """超长稿定向删段：删完落稿进 CHECK；删完仍超长才截断并留痕。"""
    spec = dict(production.get("fit") or {})
    draft = str(spec.get("draft") or "")
    tag = str(spec.get("tag") or "draft")
    picked = await _call(
        session,
        provider,
        work,
        run,
        production,
        ChapterFitCut,
        CHAPTER_FIT_SYSTEM,
        fit_payload(ctx, production, cast, draft),
        "chapter_fit",
        f"v{version}:fit:{tag}",
        temperature=0.3,
    )
    from regent.novel.harnessing.surface import book_terms

    production = run.generation_context["production"]
    spec = dict(production.get("fit") or {})
    flat, removed = apply_fit_cut(draft, list(picked.drop), terms=book_terms(ctx, cast))
    body = fit_chapter_length(flat)
    cut = flat[len(body) :].strip() if flat.startswith(body) else ""
    truncated = len(flat) > MAX_CHAPTER_CHARS
    production.setdefault("fit_results", []).append(
        {
            "tag": str(spec.get("tag") or tag),
            "chars_before": len(draft),
            "chars_dropped": len(draft) - len(flat),
            "units_dropped": len(removed),
            "chars_after": len(body),
            "truncated": truncated,
        }
    )
    production.pop("fit", None)
    if truncated:
        # 砍掉的正是章末。留着尾巴让改稿把它接回去，并按硬门失败处理：
        # 只记一笔就放行，等于把「第 4 章交付稿丢了章纲结果」原样再演一遍。
        production["fit_truncated"] = cut
    _store_prose(run, production, body)
    production["phase"] = "CHECK"
    _save(run, production)


async def load_cast(
    session: AsyncSession,
    work: StoryWorkModel,
    ctx: dict[str, Any] | None,
) -> dict[str, Any]:
    rows = (
        await session.scalars(select(PersonaSpecModel).where(PersonaSpecModel.work_id == work.id))
    ).all()
    cast = {
        row.name: {"identity": row.identity, "drives": row.drives, "voice": row.voice}
        for row in rows
    }
    if cast:
        return cast
    bible = (ctx or {}).get("world_bible") if isinstance((ctx or {}).get("world_bible"), dict) else {}
    for persona in (bible or {}).get("personas") or []:
        if not isinstance(persona, dict):
            continue
        name = str(persona.get("name") or "").strip()
        if not name:
            continue
        cast[name] = {
            "identity": persona.get("identity") or {"role": name},
            "drives": persona.get("drives") or {},
            "voice": persona.get("voice") or {},
        }
    if not cast:
        raise ProductionStopped("世界书未登记人物")
    return cast


def seal_chapter_review(run: Any, production: dict[str, Any]) -> None:
    """审校只登记通过和正文哈希，不改正文、不抽事实。"""
    text = str(run.content or "")
    digest = _content_hash(text)
    if production.get("takes"):
        take = production["takes"][0]
        take["status"] = "ACCEPTED"
        take["validation"] = {"passed": True, "issues": [], "facts": []}
    production["accepted"] = [0]
    production["review_history"] = [
        {
            "content_hash": digest,
            "passed": True,
            "hard_issues": [],
            "chapter_repairs": int(production.get("repairs") or 0),
        }
    ]
    run.review = {
        "passed": True,
        "hard_issues": [],
        "soft_issues": [],
        "continuity_issues": [],
        "front_gate_issues": [],
    }


async def prepare_chapter_canon(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: Any,
) -> None:
    """抽事实、丢掉对不上正文的摘录，再固化接受边界。"""
    from regent.novel.application.chapter_accept import finalize_chapter_acceptance

    production = deepcopy(run.generation_context["production"])
    content = str(run.content or "")
    fails = check_chapter(
        content,
        ctx=run.generation_context,
        cast=production.get("cast") or {},
        title=str(getattr(run, "title", "") or ""),
        ignore_max_length=overlong_accepted(content, production),
        ignore_min_length=short_accepted(content, production),
    )
    if fails:
        raise ProductionStopped("短链硬门未过：" + "；".join(fails[:4]))
    if not (run.review or {}).get("passed"):
        raise RuntimeError("UNVALIDATED_CHAPTER")
    history = production.get("review_history") or []
    digest = _content_hash(content)
    review_hash = str(history[-1].get("content_hash") or "") if history else ""
    if review_hash != digest:
        raise RuntimeError("UNVALIDATED_CHAPTER")
    from regent.novel.application.production import CallUnknown

    misses = int(production.get("facts_unknown") or 0)
    plan = production.get("chapter_plan") or run.generation_context.get("target_node") or {}
    planned = str(plan.get("result") or "").strip() if isinstance(plan, dict) else ""
    request: dict[str, Any] = {"chapter": content}
    if planned:
        request["planned_result"] = planned
    try:
        extracted = await _call(
            session,
            provider,
            work,
            run,
            production,
            ChapterFacts,
            CHAPTER_FACT_SYSTEM,
            request,
            "chapter_facts",
            f"v{_input_version(run)}:chapter_facts:{digest[:12]}"
            + (":landing" if planned else "")
            + (f":m{misses}" if misses else ""),
        )
    except CallUnknown:
        # 正文已经过了硬门。事实抽取第二次仍拿不到结果时不抽了，不能让写好的章节整章终止。
        production["facts_unknown"] = misses + 1
        if misses < 1:
            _save(run, production)
            raise
        production["facts_skipped"] = True
        _save(run, production)
        extracted = ChapterFacts(facts=[])
    # _call 已把带 call_count 的 production 写回 run；继续用这份字典。
    production = run.generation_context["production"]
    if planned and not production.get("facts_skipped"):
        production["landing"] = {
            "landed": bool(extracted.result_landed),
            "ending": str(extracted.ending or "").strip()[:LANDING_ENDING_MAX_CHARS],
        }
    facts = grounded_facts(content, list(extracted.facts))
    if not production.get("takes"):
        _store_prose(run, production, content)
    production["takes"][0]["validation"] = {"passed": True, "issues": [], "facts": facts}
    production["takes"][0]["status"] = "ACCEPTED"
    production["accepted"] = [0]
    finalize_chapter_acceptance(
        run,
        production,
        node_completed=False,
        review_passed=True,
    )
    _save(run, production)


async def _land_tick(
    session: AsyncSession,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: Any,
    production: dict[str, Any],
    version: int,
) -> None:
    """判这一稿写没写到章纲结果，判完回 CHECK。拿不到结果就当写到了：这一项不能停掉整章。"""
    from regent.novel.application.production import CallUnknown

    content = str(run.content or "")
    digest = _content_hash(content)
    plan = _plan_now(run.generation_context, production)
    misses = int(production.get("landing_unknown") or 0)
    try:
        verdict = await _call(
            session,
            provider,
            work,
            run,
            production,
            LandingVerdict,
            LANDING_SYSTEM,
            {
                "choice": str(plan.get("choice") or ""),
                "planned_result": str(plan.get("result") or ""),
                "chapter": content,
            },
            "h2_landing",
            f"v{version}:h2:landing:{digest[:12]}" + (f":m{misses}" if misses else ""),
        )
    except CallUnknown:
        production["landing_unknown"] = misses + 1
        if misses < 1:
            _save(run, production)
            raise
        verdict = LandingVerdict(landed=True)
    production = run.generation_context["production"]
    production["h2_landing"] = {
        "hash": digest,
        "landed": bool(verdict.landed),
        "missing": str(verdict.missing or "").strip()[:LANDING_ENDING_MAX_CHARS],
        "ending": str(verdict.ending or "").strip()[:LANDING_ENDING_MAX_CHARS],
    }
    production["phase"] = "CHECK"
    _save(run, production)


async def produce_chapter_tick(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: Any,
    production: dict[str, Any],
) -> bool:
    """一 tick 至多一次模型调用。返回 True 表示 PRODUCE 可以结束。"""
    _ensure_production(run, production)
    phase = str(production.get("phase") or "WRITE")
    if phase == "DONE":
        return True
    if phase not in CHAPTER_PHASES and phase not in H2_PHASES:
        raise ProductionStopped(f"未知短链阶段: {phase}")
    if mainline_exhausted(run.generation_context):
        raise ProductionStopped("主线用尽，先扩卷")
    if not production.get("cast"):
        production["cast"] = await load_cast(session, work, run.generation_context)
    cast = production["cast"]
    ctx = run.generation_context or {}
    version = _input_version(run)

    if phase == "PLAN" or (
        phase == "WRITE" and ctx.get("book_rhythm") and not production.get("chapter_plan")
    ):
        rhythm = ctx.get("book_rhythm") if isinstance(ctx.get("book_rhythm"), dict) else {}
        if arc_review_due(ctx, production):
            request = arc_review_payload(ctx, production)
            review = await _call(
                session,
                provider,
                work,
                run,
                production,
                ArcReview,
                ARC_REVIEW_SYSTEM,
                request,
                "chapter_arc_review",
                f"v{version}:chapter_arc:"
                f"{_content_hash(json.dumps(request, ensure_ascii=False, sort_keys=True))[:12]}",
                temperature=0,
            )
            production = run.generation_context["production"]
            production["arc_review"] = review.model_dump()
            production["arc_checked"] = True
            production["phase"] = "PLAN"
            _save(run, production)
            return False
        plan_calls = int(production.get("plan_calls") or 0)
        earlier = previous_plans(ctx)
        stages = [str(s) for s in rhythm.get("stages") or []]
        stage_index = (
            stage_position(earlier, stages, cap=max_chapters_per_stage(rhythm))[0]
            if stages
            else None
        )
        request = plan_payload(ctx, cast, production)
        digest = _content_hash(json.dumps(request, ensure_ascii=False, sort_keys=True))
        planned = await _call(
            session,
            provider,
            work,
            run,
            production,
            ChapterPlan,
            CHAPTER_PLAN_SYSTEM,
            request,
            "chapter_plan",
            f"v{version}:chapter_plan:{plan_calls}:{digest[:12]}",
            temperature=0 if plan_calls == 0 else 0.7,
        )
        production = run.generation_context["production"]
        production["plan_calls"] = plan_calls + 1
        node = _plan_node(
            planned,
            node_id=str((ctx.get("target_node") or {}).get("id") or f"ch{getattr(run, 'chapter_no', 1)}"),
            stage_index=stage_index,
        )
        if isinstance(production.get("arc_review"), dict):
            node["arc_review"] = production["arc_review"]
        chapter_no = int(getattr(run, "chapter_no", 0) or 0) or None
        stall = place_stall_reason(node, ctx, production)
        repeats = repeated_scene(node, earlier, chapter_no=chapter_no)
        mismatch = rhythm_mismatch(
            node,
            rhythm,
            earlier,
            chapter_no=chapter_no,
            review=production.get("arc_review") if isinstance(production.get("arc_review"), dict) else None,
        )
        reason = "；".join(item for item in (stall, mismatch) if item)
        out_of_plans = plan_calls + 1 >= MAX_PLAN_CALLS
        if not (stall or repeats) and mismatch:
            production["plan_moved"] = {"node": node, "reason": mismatch}
        elif (stall or repeats) and out_of_plans and isinstance(production.get("plan_moved"), dict):
            # 节奏没对上还能带着警告写；原地再写一章、把写过的一场再写一遍不行：换回没有这两样的那一稿
            node = dict(production["plan_moved"]["node"])
            reason = str(production["plan_moved"]["reason"])
        elif repeats and out_of_plans:
            node["already_written"] = f"{repeats}：这一章从那一场之后写起，那一场里的动作和对白不再写"
        if reason and not out_of_plans:
            production["plan_reject"] = reason
            production["plan_rejected"] = {
                key: node[key] for key in ("pressure", "choice", "result")
            }
            production["phase"] = "PLAN"
            _save(run, production)
            return False
        if reason:
            production.setdefault("plan_warnings", []).append(reason)
            settle_stage_claim(
                node,
                rhythm,
                earlier,
                production.get("arc_review") if isinstance(production.get("arc_review"), dict) else None,
            )
        production["chapter_plan"] = node
        production["plan_reject"] = ""
        production["plan_rejected"] = {}
        if (
            (earlier or stages)
            and not out_of_plans
            and int(production.get("method_rounds") or 0) < MAX_METHOD_ROUNDS
        ):
            production["phase"] = "METHOD"
        else:
            production["phase"] = "WRITE"
            run.generation_context["target_node"] = node
        _save(run, production)
        return False

    if phase == "METHOD":
        node = production.get("chapter_plan") if isinstance(production.get("chapter_plan"), dict) else {}
        methods = [str(item).strip() for item in (node.get("used_methods") or []) if str(item).strip()]
        if not methods:
            methods = [
                str(item.get("choice") or "").strip()
                for item in previous_plans(ctx)
                if str(item.get("choice") or "").strip()
            ]
        stage_view = plan_payload(ctx, cast, production)
        later = list(stage_view.get("later_stages") or [])
        arc_end = str((stage_view.get("book_rhythm") or {}).get("arc_end") or "")
        notes = stage_view.get("editor_notes") or {}
        if (not methods and not later and not arc_end and not notes) or int(
            production.get("method_rounds") or 0
        ) >= MAX_METHOD_ROUNDS:
            production["phase"] = "WRITE"
            run.generation_context["target_node"] = node
            _save(run, production)
            return False
        request = {
            "used_methods": methods,
            "current_stage": stage_view.get("current_stage") or "",
            "later_stages": later,
            "arc_end": arc_end,
            "editor_notes": notes,
            "pressure": node.get("pressure") or "",
            "choice": node.get("choice") or "",
            "result": node.get("result") or "",
        }
        verdict = await _call(
            session,
            provider,
            work,
            run,
            production,
            MethodVerdict,
            METHOD_JUDGE_SYSTEM,
            request,
            "chapter_method",
            f"v{version}:chapter_method:{production.get('method_rounds') or 0}:"
            f"{_content_hash(json.dumps(request, ensure_ascii=False, sort_keys=True))[:12]}",
            temperature=0,
        )
        production = run.generation_context["production"]
        node = production.get("chapter_plan") if isinstance(production.get("chapter_plan"), dict) else node
        reasons: list[str] = []
        if verdict.same_method and methods:
            matched = str(verdict.matched or "").strip()
            reasons.append("这一章的选择是已经用过的同一种手段" + ("：" + matched if matched else ""))
        if verdict.skips_ahead and (later or arc_end):
            skipped = str(verdict.skipped or "").strip()
            reasons.append(
                "这一章的结果提前写出了后面的事"
                + ("：" + skipped if skipped else "")
                + "。结果只朝 current_stage 推进，后面的事和答案留到后面的章节"
            )
        if verdict.ignores_notes and notes:
            ignored = str(verdict.ignored or "").strip()
            reasons.append("没有做到编辑意见" + ("：" + ignored if ignored else ""))
        if verdict.same_method and methods:
            production["same_method_rejects"] = int(production.get("same_method_rejects") or 0) + 1
        if reasons:
            production["plan_reject"] = "；".join(reasons)
            production["plan_rejected"] = {
                key: node.get(key) for key in ("pressure", "choice", "result")
            }
            production["method_rounds"] = int(production.get("method_rounds") or 0) + 1
            production.pop("chapter_plan", None)
            production["phase"] = "PLAN"
            _save(run, production)
            return False
        production["phase"] = "WRITE"
        run.generation_context["target_node"] = node
        _save(run, production)
        return False

    if phase == "WRITE":
        planned = production.get("chapter_plan")
        if isinstance(planned, dict) and planned.get("choice"):
            run.generation_context["target_node"] = planned
            ctx = run.generation_context
        rewrites = int(production.get("rewrites") or 0)
        h2_round = int(production.get("h2_round") or 0)
        if _is_h2(production):
            config = h2_config(ctx, production)
            payload = h2_write_payload(ctx, cast, config)
            system = h2_writer_system(ctx, cast, config, production)
            production["h2_effective_hash"] = config.content_hash
        else:
            payload = build_write_payload(ctx, cast)
            system = _writer_system(ctx, cast)
        if rewrites:
            payload["avoid"] = list(production.get("rewrite_fails") or [])
        written = await _call(
            session,
            provider,
            work,
            run,
            production,
            ChapterProse,
            system
            + (" 上一稿因 avoid 里的问题没过，这一稿不要再出现。" if rewrites else ""),
            payload,
            "chapter_write",
            f"v{version}:chapter_write"
            + (f":w{rewrites}" if rewrites else "")
            + (f":h{h2_round}" if h2_round else ""),
        )
        production = run.generation_context["production"]
        accept_draft(run, production, ctx=ctx, draft=written.content, tag=f"write:w{rewrites}")
        _save(run, production)
        return False

    if phase == "FIT":
        await fit_tick(
            session,
            provider=provider,
            work=work,
            run=run,
            production=production,
            cast=cast,
            ctx=ctx,
            version=version,
        )
        return False

    if phase == "CHECK":
        content = str(run.content or "")
        fails = check_chapter(
            content,
            ctx=ctx,
            cast=cast,
            title=str(getattr(run, "title", "") or ""),
        )
        warnings = name_warnings(content, cast)
        cut = str(production.get("fit_truncated") or "")
        if cut:
            # 确定性硬门：删段没删回来、章末被截掉过，就不能当没事入正史
            fails.append(f"删段后仍超长，章末被截掉{len(cut)}字，本章结果可能没落地")
        h2 = _is_h2(production)
        # 首过只看硬门，两臂同一个口径：软警告会把这一章送进改稿，但改过稿不等于
        # 首稿没过门。口径不一致会让默认臂的基线被人名警告压低，试用判定就偏向采纳。
        production.setdefault("first_pass", not fails)
        if h2:
            from regent.novel.harnessing import recursion

            warnings += [
                item
                for item in h2_soft_issues(content, ctx, h2_config(ctx, production))
                if item not in warnings
            ]
        repairs = int(production.get("repairs") or 0)
        production["check_fails"] = fails + warnings
        if h2:
            if not fails and h2_landing_due(content, ctx, production):
                production["phase"] = "LAND"
                _save(run, production)
                return False
            stall = [] if fails else h2_stall_issues(content, ctx, production)
            trigger = recursion.round_trigger(production, fails=fails, stall=stall)
            if trigger:
                recursion.start_round(
                    run, production, trigger=trigger, fails=fails, soft=warnings, stall=stall
                )
                _save(run, production)
                return False
        if not fails and (not warnings or repairs >= 1):
            production["soft_issues"] = warnings
            production["phase"] = "DONE"
            if h2:
                from regent.novel.harnessing.overlay import record_verified_round

                await record_verified_round(session, work=work, run=run, production=production)
            _save(run, production)
            return True
        digest = _content_hash(content)
        if repairs >= 1 or production.get("last_failed_hash") == digest:
            rewrites = int(production.get("rewrites") or 0)
            if rewrites >= MAX_REWRITES:
                if not cuts_overlong(ctx, production) and all(_overlong_fail(item) for item in fails):
                    # 递归、改稿、重写都没压回来：只差字数就原样入正史并记账，
                    # 不删段不砍尾，也不为字数停掉整章
                    production["overlong_accepted"] = len(content)
                    production["soft_issues"] = warnings
                    production["phase"] = "DONE"
                    _save(run, production)
                    return True
                if len(content.strip()) >= SHORT_ACCEPT_MIN_CHARS and all(
                    _short_fail(item) for item in fails
                ):
                    production["short_accepted"] = len(content)
                    production["soft_issues"] = warnings
                    production["phase"] = "DONE"
                    _save(run, production)
                    return True
                raise ProductionStopped(
                    "短链重写后仍未通过硬门：" + "；".join(str(item) for item in fails[:4])
                )
            production["rewrites"] = rewrites + 1
            production["rewrite_fails"] = fails
            production["repairs"] = 0
            production["last_failed_hash"] = ""
            production["phase"] = "WRITE"
            _save(run, production)
            return False
        production["last_failed_hash"] = digest
        production["phase"] = "REPAIR"
        _save(run, production)
        return False

    if phase == "LAND":
        if not _is_h2(production):
            raise ProductionStopped(f"非 H2 臂不走递归阶段: {phase}")
        await _land_tick(session, provider, work, run, production, version)
        return False

    if phase in H2_PHASES:
        if not _is_h2(production):
            raise ProductionStopped(f"非 H2 臂不走递归阶段: {phase}")
        from regent.novel.harnessing import recursion

        handler = {
            "PROPOSE": recursion.propose_tick,
            "PROBE": recursion.probe_tick,
            "COMPOSE": recursion.compose_tick,
            "SCREEN": recursion.screen_tick,
        }[phase]
        await handler(session, provider=provider, work=work, run=run, production=production, cast=cast)
        return False

    if phase == "REPAIR":
        if int(production.get("repairs") or 0) >= 1:
            raise ProductionStopped("短链只允许改稿一次")
        draft = str(run.content or "")
        if _is_h2(production):
            config = h2_config(ctx, production)
            payload = h2_write_payload(ctx, cast, config)
            system = h2_repair_system(config)
        else:
            payload = build_write_payload(ctx, cast)
            system = CHAPTER_REPAIR_SYSTEM
        payload["previous_draft"] = draft
        payload["check_fails"] = list(production.get("check_fails") or [])
        payload["length"] = length_brief(draft)
        cut = str(production.get("fit_truncated") or "")
        if cut:
            # 被截掉的章末原文交给改稿：不给它，改稿只能凭空补一个结局
            payload["cut_tail"] = cut
        written = await _call(
            session,
            provider,
            work,
            run,
            production,
            ChapterProse,
            system,
            payload,
            "chapter_repair",
            f"v{version}:chapter_repair:{_content_hash(draft)[:12]}",
        )
        production = run.generation_context["production"]
        production["repairs"] = int(production.get("repairs") or 0) + 1
        accept_draft(run, production, ctx=ctx, draft=written.content, tag="repair")
        _save(run, production)
        return False

    raise ProductionStopped(f"未知短链阶段: {phase}")


def _is_h2(production: dict[str, Any]) -> bool:
    return str(production.get("executor") or "") == H2_ARCHITECTURE


def cuts_overlong(ctx: dict[str, Any] | None, production: dict[str, Any]) -> bool:
    """超长稿是否走删段；H2 臂的新运行交给递归，不删。"""
    return not (_is_h2(production) and (ctx or {}).get("overlong") == OVERLONG_MARK)


def _overlong_fail(fail: str) -> bool:
    return str(fail).startswith(f"正文超过{MAX_CHAPTER_CHARS}字")


def overlong_accepted(content: str, production: dict[str, Any]) -> bool:
    """这一稿是 CHECK 兜底原样收下的超长稿：审校与入库不再按字数拦它。"""
    accepted = int(production.get("overlong_accepted") or 0)
    return accepted > 0 and accepted == len(content or "")


def _short_fail(fail: str) -> bool:
    return str(fail) == f"正文不足{MIN_CHAPTER_CHARS}字"


def short_accepted(content: str, production: dict[str, Any]) -> bool:
    """这一稿是 CHECK 兜底原样收下的不足字数稿：审校与入库不再按下限拦它。"""
    accepted = int(production.get("short_accepted") or 0)
    return accepted > 0 and accepted == len(content or "")
