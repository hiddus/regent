"""可恢复的小说章节 Agent loop。

ASSEMBLE 编译章节级增量任务、近期章节和 Canon；REVIEW 的修订稿必须复审，
任何硬规则失败都不会因篇幅达标而放行。所有章节（包括第一章）都会写入 Canon。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.model.provider import StructuredModelResponse
from regent.novel.application import executor as executor_app
from regent.novel.application import memory as memory_app
from regent.novel.domain import memory as memory_domain
from regent.novel.domain.price_book import PRICE_BOOK_VERSION, actual_minor
from regent.novel.domain.quality import ChapterEvidence, template_density
from regent.novel.domain.states import ChapterRunState, ChapterStep
from regent.novel.infrastructure.models import (
    CanonCommitModel,
    ChapterRunModel,
    CostEntryModel,
    CriticalNodeModel,
    CriticalPathModel,
    InformationSetModel,
    ModelCallModel,
    PersonaSpecModel,
    StoryGoalModel,
    StoryWorkModel,
    VolumeModel,
)


class StoryOutlineNode(BaseModel):
    """大纲节点：LLM 根据 premise + 方向卡生成。"""
    title: str = Field(description="节点标题，必须具体到本作的角色/地名/事件")
    node_type: str = Field(description="INCITING/ESCALATION/REVELATION/REVERSAL/CLIMAX/BETRAYAL/DEATH/WAR/RESOLUTION")
    promise: str = Field(default="", description="本章承诺给读者的体验（如：主角首次碾压、世界观第一次揭示）")
    preconditions: list[str] = Field(default_factory=list, description="前置条件（如：主角已觉醒金手指）")
    consequences: list[str] = Field(default_factory=list, description="后续影响（如：引来更强势力注意）")


class StoryOutline(BaseModel):
    """LLM 生成的定制化故事大纲。"""
    volume_title: str = Field(description="首卷标题，必须体现本作特色")
    cultivation_realm: str = Field(default="", description="首卷修炼境界范围")
    volume_goal: str = Field(default="", description="首卷整体目标")
    arc_titles: list[str] = Field(default_factory=list, description="弧段标题列表（3-5 个弧段）")
    nodes: list[StoryOutlineNode] = Field(min_length=10, max_length=16, description="关键路径节点（10-16 个）")
    personas: list[dict[str, str]] = Field(
        default_factory=list,
        description="本作核心角色（3-5 个），每个包含 name/identity/drive/voice",
    )


class Performance(BaseModel):
    persona: str
    immediate_goal: str
    private_reasoning: str
    actions: list[str] = Field(min_length=1, max_length=6)
    dialogue: list[str] = Field(default_factory=list, max_length=8)
    emotional_shift: str


class CharacterAction(BaseModel):
    persona: str = Field(description="角色名")
    scene_actions: list[str] = Field(min_length=1, max_length=6, description="本章具体行动")
    emotional_arc: str = Field(default="", description="情绪变化轨迹")
    key_dialogue: list[str] = Field(default_factory=list, max_length=4, description="关键台词")


class DirectorPlan(BaseModel):
    scene_goal: str
    character_actions: list[CharacterAction] = Field(default_factory=list, description="每个角色在本章的行动规划")
    beats: list[str] = Field(min_length=3, max_length=10)
    state_before: dict[str, str] = Field(default_factory=dict, description="本章开局的持久状态")
    state_after: dict[str, str] = Field(default_factory=dict, description="本章结尾的持久状态，至少两项发生变化")
    allowed_revelations: list[str] = Field(default_factory=list)
    forbidden_revelations: list[str] = Field(default_factory=list)
    ending_hook: str


class ChapterDraft(BaseModel):
    title: str
    content: str = Field(min_length=800)


class ChapterReview(BaseModel):
    passed: bool
    continuity_issues: list[str] = Field(default_factory=list)
    leakage_issues: list[str] = Field(default_factory=list)
    prose_issues: list[str] = Field(default_factory=list)
    revision_instructions: list[str] = Field(default_factory=list)


class CanonExtraction(BaseModel):
    facts: list[dict[str, Any]] = Field(default_factory=list, max_length=40)


def _visible_performances(run: ChapterRunModel) -> list[dict[str, Any]]:
    """导演和成文器只能看到角色外显行为，不能看到私密推理。"""
    return [
        {key: value for key, value in performance.items() if key != "private_reasoning"}
        for performance in (run.performances or [])
    ]


def _chapter_assignment(chapter_no: int) -> dict[str, Any]:
    """把一个路径节点的三章拆成不同职责，防止每章重演同一宏观目标。"""
    position = (chapter_no - 1) % 3
    assignments = (
        ("进入与施压", "承接上一章结果，引入本节点的新阻力并迫使主角作出选择", "局势发生不可逆变化"),
        ("升级与转向", "放大选择的代价，让对抗升级并揭示一条新信息", "主角改变策略或关系发生变化"),
        ("兑现与转场", "兑现节点承诺，结算核心冲突并制造下一节点的因果入口", "本节点不得留在原地循环"),
    )
    role, objective, required_change = assignments[position]
    return {"position_in_node": position + 1, "role": role, "objective": objective,
            "required_state_change": required_change, "must_not_repeat_previous_chapter": True}


def _normalized_prose(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text).lower()


def _repetition_score(left: str, right: str, *, width: int = 12) -> float:
    """字符 shingles 的包含率；可抓住换标点、轻微改写后的跨章复写。"""
    a, b = _normalized_prose(left), _normalized_prose(right)
    if len(a) < width or len(b) < width:
        return 0.0
    sa = {a[i:i + width] for i in range(len(a) - width + 1)}
    sb = {b[i:i + width] for i in range(len(b) - width + 1)}
    return len(sa & sb) / max(1, min(len(sa), len(sb)))


def _hard_quality_issues(content: str, recent_chapters: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    if len(_normalized_prose(content)) < 800:
        issues.append("正文有效字符少于 800")
    for chapter in recent_chapters:
        score = _repetition_score(content, str(chapter.get("content", "")))
        if score >= 0.18:
            issues.append(f"与第{chapter.get('chapter_no')}章存在高比例复写（{score:.0%}）")
    window = [
        ChapterEvidence(chapter_no=int(chapter.get("chapter_no", 0)), text=str(chapter.get("content", "")))
        for chapter in reversed(recent_chapters[:2])
    ] + [ChapterEvidence(chapter_no=0, text=content)]
    templates = template_density(window)
    if not templates.passed:
        evidence = "、".join(templates.evidence[:5])
        issues.append(f"近三章模板套句密度过高（{templates.score}/万字）：{evidence}")
    return issues


def _structural_quality_issues(run: ChapterRunModel) -> list[str]:
    """检查章节是否真的推进，而不把字数或模型自评当作推进。"""
    plan = run.generation_context.get("director_plan", {})
    before = plan.get("state_before", {}) if isinstance(plan, dict) else {}
    after = plan.get("state_after", {}) if isinstance(plan, dict) else {}
    changed = [key for key in set(before) | set(after) if before.get(key) != after.get(key)]
    issues: list[str] = []
    if len(changed) < 2:
        issues.append("本章持久状态变化少于两项，情节没有形成足够净推进")
    recent = run.generation_context.get("recent_chapters", [])
    if recent:
        previous_plan = recent[0].get("director_plan", {})
        previous_after = previous_plan.get("state_after", {}) if isinstance(previous_plan, dict) else {}
        conflicts = [
            key for key in set(previous_after) & set(before)
            if previous_after[key] != before[key]
        ]
        if conflicts:
            issues.append("本章开局未继承上一章结局状态：" + "、".join(sorted(conflicts)))
    return issues


async def _record_call(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    run: ChapterRunModel,
    step: str,
    purpose: str,
    response: StructuredModelResponse,
    system_prompt: str = "",
    user_prompt: str = "",
) -> None:
    """Record model call metadata and cost entry after each generation."""
    prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
    context_hash = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()
    output_json = json.dumps(response.output.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, default=str)
    output_hash = hashlib.sha256(output_json.encode("utf-8")).hexdigest()
    # 同一步可能在审校回路中多次调用；输出 hash 既保留每次证据，也让重放幂等。
    logical_call_id = f"{run.id}:{step}:{purpose}:{output_hash[:16]}"
    existing = await session.scalar(
        select(ModelCallModel).where(ModelCallModel.logical_call_id == logical_call_id)
    )
    if existing is not None:
        return

    session.add(
        ModelCallModel(
            id=uuid.uuid4(),
            logical_call_id=logical_call_id,
            work_id=work.id,
            run_id=run.id,
            chapter_no=run.chapter_no,
            step=step,
            purpose=purpose,
            provider="openai_compatible",
            model=response.model,
            prompt_hash=prompt_hash,
            context_hash=context_hash,
            status="SUCCEEDED",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cached_input_tokens=getattr(response.usage, "cached_input_tokens", 0) or 0,
            output_hash=output_hash,
            price_book_version=PRICE_BOOK_VERSION,
            usage_source="provider",
            currency="CNY",
        )
    )
    # 成本来自版本化价格本（§6），不再使用调用点硬编码单价。
    # legacy 路径在调用后才记账，因此补一次等额预留并立即结算，保持两段式账本。
    cost_minor = actual_minor(
        response.model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cached_input_tokens=getattr(response.usage, "cached_input_tokens", 0) or 0,
    )
    # legacy 路径在调用之后才记账，无法在调用前形成上限——这正是 v2 存在的原因之一。
    # 因此这里只按价格本落一条 CONSUME 流水，不做事后补预留的假动作。
    session.add(
        CostEntryModel(
            id=uuid.uuid4(),
            work_id=work.id,
            chapter_no=run.chapter_no,
            step=step,
            logical_call_id=logical_call_id,
            funding_pool="platform",
            funding_source="platform_grant",
            amount_minor=cost_minor,
            currency="CNY",
            entry_kind="CONSUME",
            price_book_version=PRICE_BOOK_VERSION,
        )
    )
    await session.flush()


async def _latest_goal(session: AsyncSession, work_id: uuid.UUID) -> StoryGoalModel:
    goal = await session.scalar(
        select(StoryGoalModel)
        .where(StoryGoalModel.work_id == work_id)
        .order_by(StoryGoalModel.version.desc())
        .limit(1)
    )
    if goal is None:
        raise RuntimeError("story goal missing")
    return goal


async def _current_volume_no(session: AsyncSession, work: StoryWorkModel) -> int:
    """当前卷号：优先 ACTIVE，其次编号最大的一卷。"""
    current = await session.scalar(
        select(VolumeModel)
        .where(
            VolumeModel.work_id == work.id,
            VolumeModel.state == "ACTIVE",
        )
        .order_by(VolumeModel.volume_no.desc())
        .limit(1)
    )
    if current is not None:
        return int(current.volume_no)
    latest = await session.scalar(
        select(VolumeModel)
        .where(VolumeModel.work_id == work.id)
        .order_by(VolumeModel.volume_no.desc())
        .limit(1)
    )
    return int(latest.volume_no) if latest is not None else 1


def _current_entities(canon: list[dict[str, Any]]) -> list[str]:
    """从当前 Canon 事实里取实体，作为记忆召回的按需线索。

    只取最近若干条的实体：召回是为了「这一章用得上什么」，不是把全书实体
    都塞进去。
    """
    entities: list[str] = []
    for fact in canon[-40:]:
        values = fact.get("entities") if isinstance(fact, dict) else None
        if isinstance(values, (list, tuple)):
            entities.extend(str(v) for v in values if str(v).strip())
    seen: set[str] = set()
    unique = [e for e in entities if not (e in seen or seen.add(e))]
    return sorted(unique)[:20]


def tag_facts(
    facts: list[Any], *, volume_no: int, chapter_no: int
) -> list[dict[str, Any]]:
    """给事实打上卷/章标签（P1-3）。

    没有标签就无法按卷切分，长篇只能退化成「最近 N 条」——那样既会把前几卷的
    细节搬进当前卷，也会把当前卷的关键事实挤出上下文。
    """
    tagged: list[dict[str, Any]] = []
    for fact in facts:
        item = dict(fact) if isinstance(fact, dict) else fact.model_dump(mode="json")
        item.setdefault("volume_no", int(volume_no))
        item.setdefault("chapter_no", int(chapter_no))
        tagged.append(item)
    return tagged


async def _canon_facts(
    session: AsyncSession, work: StoryWorkModel, run: ChapterRunModel | None = None,
) -> list[dict[str, Any]]:
    """三级 Canon 上下文（30 万字架构）：
    - 当前卷：全量事实（按 volume_no 过滤，上限 120 条）
    - 前 1-2 卷：每卷摘要（VolumeModel.summary），每卷 <= 20 条
    - 更早卷 / 全局：世界观级摘要，<= 15 条

    历史事实可能没有 volume_no 标签。直接按卷过滤会在标签缺失时静默清空上下文，
    因此缺失标签的事实按当前卷处理；新提交的事实一律由 ``tag_facts`` 打标签。
    """
    rows = await session.scalars(
        select(CanonCommitModel)
        .where(
            CanonCommitModel.work_id == work.id,
            CanonCommitModel.branch_id == work.branch_id,
        )
        .order_by(CanonCommitModel.version.desc())
        .limit(20)
    )
    accepted_hashes = None
    if run is not None and (run.generation_context or {}).get("architecture_version") == "director_v2":
        accepted_rows = (await session.scalars(select(ChapterRunModel).where(
            ChapterRunModel.work_id == work.id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.chapter_no < run.chapter_no,
            ChapterRunModel.state == ChapterRunState.CANONIZED.value,
        ).order_by(ChapterRunModel.chapter_no, ChapterRunModel.attempt.desc()))).all()
        accepted_hashes = set()
        seen = set()
        for accepted in accepted_rows:
            if accepted.chapter_no not in seen:
                seen.add(accepted.chapter_no)
                accepted_hashes.add(hashlib.sha256(accepted.content.encode()).hexdigest())
    all_facts: list[dict[str, Any]] = []
    for row in reversed(rows.all()):
        if accepted_hashes is not None and row.source_hash not in accepted_hashes:
            continue
        all_facts.extend(row.facts or [])
    current_volume_no = await _current_volume_no(session, work)
    in_volume = [
        fact
        for fact in all_facts
        if int(fact.get("volume_no", current_volume_no) or current_volume_no)
        == current_volume_no
    ]
    # 标签整体缺失时退回全量：宁可多带，也不能把上下文清空到无法创作。
    current_volume_facts = (in_volume or all_facts)[-120:]

    # --- 第二级：已完成卷摘要 ---
    prev_volume_summaries: list[dict[str, Any]] = []
    completed_vols = list(
        (await session.scalars(
            select(VolumeModel).where(
                VolumeModel.work_id == work.id,
                VolumeModel.state == "COMPLETED",
            ).order_by(VolumeModel.volume_no.desc())
        )).all()
    )
    for vol in completed_vols[:2]:  # 前 1-2 卷
        vol_summary = list(vol.summary or [])
        if vol_summary:
            prev_volume_summaries.extend(vol_summary[:20])
        else:
            # 没有预存摘要时，从该卷章节的 Canon 中提取关键事实
            vol_facts = [f for f in all_facts if f.get("volume_no") == int(vol.volume_no)]
            prev_volume_summaries.extend(vol_facts[:20])

    # --- 第三级：世界观级摘要（全局） ---
    world_state: list[dict[str, Any]] = []
    for f in all_facts:
        if f.get("confidence", "high") == "high" and f.get("entities"):
            world_state.append(f)
            if len(world_state) >= 15:
                break

    return current_volume_facts + prev_volume_summaries + world_state


class ClarifyQuestions(BaseModel):
    """LLM 动态生成的澄清问题。"""
    questions: list[dict[str, Any]] = Field(
        min_length=1,
        max_length=3,
        description="2-3 个澄清问题，每个包含 question_id, prompt, options, default_assumption",
    )


async def generate_clarify_questions(
    provider: ModelProvider,
    *,
    raw_intent: str,
    genre: str,
) -> list[dict[str, Any]]:
    """根据用户 premise 动态生成澄清问题，而非使用写死模板。"""
    sys_prompt = (
        "你是小说编辑。阅读用户的故事前提，找出最关键的不确定因素，生成 2-3 个澄清问题。\n"
        "要求：\n"
        "1. 问题必须针对这篇故事的具体内容——不要问通用问题\n"
        "2. 每个问题的选项必须贴合故事背景，不要用泛泛的选项\n"
        "3. 问题应该帮助确定故事方向、核心冲突或主角动机\n"
        "4. 如果前提已经足够详细，只生成 1 个最关键的问题\n"
        "5. 每个问题必须包含：question_id（英文标识）、prompt（问题文本）、options（3-4 个选项）、default_assumption（默认值）\n"
    )
    usr_prompt = json.dumps({
        "raw_intent": raw_intent,
        "genre": genre or "未指定",
    }, ensure_ascii=False)
    response = await provider.generate_structured(
        system_prompt=sys_prompt,
        user_prompt=usr_prompt,
        response_model=ClarifyQuestions,
    )
    return response.output.questions


class DirectionCards(BaseModel):
    """LLM 动态生成的方向卡。"""
    cards: list[dict[str, str]] = Field(
        min_length=2,
        max_length=3,
        description="2-3 张方向卡，每张包含 card_id, title, protagonist_desire, core_conflict, genre_promise, pacing, differentiator",
    )


async def generate_direction_cards(
    provider: ModelProvider,
    *,
    raw_intent: str,
    genre: str,
    answers: dict[str, str],
) -> list[dict[str, str]]:
    """根据用户 premise + 澄清答案，动态生成方向卡。"""
    sys_prompt = (
        "你是小说策划编辑。根据用户的故事前提和澄清答案，设计 2-3 个截然不同的故事方向。\n"
        "要求：\n"
        "1. 每张卡必须针对这篇故事的具体内容——标题、描述都要贴合 premise\n"
        "2. 卡片之间差异必须明显：不同的叙事套路、不同的爽点类型、不同的节奏\n"
        "3. 混搭多种套路，拒绝单一公式（如纯打脸、纯升级）\n"
        "4. 每张卡必须包含：card_id（英文标识，如 card-mystery）、title（题材+流派名）、"
        "protagonist_desire（主角想要什么+转折）、core_conflict（核心阻力+特色）、"
        "genre_promise（给读者的体验承诺）、pacing（阅读节奏）、differentiator（与其他卡的差异点）\n"
        "5. 方向卡的描述要具体到这篇故事的角色、设定、冲突——不要用泛泛的模板语言\n"
    )
    usr_prompt = json.dumps({
        "raw_intent": raw_intent,
        "genre": genre or "未指定",
        "clarify_answers": answers,
    }, ensure_ascii=False)
    response = await provider.generate_structured(
        system_prompt=sys_prompt,
        user_prompt=usr_prompt,
        response_model=DirectionCards,
    )
    return response.output.cards


async def generate_outline(
    provider: ModelProvider,
    *,
    raw_intent: str,
    genre: str,
    direction_title: str,
    protagonist_desire: str,
    core_conflict: str,
    genre_promise: str,
) -> StoryOutline:
    """根据 premise + 方向卡生成定制化故事大纲。

    替代硬编码的路径节点模板，让 LLM 根据用户的具体设定
    生成定制化的路径节点、角色、卷结构。
    """
    sys_prompt = (
        "你是小说架构师。根据用户的故事前提和选定方向，设计故事大纲。\n"
        "要求：\n"
        "1. 节点标题必须具体到角色名、地名、事件\n"
        "2. 每个节点有 promise（读者体验）和 consequences（后续影响）\n"
        "3. 节点间有因果链，不是独立事件\n"
        "4. 角色具体到名字和身份\n"
        "5. 混搭多种套路，拒绝单一公式\n"
        "6. 10-16 个节点，3-5 个弧段，3-5 个角色\n"
    )
    usr_prompt = json.dumps({
        "raw_intent": raw_intent,
        "genre": genre,
        "direction": direction_title,
        "protagonist_desire": protagonist_desire,
        "core_conflict": core_conflict,
        "genre_promise": genre_promise,
    }, ensure_ascii=False)
    response = await provider.generate_structured(
        system_prompt=sys_prompt,
        user_prompt=usr_prompt,
        response_model=StoryOutline,
    )
    return response.output


if TYPE_CHECKING:  # 仅用于类型注解：运行时在函数内导入，避免循环导入
    from regent.novel.application.direction import EndingVerdict


async def generate_ending_verdict(
    provider: ModelProvider,
    *,
    session: AsyncSession,
    work: StoryWorkModel,
    run: ChapterRunModel,
    raw_intent: str,
    genre: str,
    ending_statement: str,
    volume_no: int,
    latest_chapter_no: int,
    completed_nodes: list[dict[str, Any]],
    accepted_text: str = "",
    verified_facts: Sequence[dict[str, Any]] = (),
    open_promises: Sequence[dict[str, Any]] = (),
) -> EndingVerdict:
    """让导演判断用户认可的终局是否已经达成（B-05 / C-05）。

    只在用户没有给出显式终局（目标卷数/结局描述）时调用：**用户说了写三卷，第三
    卷写完就是写完，不必再问模型**。模型是用来补位的，不是用来覆盖用户意图的。

    **判断只能依据已经写出来的东西**（C-05）。路径节点的 title/promise 是「计划」
    不是「成品」：只给它看计划，模型就会在计划写完时宣布故事讲完了，而正文里可能
    一个人物的下落都没交代、一串伏笔还挂在那儿。因此请求必须带上最近已接受的
    正文、已核验事实，尤其是**仍未兑现的承诺**——有没收的线，看的是这个。

    调用走 CallBroker：与场景调用同样的预算、持久化和幂等恢复约束。终局判断是
    一次会改变作品终态的调用，不能因为「它不在六步里」就绕过计费与恢复。
    """
    from regent.novel.application.direction import MAX_COST_MINOR, EndingVerdict, ProductionStopped
    from regent.novel.application.production import CallBroker

    sys_prompt = (
        "你是小说的总导演。判断用户最初想讲的这个故事是否已经讲完。\n"
        "判断依据只能是用户意图与已经写出的内容：\n"
        "1. 用户原始意图里承诺的核心冲突是否已经解决\n"
        "2. 主要人物的命运是否已经有了交代，而不是悬在半空\n"
        "3. 关键路径最后一个节点的后果是否已经落到正文里\n"
        "4. open_promises 里还有没收的线，就意味着没讲完\n"
        "5. 已经讲完就回答 story_complete=true；明显还有未收的线就回答 false\n"
        "reason 必须引用 accepted_text / verified_facts 里的具体内容，不得空泛表态。\n"
    )
    usr_prompt = json.dumps({
        "raw_intent": raw_intent,
        "genre": genre,
        "ending_statement": ending_statement,
        "volume_no": volume_no,
        "latest_chapter_no": latest_chapter_no,
        "completed_nodes": completed_nodes[-8:],
        # 已经写出来的东西：终局判断据此，而不是据此的计划（C-05）
        "accepted_text": accepted_text[-4000:],
        "verified_facts": [
            str(f.get("statement", "")) for f in verified_facts
        ][-40:],
        "open_promises": [str(p.get("content", "")) for p in open_promises][-20:],
    }, ensure_ascii=False)
    # D-04：终局裁决同样受章级货币上限约束。它能改变作品终态，不能成为
    # 绕过预算的旁路——预算按「本章已结算金额」扣减（与导演 _call_batch 同一
    # 口径），耗尽时在调用 provider 前拒绝。拒绝语义是「没有判断」而不是
    # 「判断为没讲完」：调用方把异常折算成 None，扩卷不会被预算故障触发。
    production_state = dict((run.generation_context or {}).get("production", {}) or {})
    committed = int(production_state.get("committed_minor", 0) or 0)
    remaining = MAX_COST_MINOR - committed
    if remaining <= 0:
        raise ProductionStopped("终局判断预算已耗尽：不做终局判断")
    broker = CallBroker(lease_owner=f"run:{run.id}", budget_limit_minor=remaining)
    result = await broker.run(
        session,
        provider=provider,
        schema=EndingVerdict,
        work_id=work.id,
        run_id=run.id,
        chapter_no=run.chapter_no,
        step="ENDING",
        purpose="ending_verdict",
        # 幂等键：同一卷同一章的终局判断重试时复用已成功的调用，不重复计费（C-05）
        command_id=f"ending:{volume_no}:{run.chapter_no}",
        system_prompt=sys_prompt,
        user_prompt=usr_prompt,
    )
    return result.output


async def generate_volume_expansion_outline(
    provider: ModelProvider,
    *,
    raw_intent: str,
    genre: str,
    volume_no: int,
    prev_volume_title: str,
    prev_volume_summary: list[dict[str, Any]],
    last_nodes: list[dict[str, Any]],
    cultivation_realm: str,
) -> StoryOutline:
    """为下一卷生成定制化大纲。

    基于上一卷的结局和当前世界状态，生成承上启下的新卷大纲。
    """
    sys_prompt = (
        "你是小说架构师。根据上一卷的结局和当前世界状态，设计下一卷的故事大纲。\n"
        "要求：\n"
        "1. 必须承接上一卷的结局后果——不能重新开始\n"
        "2. 节点标题必须具体到角色名、地名、事件\n"
        "3. 每个节点必须有 promise 和 consequences\n"
        "4. 修炼境界必须递进（不能停留在上一卷的境界）\n"
        "5. 引入新角色、新势力、新谜团，保持新鲜感\n"
        "6. 12-18 个节点，覆盖 3-6 个弧段\n"
    )
    usr_prompt = json.dumps({
        "raw_intent": raw_intent,
        "genre": genre,
        "volume_no": volume_no,
        "prev_volume_title": prev_volume_title,
        "prev_volume_summary": prev_volume_summary[-10:] if prev_volume_summary else [],
        "last_nodes": last_nodes[-5:],
        "current_realm": cultivation_realm,
        "next_realm_hint": "下一境界阶段",
    }, ensure_ascii=False)
    response = await provider.generate_structured(
        system_prompt=sys_prompt,
        user_prompt=usr_prompt,
        response_model=StoryOutline,
    )
    return response.output


async def assemble(
    session: AsyncSession, *, work: StoryWorkModel, run: ChapterRunModel
) -> None:
    architecture_version = (run.generation_context or {}).get("architecture_version", "legacy_v1")
    goal = await _latest_goal(session, work.id)
    path = await session.scalar(
        select(CriticalPathModel)
        .where(CriticalPathModel.work_id == work.id)
        .order_by(CriticalPathModel.version.desc())
        .limit(1)
    )
    nodes: list[CriticalNodeModel] = []
    if path is not None:
        nodes = list(
            (
                await session.scalars(
                    select(CriticalNodeModel)
                    .where(CriticalNodeModel.path_id == path.id)
                    .order_by(CriticalNodeModel.ordinal)
                )
            ).all()
        )
    # 30 万字架构：卷感知节点映射
    # 优先按 volume_no 定位当前章节所属节点，回退到全局 ordinal 计算
    target = None
    # 末节点已完成且没有可扩展的新卷时为 True：整本结束，不再重复生成末节点
    story_complete = False
    current_vol = None
    if nodes:
        from regent.novel.application.works import CHAPTERS_PER_NODE
        # 查找当前章节所属的卷
        current_vol = await session.scalar(
            select(VolumeModel).where(
                VolumeModel.work_id == work.id,
                VolumeModel.start_chapter_no <= run.chapter_no,
                VolumeModel.end_chapter_no >= run.chapter_no,
            )
        )
        if current_vol is not None:
            # 在当前卷内按弧段定位节点
            vol_nodes = [n for n in nodes if n.volume_no == current_vol.volume_no]
            if vol_nodes:
                local_ch = run.chapter_no - int(current_vol.start_chapter_no)
                idx = min(len(vol_nodes) - 1, max(0, local_ch // CHAPTERS_PER_NODE))
                target = vol_nodes[idx]
        if target is None:
            # 回退：全局 ordinal 映射
            target_index = min(len(nodes) - 1, max(0, (run.chapter_no - 1) // CHAPTERS_PER_NODE))
            target = nodes[target_index]
    canon = await _canon_facts(session, work, run)
    # R3：长期记忆按需召回（稳定规则 / 人物弧线 / 承诺与伏笔 / 关系变化）。
    # 未兑现的承诺永不因限额被裁掉；召回结果带摘要，可进入上下文 manifest。
    recalled = await memory_app.recall_memory(
        session,
        work=work,
        chapter_no=int(run.chapter_no),
        entities=_current_entities(canon),
    )
    parent_canon = await session.scalar(select(CanonCommitModel).where(
        CanonCommitModel.work_id == work.id, CanonCommitModel.branch_id == work.branch_id,
    ).order_by(CanonCommitModel.version.desc()).limit(1))
    recent_rows = list((await session.scalars(
        select(ChapterRunModel).where(
            ChapterRunModel.work_id == work.id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.chapter_no < run.chapter_no,
            ChapterRunModel.content != "",
            ChapterRunModel.state == ChapterRunState.CANONIZED.value,
        ).order_by(ChapterRunModel.chapter_no.desc(), ChapterRunModel.attempt.desc()).limit(30)
    )).all())
    recent_chapters: list[dict[str, Any]] = []
    seen_chapters: set[int] = set()
    for row in recent_rows:
        number = int(row.chapter_no)
        if number in seen_chapters:
            continue
        seen_chapters.add(number)
        content = row.content or ""
        previous_context = row.generation_context or {}
        recent_chapters.append({
            "chapter_no": number, "title": row.title,
            "summary": content[:500], "ending": content[-800:], "content": content,
            "director_plan": previous_context.get("director_plan", {}),
            "actual_state": previous_context.get("actual_state", {}),
            "target_node": previous_context.get("target_node", {}),
            "node_completed": previous_context.get("node_completed", False),
        })
        if len(recent_chapters) >= 3:
            break
    if architecture_version == "director_v2" and nodes:
        previous = recent_chapters[0] if recent_chapters else {}
        previous_id = previous.get("target_node", {}).get("id")
        previous_index = next((i for i, node in enumerate(nodes) if node.node_id == previous_id), None)
        if previous_index is not None:
            completed = bool(previous.get("node_completed"))
            if completed and previous_index >= len(nodes) - 1:
                # 末节点已完成：不再重复生成它（P1-3）。是否还有下一章取决于
                # 有没有展开出新卷与新节点；没有就是整本结束。
                target = None
                story_complete = True
            else:
                target = nodes[min(len(nodes) - 1, previous_index + int(completed))]
        elif not recent_chapters:
            target = nodes[0]
    # 计算前后节点（让 DIRECT 知道从哪来、往哪去）
    target_idx = None
    if target is not None and nodes:
        for i, n in enumerate(nodes):
            if n.node_id == target.node_id:
                target_idx = i
                break
    prev_node = nodes[target_idx - 1] if target_idx and target_idx > 0 else None
    next_node = nodes[target_idx + 1] if target_idx is not None and target_idx < len(nodes) - 1 else None
    # 计算当前卷内剩余节点（只保留接下来 3 个，避免上下文过长）
    vol_remaining = []
    if current_vol is not None and nodes:
        vol_nodes_all = [n for n in nodes if n.volume_no == current_vol.volume_no]
        target_ord = target.ordinal if target else 0
        count = 0
        for vn in vol_nodes_all:
            if vn.ordinal >= target_ord and count < 3:
                vol_remaining.append({"title": vn.title, "promise": vn.promise})
                count += 1
    # D-01：纠错语义必须穿过 ASSEMBLE 重建。排队时保存的 correction /
    # replay_reason / corrections 若在重建那一刻被清掉，重演拿到的就是一次
    # 「不知道要改什么」的普通重跑——纠错内容只躺在事件里，永远到不了导演请求。
    old_context = run.generation_context or {}
    run.generation_context = {
        # 执行器身份先落位：ASSEMBLE 每次重建上下文，重建丢了它就等于
        # 「这一章跑的哪个臂」不可举证。
        **executor_app.carry_over(old_context),
        **{
            key: old_context[key]
            for key in ("correction", "replay_reason", "corrections")
            if key in old_context
        },
        "architecture_version": architecture_version,
        "story_complete": story_complete,
        "parent_canon_version": int(parent_canon.version) if parent_canon else 0,
        "raw_intent": goal.raw_intent,
        "normalized_goal": goal.normalized_goal,
        "assumptions": goal.assumptions or [],
        "genre": work.genre,
        "chapter_no": run.chapter_no,
        "volume": {
            "volume_no": int(current_vol.volume_no) if current_vol else 1,
            "title": current_vol.title if current_vol else "",
            "cultivation_realm": current_vol.cultivation_realm if current_vol else "",
            "remaining_arc": vol_remaining,
        },
        "target_node": {
            "id": target.node_id,
            "title": target.title,
            "promise": target.promise,
            "preconditions": target.preconditions or [],
            "consequences": target.consequences or [],
        } if target else {},
        "prev_node": {
            "title": prev_node.title,
            "consequences": prev_node.consequences or [],
        } if prev_node else {},
        "next_node": {
            "title": next_node.title,
            "preconditions": next_node.preconditions or [],
        } if next_node else {},
        "chapter_assignment": (
            {"objective": "由导演按人物、因果与阅读体验决定本章职责"}
            if architecture_version == "director_v2" else _chapter_assignment(run.chapter_no)
        ),
        "actual_state": recent_chapters[0].get("actual_state", {}) if recent_chapters else {},
        "recent_chapters": recent_chapters,
        "canon": canon,
        "memory": recalled.as_payload(),
        "memory_source_hash": recalled.source_hash,
    }

    existing = list(
        (await session.scalars(select(PersonaSpecModel).where(PersonaSpecModel.work_id == work.id))).all()
    )
    if not existing:
        defaults = (
            ("主角", "推动目标但必须付出代价", "克制、具体、少解释"),
            ("同伴", "帮助主角，同时保护自己的秘密", "敏锐、留有余地"),
            ("对手", "阻止主角并证明自己的秩序正确", "冷静、带压迫感"),
        )
        for name, drive, voice in defaults:
            session.add(
                PersonaSpecModel(
                    id=uuid.uuid4(), work_id=work.id, name=name,
                    identity={"role": name}, drives={"primary": drive},
                    voice={"style": voice}, stable_traits=[drive, voice],
                )
            )
        await session.flush()


def _memory_view(
    payloads: Sequence[dict[str, Any]],
    audience: str,
    persona: str = "",
) -> list[dict[str, Any]]:
    """按受众投影长期记忆（C-03）——实现下沉到 domain（memory.project_payloads）。

    旧流程与 director_v2 必须共用同一个裁剪实现，两边不得漂移（D-03）。
    """
    return memory_domain.project_payloads(payloads, audience, persona)


async def perform(
    session: AsyncSession, *, provider: ModelProvider, work: StoryWorkModel, run: ChapterRunModel
) -> None:
    personas = list((await session.scalars(
        select(PersonaSpecModel).where(PersonaSpecModel.work_id == work.id)
    )).all())
    canon = list(run.generation_context.get("canon", []))
    scene_id = f"chapter-{run.chapter_no}"
    recalled_payloads = list(run.generation_context.get("memory", []))

    async def one(persona: PersonaSpecModel) -> dict[str, Any]:
        grants = [
            fact for fact in canon
            if not fact.get("known_by")
            or persona.name in fact.get("known_by", [])
            or "ALL" in fact.get("known_by", [])
        ]
        excluded = [str(fact.get("statement", "")) for fact in canon if fact not in grants]
        context_hash = hashlib.sha256(
            json.dumps(grants, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        existing = await session.scalar(select(InformationSetModel).where(
            InformationSetModel.persona_id == persona.id,
            InformationSetModel.scene_id == scene_id,
            InformationSetModel.context_hash == context_hash,
        ))
        if existing is None:
            session.add(InformationSetModel(
                id=uuid.uuid4(), work_id=work.id, persona_id=persona.id,
                scene_id=scene_id, grants=grants, exclusions=excluded,
                context_hash=context_hash,
            ))
        sys_prompt = (
            "你正在独立扮演一个小说人物。只能依据角色设定、已知事实和本章任务行动；"
            "不得猜测未提供的秘密。给出有个人动机和独特声纹的具体行动，不写完整章节。"
        )
        recent = run.generation_context.get("recent_chapters", [])
        usr_prompt = json.dumps({
            "persona": persona.name, "identity": persona.identity,
            "drives": persona.drives, "voice": persona.voice,
            "known_facts": grants,
            # 长期记忆按「这个人物」投影：跨章记住的是他自己知道/误信/承诺过的
            # 事，读者认知与导演笔记不进人物上下文（C-03）。
            "character_memory": _memory_view(recalled_payloads, "character", persona.name),
            "chapter_assignment": run.generation_context.get("chapter_assignment", {}),
            "target_node": run.generation_context.get("target_node", {}),
            "previous_ending": recent[0].get("ending", "") if recent else "",
        }, ensure_ascii=False)
        response = await provider.generate_structured(
            system_prompt=sys_prompt, user_prompt=usr_prompt, response_model=Performance,
        )
        await _record_call(
            session, work=work, run=run, step="PERFORM", purpose=f"persona:{persona.name}",
            response=response, system_prompt=sys_prompt, user_prompt=usr_prompt,
        )
        return response.output.model_dump(mode="json")

    run.performances = list(await asyncio.gather(*(one(persona) for persona in personas)))


async def direct(session: AsyncSession, *, provider: ModelProvider, work: StoryWorkModel, run: ChapterRunModel) -> None:
    genre = work.genre or ""
    ctx = run.generation_context
    target = ctx.get("target_node", {})
    prev_n = ctx.get("prev_node", {})
    next_n = ctx.get("next_node", {})
    # 构建节点因果链提示
    chain_hint = ""
    if target.get("promise"):
        chain_hint += f"\n本章承诺给读者的体验：{target['promise']}"
    if prev_n.get("consequences"):
        chain_hint += f"\n上一章的后果（必须承接）：{prev_n['consequences']}"
    if next_n.get("preconditions"):
        chain_hint += f"\n下一章的前置条件（必须埋下伏笔）：{next_n['preconditions']}"
    if "玄幻" in genre:
        rhythm_hint = (
            "本章的爽点不能是单一套路：可以是实力碾压、谜团揭示、反套路反转、多方博弈的意外交汇。"
            "禁止纯过渡、纯描写、纯心理活动——每章都要有冲突升级或主角获胜/顿悟的瞬间。"
            "结尾必须留下悬念或下一个冲突的钩子。"
        )
    else:
        rhythm_hint = (
            "本章必须包含至少一个情节转折点或冲突升级。结尾留下悬念。"
        )
    sys_prompt = (
        "你是小说导演。将多个角色相互隔离后产生的外显表演编排为因果清晰的场景计划。\n"
        "要求：\n"
        "- 角色只能基于自己已知的事实行动，不能知道其他角色的秘密\n"
        "- 每个角色的行动必须具体到可执行的动作，不是抽象描述\n"
        "- 关键台词要符合角色说话风格，简洁有力\n"
        "- 场景计划必须有明确的冲突升级和因果链\n"
        "- 严格完成 chapter_assignment 指定的章节职责和状态变化\n"
        "- state_before 必须继承上一章 state_after；state_after 至少改变位置、关系、资源、伤势、知识或目标中的两项\n"
        "- 必须从 recent_chapters 最后一章结尾继续，禁止复写已经发生的冲突、动作和钩子\n"
        f"{rhythm_hint}\n"
        "不写正文，只输出结构化计划。"
    )
    usr_prompt = json.dumps(
        {
            "story_intent": ctx.get("raw_intent", ""),
            "story_goal": ctx.get("normalized_goal", ""),
            "genre": genre,
            "target_node": target,
            "performances": _visible_performances(run),
            "prev_consequences": prev_n.get("consequences", []),
            "next_preconditions": next_n.get("preconditions", []),
            "chapter_assignment": ctx.get("chapter_assignment", {}),
            "recent_chapters": ctx.get("recent_chapters", []),
            # 导演看全部六类视图（含自己的待办笔记）；未兑现承诺是它排场景的硬约束。
            "director_memory": _memory_view(ctx.get("memory", []), "director"),
        },
        ensure_ascii=False,
    )
    response = await provider.generate_structured(
        system_prompt=sys_prompt,
        user_prompt=usr_prompt,
        response_model=DirectorPlan,
    )
    await _record_call(
        session, work=work, run=run, step="DIRECT", purpose="scene_plan",
        response=response, system_prompt=sys_prompt, user_prompt=usr_prompt,
    )
    context = dict(run.generation_context)
    director_plan = response.output.model_dump(mode="json")
    # 后处理：强制 state_before 继承上一章 state_after（如果有的话）
    recent = ctx.get("recent_chapters", [])
    if recent:
        prev_chapter = recent[0]  # recent_chapters[0] 是最近的一章
        prev_dp = prev_chapter.get("director_plan", {})
        prev_state_after = prev_dp.get("state_after", {})
        if prev_state_after:
            # 强制覆盖 state_before，确保跨章状态继承准确
            director_plan["state_before"] = prev_state_after
    context["director_plan"] = director_plan
    run.generation_context = context


async def weave(session: AsyncSession, *, provider: ModelProvider, work: StoryWorkModel, run: ChapterRunModel) -> None:
    genre = work.genre or ""
    if "玄幻" in genre:
        style_hint = (
            "网文风格：节奏明快，冲突密集，爽点多样。"
            "爽点不能只是打脸——可以是实力碾压、谜团揭示、反套路反转、多方博弈的意外交汇。"
            "使用四字短语和短句增强气势；修炼突破场景要写出震撼感；"
            "悬疑揭示场景要写出“原来如此”的顿悟时刻；"
            "对话简洁有力，符合古代语境；避免现代口语。"
            "禁止大段纯景物描写或纯心理独白——一切描写必须服务于冲突推进。"
        )
    else:
        style_hint = "用动作、对话和具体感官推动情节。每章至少一个冲突升级或转折。"
    # 人在回路：注入检查点 1 的场景计划反馈
    guidance = run.user_guidance or {}
    plan_feedback = guidance.get("after_direct", "")
    feedback_hint = ""
    if plan_feedback:
        feedback_hint = f"\n用户反馈（必须体现在正文中）：{plan_feedback}"
    revision_instructions = list(
        run.generation_context.get("revision_instructions", []) or []
    )
    revision_hint = ""
    if revision_instructions:
        revision_hint = (
            "\n这是质量复审后的重写。必须逐项修复："
            + "；".join(str(item) for item in revision_instructions)
        )
    # 从导演计划中获取状态变化作为硬约束
    dp = run.generation_context.get("director_plan", {})
    state_before = dp.get("state_before", {}) if isinstance(dp, dict) else {}
    state_after = dp.get("state_after", {}) if isinstance(dp, dict) else {}
    ending_hook = dp.get("ending_hook", "") if isinstance(dp, dict) else ""
    required_changes = [
        key for key in set(state_before) | set(state_after)
        if state_before.get(key) != state_after.get(key)
    ]
    state_hint = ""
    if state_before or state_after:
        # 列出完整的 state_before 和 state_after，让模型清楚知道开头和结尾的具体状态
        state_hint = "\n【硬约束】本章的开头和结尾必须严格符合以下状态：\n"
        if state_before:
            state_hint += "【开头状态 state_before】：\n"
            for key, value in state_before.items():
                state_hint += f"  - {key}：{value}\n"
        if state_after:
            state_hint += "【结尾状态 state_after】：\n"
            for key, value in state_after.items():
                state_hint += f"  - {key}：{value}\n"
        if required_changes:
            state_hint += "【必须实现的状态变化】：\n"
            for key in required_changes:
                state_hint += f"  - {key}：{state_before.get(key, '未知')} → {state_after.get(key, '未知')}\n"
        if ending_hook:
            state_hint += f"【本章结尾必须】：{ending_hook}\n"
        state_hint += (
            "严禁正文中出现与上述状态相矛盾的时间、位置、人物关系、物件状态描述。"
            "开头第一段必须体现 state_before 的位置和时间，结尾最后一段必须体现 state_after 的位置和时间。"
        )
    sys_prompt = (
        "你是中文类型小说写作者。依据导演计划和角色表演写一章可直接阅读的正文。"
        f"目标 1800–2500 个中文字符；{style_hint}{feedback_hint}{revision_hint}{state_hint}"
        "避免总结式大纲、元叙事和设定堆砌；结尾必须兑现本章推进并留下自然悬念。"
        "承接最近章节的准确结束状态，但严禁复用其事件、动作序列、威胁台词和结尾钩子。"
    )
    # 从导演计划中获取角色行动（替代旧的 performances）
    char_actions = dp.get("character_actions", []) if isinstance(dp, dict) else []
    usr_prompt = json.dumps(
        {"director_plan": dp, "character_actions": char_actions,
         "performances": _visible_performances(run),
         "revision_instructions": revision_instructions,
         # 正文只拿叙述者视图：读者认知可以出现，导演笔记永不进正文（C-03）。
         "narrator_memory": _memory_view(
             run.generation_context.get("memory", []), "narrator"
         ),
         "context": run.generation_context},
        ensure_ascii=False,
    )
    response = await provider.generate_structured(
        system_prompt=sys_prompt,
        user_prompt=usr_prompt,
        response_model=ChapterDraft,
        temperature=0.85,
    )
    await _record_call(
        session, work=work, run=run, step="WEAVE", purpose="prose",
        response=response, system_prompt=sys_prompt, user_prompt=usr_prompt,
    )
    run.title = response.output.title
    run.content = response.output.content.strip()
    run.word_count = len(run.content)
    if revision_instructions:
        context = dict(run.generation_context)
        context.pop("revision_instructions", None)
        run.generation_context = context


async def review(session: AsyncSession, *, provider: ModelProvider, work: StoryWorkModel, run: ChapterRunModel) -> None:
    is_first_chapter = run.chapter_no <= 1
    sys_prompt = (
        "你是严格的小说编辑。对照 Canon、导演计划的 state_before/state_after、近期章节检查本章。\n"
        "【严重程度区分】：\n"
        "- continuity_issues：仅记录真正的事实矛盾（时间线、位置、人物关系、物件状态与上一章/director_plan 直接矛盾）。过渡略仓促、场景未充分铺垫不算 continuity_issues。\n"
        "- leakage_issues：仅记录角色知道了他不应知道的信息。\n"
        "- prose_issues：节奏/文风/描写层面的小问题（包括“告知而非展示”、“内心独白过长”、“意象重复”等）。\n"
        "【passed 规则】：仅当 continuity_issues 或 leakage_issues 非空、或存在跨章复写、或未实现 required_state_change 时 passed=false。"
        "只有 prose_issues 时 passed=true（小问题交给改稿处理）。"
    )
    if is_first_chapter:
        sys_prompt += (
            "\n注意：这是第一章，没有前一章需要承接。不要因'未承接上一章结尾'而扣分。"
            "重点关注：信息泄露（角色不应知道的信息）、因果推进、正文与 director_plan 的 state_after 一致性。"
        )
    else:
        sys_prompt += (
            "\n本章开头必须继承上一章的 state_after（时间、位置、人物关系）。"
            "若本章开头与上一章 state_after 存在事实矛盾（非小过渡问题），必须在 continuity_issues 中列出。"
        )
    # 人在回路：注入检查点 2 的初稿反馈
    guidance = run.user_guidance or {}
    draft_feedback = guidance.get("after_weave", "")
    dp = run.generation_context.get("director_plan", {})
    usr_prompt_data: dict[str, Any] = {
        "context": run.generation_context,
        "director_plan": dp,
        "character_actions": dp.get("character_actions", []) if isinstance(dp, dict) else [],
        "draft": {"title": run.title, "content": run.content},
    }
    if draft_feedback:
        usr_prompt_data["user_feedback"] = draft_feedback
    usr_prompt = json.dumps(usr_prompt_data, ensure_ascii=False)
    response = await provider.generate_structured(
        system_prompt=sys_prompt,
        user_prompt=usr_prompt,
        response_model=ChapterReview,
    )
    await _record_call(
        session, work=work, run=run, step="REVIEW", purpose="edit_check",
        response=response, system_prompt=sys_prompt, user_prompt=usr_prompt,
    )
    result = response.output
    if result.continuity_issues or result.leakage_issues:
        result = result.model_copy(update={"passed": False})
    revision_attempted = False
    prose_hard_issues = _hard_quality_issues(
        run.content, list(run.generation_context.get("recent_chapters", [])),
    )
    structural_issues = _structural_quality_issues(run)
    hard_issues = prose_hard_issues + structural_issues
    if hard_issues:
        result = result.model_copy(update={
            "passed": False,
            "prose_issues": list(result.prose_issues) + hard_issues,
            "revision_instructions": list(result.revision_instructions) + [
                "删除与近期章节重复的事件和表达，确保本章产生新的状态变化"
            ],
        })
    # 一次证据驱动修订；修订后必须重新经过模型和确定性硬规则复审。
    if not result.passed:
        revision_attempted = True
        rev_sys = (
            "你是中文类型小说改稿编辑。严格执行问题清单，重写为完整可读章节；"
            "保留正确情节，修复连续性、信息泄露、节奏和文风问题。"
        )
        rev_usr = json.dumps(
            {"draft": run.content, "issues": result.model_dump(mode="json"),
             "context": run.generation_context}, ensure_ascii=False,
        )
        revised = await provider.generate_structured(
            system_prompt=rev_sys,
            user_prompt=rev_usr,
            response_model=ChapterDraft,
        )
        await _record_call(
            session, work=work, run=run, step="REVIEW", purpose="revision",
            response=revised, system_prompt=rev_sys, user_prompt=rev_usr,
        )
        run.title = revised.output.title
        run.content = revised.output.content.strip()
        run.word_count = len(run.content)
        recheck_usr = json.dumps({
            **usr_prompt_data,
            "draft": {"title": run.title, "content": run.content},
            "previous_review": result.model_dump(mode="json"),
            "review_stage": "revision_recheck",
        }, ensure_ascii=False)
        rechecked = await provider.generate_structured(
            system_prompt=sys_prompt, user_prompt=recheck_usr, response_model=ChapterReview,
        )
        await _record_call(
            session, work=work, run=run, step="REVIEW", purpose="revision_recheck",
            response=rechecked, system_prompt=sys_prompt, user_prompt=recheck_usr,
        )
        result = rechecked.output
        if result.continuity_issues or result.leakage_issues:
            result = result.model_copy(update={"passed": False})
        prose_hard_issues = _hard_quality_issues(
            run.content, list(run.generation_context.get("recent_chapters", [])),
        )
        structural_issues = _structural_quality_issues(run)
        hard_issues = prose_hard_issues + structural_issues
        if hard_issues:
            result = result.model_copy(update={
                "passed": False, "prose_issues": list(result.prose_issues) + hard_issues,
            })
    run.review = {
        **result.model_dump(mode="json"),
        "revised": revision_attempted,
    }
    if not result.passed:
        failure_classes: list[str] = []
        if result.leakage_issues:
            failure_classes.append("PERFORMANCE")
        if structural_issues or result.continuity_issues:
            failure_classes.append("STRUCTURE")
        if prose_hard_issues or result.prose_issues:
            failure_classes.append("PROSE")
        run.review["failure_classes"] = failure_classes
        raise RuntimeError("QUALITY_GATE_FAILED")


async def canon(
    session: AsyncSession, *, provider: ModelProvider, work: StoryWorkModel, run: ChapterRunModel
) -> None:
    source_hash = hashlib.sha256(run.content.encode("utf-8")).hexdigest()
    from regent.novel.application.direction import is_directed

    if is_directed(run):
        if not run.review.get("passed") or run.generation_context.get("validated_content_hash") != source_hash:
            raise RuntimeError("UNVALIDATED_CHAPTER")
        # Serialise chapter acceptance per work, including the empty-Canon case.
        await session.scalar(select(StoryWorkModel.id).where(StoryWorkModel.id == work.id).with_for_update())
    existing = await session.scalar(
        select(CanonCommitModel).where(
            CanonCommitModel.work_id == work.id,
            CanonCommitModel.branch_id == work.branch_id,
            CanonCommitModel.chapter_no == run.chapter_no,
            CanonCommitModel.source_hash == source_hash,
        )
    )
    if existing is not None:
        return
    canon_sys = (
        "从已完成章节提取后续必须保持一致的客观事实。每项使用 statement、"
        "entities、known_by、confidence 字段；只提取正文明确成立的事实，不推测。"
    )
    if is_directed(run):
        facts = run.generation_context["verified_facts"]
    else:
        response = await provider.generate_structured(
            system_prompt=canon_sys,
            user_prompt=run.content,
            response_model=CanonExtraction,
        )
        await _record_call(
            session, work=work, run=run, step="CANON", purpose="fact_extraction",
            response=response, system_prompt=canon_sys, user_prompt=run.content,
        )
        facts = response.output.facts
    latest = await session.scalar(
        select(CanonCommitModel)
        .where(CanonCommitModel.work_id == work.id, CanonCommitModel.branch_id == work.branch_id)
        .order_by(CanonCommitModel.version.desc()).limit(1)
    )
    parent = int(latest.version) if latest else 0
    facts = tag_facts(
        facts,
        volume_no=await _current_volume_no(session, work),
        chapter_no=int(run.chapter_no),
    )
    if is_directed(run) and parent != run.generation_context.get("parent_canon_version", 0):
        from regent.novel.application.direction import ProductionStopped

        raise ProductionStopped("父事实版本已变化，不能提交过期场景")
    session.add(
        CanonCommitModel(
            id=uuid.uuid4(), work_id=work.id, branch_id=work.branch_id,
            chapter_no=run.chapter_no, parent_version=parent, version=parent + 1,
            facts=facts, source_hash=source_hash,
            validation_id=hashlib.sha256(json.dumps(run.review, sort_keys=True).encode()).hexdigest()[:64],
        )
    )
    # R3：事实链提交后同步抽取长期记忆（规则/弧线/承诺/关系），供后续按需召回。
    # 承诺不来自正文核验——它来自关键路径节点对读者的承诺，是唯一不靠猜测的
    # 伏笔来源；节点完成即兑现（A-04）。
    memory_facts: list[dict[str, Any]] = [
        dict(f) if isinstance(f, dict) else f for f in facts
    ]
    node = run.generation_context.get("target_node") or {}
    if node.get("promise"):
        memory_facts.append(
            {
                "memory_kind": "promise",
                "subject": str(node.get("title") or "").strip(),
                "statement": str(node["promise"]),
                "chapter_no": int(run.chapter_no),
            }
        )
        if run.generation_context.get("node_completed") and node.get("title"):
            memory_facts.append({"resolves": str(node["title"]).strip()})
    await memory_app.record_chapter_memory(
        session, work=work, chapter_no=int(run.chapter_no),
        facts=memory_facts, source_hash=source_hash,
    )


async def execute_step(
    session: AsyncSession, *, provider: ModelProvider, work: StoryWorkModel,
    run: ChapterRunModel, step: ChapterStep,
) -> bool:
    from regent.novel.application.direction import (
        is_directed,
        plan_chapter,
        produce_tick,
        validate_chapter,
    )

    if is_directed(run) and step != ChapterStep.ASSEMBLE:
        if step == ChapterStep.DIRECT:
            await plan_chapter(session, provider=provider, work=work, run=run)
        elif step == ChapterStep.PRODUCE:
            return await produce_tick(session, provider=provider, work=work, run=run)
        elif step == ChapterStep.REVIEW:
            return await validate_chapter(session, provider=provider, work=work, run=run)
        elif step == ChapterStep.CANON:
            await canon(session, provider=provider, work=work, run=run)
        else:
            raise RuntimeError("INVALID_DIRECTOR_STEP")
        return True
    if step == ChapterStep.ASSEMBLE:
        await assemble(session, work=work, run=run)
    elif step == ChapterStep.PERFORM:
        await perform(session, provider=provider, work=work, run=run)
    elif step == ChapterStep.DIRECT:
        await direct(session, provider=provider, work=work, run=run)
    elif step == ChapterStep.WEAVE:
        await weave(session, provider=provider, work=work, run=run)
    elif step == ChapterStep.REVIEW:
        await review(session, provider=provider, work=work, run=run)
    elif step == ChapterStep.CANON:
        await canon(session, provider=provider, work=work, run=run)
    return True
