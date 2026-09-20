"""Work onboarding services."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application.events import append_event
from regent.novel.application.production import TransactionFreeProvider
from regent.novel.application.works_access import get_owned_work as _get_owned_work
from regent.novel.application.works_constants import (
    CHAPTERS_PER_NODE,
    DEFAULT_GENRES,
    MAX_CLARIFY_ROUNDS,
    MAX_PATH_NODES,
    MAX_QUESTIONS_PER_ROUND,
    MIN_PATH_NODES,
    OnboardingStatus,
)
from regent.novel.application.works_onboarding_projection import onboarding_out as _onboarding_out
from regent.novel.application.works_path import (
    get_critical_path,
)
from regent.novel.domain.errors import (
    GuardViolation,
    InvalidState,
    NotFound,
    ValidationFailed,
)
from regent.novel.domain.models import (
    ClarifyQuestion,
    ConfirmDirectionOut,
    CriticalNode,
    DirectionCard,
    OnboardingOut,
    PathNodeType,
)
from regent.novel.domain.states import (
    StoryWorkState,
)
from regent.novel.infrastructure.models import (
    ArcNodeModel,
    CriticalNodeModel,
    CriticalPathModel,
    OnboardingSessionModel,
    StoryGoalModel,
    StoryWorkModel,
    VolumeModel,
)


def _tx_free_provider(provider: Any | None, session: AsyncSession) -> Any | None:
    """Model calls must not hold the API request transaction open."""
    if provider is None:
        return None
    if isinstance(provider, TransactionFreeProvider):
        return provider
    return TransactionFreeProvider(provider, session)


def _build_clarify_questions(intent: str, genre: str) -> list[ClarifyQuestion]:
    """确定性生成澄清问题。信息不足时写入 assumptions 后继续，不得无限追问。"""
    text = (intent or "").strip()
    questions: list[ClarifyQuestion] = []
    if not genre:
        questions.append(
            ClarifyQuestion(
                question_id="genre",
                prompt="想写哪个题材？",
                options=list(DEFAULT_GENRES),
                default_assumption=DEFAULT_GENRES[0],
            )
        )
    # 东方玄幻额外问题：修炼体系偏好
    if genre == "东方玄幻" or (
        not genre
        and len(text) >= 40
        and any(kw in text for kw in ("修炼", "修仙", "玄幻", "灵气", "境界"))
    ):
        questions.append(
            ClarifyQuestion(
                question_id="cultivation",
                prompt="偏好哪种修炼体系？",
                options=["凡人流（苦修逆袭）", "天才流（天赋碾压）", "废柴流（绝处逢生）"],
                default_assumption="凡人流（苦修逆袭）",
            )
        )
    if len(text) < 40:
        questions.append(
            ClarifyQuestion(
                question_id="desire",
                prompt="主角最想要的到底是什么？",
                options=["变强", "活下去", "被承认", "复仇"],
                default_assumption="变强",
            )
        )
    if "冲突" not in text and "敌人" not in text and "对手" not in text:
        questions.append(
            ClarifyQuestion(
                question_id="conflict",
                prompt="谁或什么在挡着他？",
                options=["一个更强的对手", "规则本身", "身边最亲近的人", "自己的过去"],
                default_assumption="一个更强的对手",
            )
        )
    return questions[:MAX_QUESTIONS_PER_ROUND]


async def _build_direction_cards(
    intent: str,
    genre: str,
    answers: dict[str, str],
    provider: Any | None = None,
    *,
    revision_feedback: str = "",
    previous_cards: list[dict[str, str]] | None = None,
) -> list[DirectionCard]:
    """2–3 张方向卡。优先 LLM 动态生成，回退到写死模板。"""
    # 尝试 LLM 动态生成
    if provider is not None:
        try:
            from regent.novel.application.generation import generate_direction_cards

            raw_cards = await generate_direction_cards(
                provider,
                raw_intent=intent,
                genre=genre,
                answers=answers,
                revision_feedback=revision_feedback,
                previous_cards=previous_cards,
            )
            cards = []
            for rc in raw_cards:
                cards.append(
                    DirectionCard(
                        card_id=rc.get("card_id", f"card-{len(cards)}"),
                        title=rc.get("title", "未命名方向"),
                        protagonist_desire=rc.get("protagonist_desire", ""),
                        core_conflict=rc.get("core_conflict", ""),
                        genre_promise=rc.get("genre_promise", ""),
                        pacing=rc.get("pacing", ""),
                        differentiator=rc.get("differentiator", ""),
                    )
                )
            if cards:
                return cards
        except Exception:
            pass  # 回退到写死模板

    # 回退：写死模板
    desire = answers.get("desire") or "变强"
    conflict = answers.get("conflict") or "一个更强的对手"
    g = answers.get("genre") or genre or DEFAULT_GENRES[0]

    if "玄幻" in g:
        return [
            DirectionCard(
                card_id="card-xuanhuan-mystery",
                title=f"{g}·悬疑修炼流",
                protagonist_desire=f"{desire}，但金手指本身就是一个未解谜团",
                core_conflict=(
                    f"{conflict}，而主角的每次突破都会揭开金手指的一层真相——"
                    "它不是奖赏，而是某个远古存在的布局"
                ),
                genre_promise=(
                    "修炼即解谜：每次境界突破不仅变强，还揭开世界观的一角。"
                    "读者跟着主角一起拼凑真相，爽点来自“原来如此”的顿悟时刻。"
                ),
                pacing="中快：修炼突破与谜团揭示交替，每三章一个小真相，每十章一个大反转",
                differentiator="悬疑+修炼混搭，爽点不是打脸而是“拼图完成”的智识快感",
            ),
            DirectionCard(
                card_id="card-xuanhuan-faction",
                title=f"{g}·群像博弈流",
                protagonist_desire=f"{desire}，但所有势力都在下棋",
                core_conflict=(
                    f"{conflict}，而主角不是唯一的主角——"
                    "对手也有金手指，盟友也有秘密，每个人都有自己的叙事线"
                ),
                genre_promise=(
                    "多视角叙事：主角在明，对手在暗。"
                    "爽点来自多方博弈的意外交汇——当读者发现三条看似无关的线其实指向同一个结局。"
                ),
                pacing="中：五章切换一次视角，十章一次多方碰撞的大场面",
                differentiator="群像+玄幻混搭，不是一个人碾压所有人，而是聪明人在棋局中找到唯一胜路",
            ),
            DirectionCard(
                card_id="card-xuanhuan-subvert",
                title=f"{g}·反套路流",
                protagonist_desire=f"{desire}，但金手指的代价没人告诉过他",
                core_conflict=(
                    f"{conflict}，而主角的每次“胜利”都会引发意想不到的后果——"
                    "打脸了天才，却引来天才背后的势力；拿了机缘，却发现机缘是陷阱"
                ),
                genre_promise=(
                    "反套路叙事：看似经典的开局，但每个“爽点”都有反转。"
                    "读者以为要打脸，结果打出更大的麻烦；以为升级是好事，结果打开了潘多拉盒子。"
                ),
                pacing="快：每章有爽点，但每个爽点都带着“等等，这不对”的转折",
                differentiator="反套路+玄幻混搭，爽感来自“猜不到下一步”的惊喜",
            ),
        ]

    return [
        DirectionCard(
            card_id="card-fast",
            title=f"{g}·快节奏逆袭",
            protagonist_desire=f"{desire}，而且越快越好",
            core_conflict=f"{conflict}一直压在他头上",
            genre_promise="每章都有进展，三章一个小高潮",
            pacing="快：短章、高频爽点",
            differentiator="节奏最快，重情节推进，轻环境描写",
        ),
        DirectionCard(
            card_id="card-steady",
            title=f"{g}·稳扎稳打",
            protagonist_desire=f"{desire}，但要付出代价",
            core_conflict=f"{conflict}，且代价逐章累积",
            genre_promise="人物关系扎实，伏笔兑现完整",
            pacing="中：章章有因果，十章一个大转折",
            differentiator="最重因果链，伏笔一定有回收",
        ),
        DirectionCard(
            card_id="card-twist",
            title=f"{g}·反转密集",
            protagonist_desire=f"{desire}，但目标本身会被推翻",
            core_conflict=f"{conflict}，且身份会反转",
            genre_promise="身份揭露与立场反转密集",
            pacing="中快：每 2–3 章一次反转",
            differentiator="反转最多，适合喜欢猜不到下一步的读者",
        ),
    ]


def _default_path_nodes(genre: str, desire: str, conflict: str) -> list[CriticalNode]:
    """10–20 节点默认关键路径。人工裁决点固定在死亡/背叛/揭露/开战。

    30 万字架构：东方玄幻使用 18 节点种子（对应一卷 ~54 章），
    其他题材保持原有 18 节点通用模板。
    """
    g = genre or DEFAULT_GENRES[0]

    if "玄幻" in g:
        # 东方玄幻混搭种子：融合悬疑+反套路+修炼，拒绝单一公式
        # 每个节点都有“表面”和“暗线”两层，避免线性套路
        seed = [
            ("金手指觉醒：但代价不明", PathNodeType.INCITING),
            ("第一次展示能力：引来注意而非赞赏", PathNodeType.REVELATION),
            ("被势力试探：有人想拉拢，有人想除掉", PathNodeType.REVERSAL),
            ("意外碾压：不是刻意打脸，而是实力差距太大", PathNodeType.ESCALATION),
            ("发现金手指的异常：它似乎在自主行动", PathNodeType.REVELATION),
            ("被迫卷入势力纷争：不是选择站队，而是被所有人利用", PathNodeType.REVERSAL),
            ("突破境界：金手指解锁新能力，但世界观也被颠覆了一角", PathNodeType.CLIMAX),
            ("发现更大的棋局：自己的金手指只是某个布局的棋子", PathNodeType.REVELATION),
            ("进入秘境：规则对自己有利，但对其他人是死亡陷阱", PathNodeType.ESCALATION),
            ("秘境中的意外发现：金手指的来源线索", PathNodeType.REVELATION),
            ("盟友背叛：不是恶意，而是各有各的立场", PathNodeType.BETRAYAL),
            ("绝境中觉醒：金手指的真正用法，没人想到", PathNodeType.CLIMAX),
            ("反击：不是复仇，而是重新定义规则", PathNodeType.WAR),
            ("离开故土：更大的世界展开", PathNodeType.REVERSAL),
            ("新世界的冲击：这里的强者完全颠覆认知", PathNodeType.ESCALATION),
            ("建立自己的势力：不是招兵买马，而是吸引同类人", PathNodeType.ESCALATION),
            ("天劫降临：金手指与天劫产生共鸣，引来远古存在的注视", PathNodeType.CLIMAX),
            ("卷末清算：谜团解开一层，但更大的谜团浮现", PathNodeType.RESOLUTION),
        ]
    else:
        seed = [
            ("开局：一个具体的不公", PathNodeType.INCITING),
            ("获得第一个外力", PathNodeType.ESCALATION),
            ("第一次小胜，结下第一个仇", PathNodeType.ESCALATION),
            ("发现规则比想象中残酷", PathNodeType.REVELATION),
            ("被迫与对手合作", PathNodeType.REVERSAL),
            ("身边人隐瞒了一件事", PathNodeType.REVELATION),
            ("第一次真正失败", PathNodeType.REVERSAL),
            ("身份被揭露一半", PathNodeType.REVELATION),
            ("亲近的人背叛", PathNodeType.BETRAYAL),
            ("重要的人死了", PathNodeType.DEATH),
            ("被迫站队，开战", PathNodeType.WAR),
            ("拿到关键资源", PathNodeType.ESCALATION),
            ("真相浮出水面", PathNodeType.REVELATION),
            ("付出不可逆代价", PathNodeType.REVERSAL),
            ("决战前夜", PathNodeType.CLIMAX),
            ("卷末决战", PathNodeType.CLIMAX),
            ("战后清算", PathNodeType.RESOLUTION),
            ("新的悬念落地", PathNodeType.RESOLUTION),
        ]

    nodes: list[CriticalNode] = []
    for idx, (title, ntype) in enumerate(seed):
        nodes.append(
            CriticalNode(
                node_id=f"n{idx + 1:02d}",
                ordinal=idx + 1,
                title=title,
                node_type=ntype,
                promise=f"{g}·{desire}推进一步" if idx % 2 == 0 else f"{g}·{conflict}加码",
                preconditions=[f"n{idx:02d}"] if idx else [],
                consequences=[f"n{idx + 2:02d}"] if idx + 1 < len(seed) else [],
                requires_human=ntype
                in {
                    PathNodeType.DEATH,
                    PathNodeType.BETRAYAL,
                    PathNodeType.REVELATION,
                    PathNodeType.WAR,
                },
                locked=False,
            )
        )
    return nodes


async def create_work(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    raw_intent: str,
    title: str = "",
    genre: str = "",
    direction_keywords: list[str] | None = None,
    direction_custom_keywords: list[str] | None = None,
    client_nonce: str = "",
    provider: Any | None = None,
) -> tuple[StoryWorkModel, OnboardingOut]:
    """FR-01/FR-02/FR-03。幂等键 ``user_id:client_nonce``（Tech-Spec §5）。"""
    provider = _tx_free_provider(provider, session)
    if client_nonce:
        existing = await session.scalar(
            select(StoryWorkModel).where(
                StoryWorkModel.owner_id == owner_id,
                StoryWorkModel.title == title,
            )
        )
        # nonce 去重依赖下面唯一约束；此处仅在同 title 时提示，避免误判
        if existing is not None and existing.state == StoryWorkState.ONBOARDING.value:
            pass

    work = StoryWorkModel(
        id=uuid.uuid4(),
        owner_id=owner_id,
        title=title or (raw_intent[:18] or "未命名作品"),
        genre=genre,
        state=StoryWorkState.ONBOARDING.value,
        version=1,
    )
    session.add(work)
    await session.flush()

    from regent.novel.domain.story_direction import (
        assumption_line,
        format_direction_line,
        normalize_direction_keywords,
    )

    kw = normalize_direction_keywords(
        direction_keywords or [],
        direction_custom_keywords or [],
    )
    goal_assumptions: list[str] = []
    if kw:
        goal_assumptions.append(assumption_line(kw))
    if not genre.strip() and kw:
        genre = format_direction_line(kw)[:200]
        work.genre = genre

    session.add(
        StoryGoalModel(
            id=uuid.uuid4(),
            work_id=work.id,
            raw_intent=raw_intent,
            normalized_goal="",
            assumptions=goal_assumptions,
            version=1,
        )
    )

    questions = _build_clarify_questions(raw_intent, genre)
    # 尝试 LLM 动态生成澄清问题（覆盖写死模板）
    if provider is not None and not questions:
        try:
            from regent.novel.application.generation import generate_clarify_questions

            llm_qs = await generate_clarify_questions(provider, raw_intent=raw_intent, genre=genre)
            questions = [ClarifyQuestion(**q) for q in llm_qs]
        except Exception:
            pass  # 回退到空列表（信息足够，直接给方向卡）
    elif provider is not None:
        # 有写死问题时，用 LLM 替换为动态版本
        try:
            from regent.novel.application.generation import generate_clarify_questions

            llm_qs = await generate_clarify_questions(provider, raw_intent=raw_intent, genre=genre)
            if llm_qs:
                questions = [ClarifyQuestion(**q) for q in llm_qs]
        except Exception:
            pass  # 保留写死问题
    onboarding = OnboardingSessionModel(
        id=uuid.uuid4(),
        work_id=work.id,
        user_id=owner_id,
        clarify_round=1 if questions else 0,
        question_count=len(questions),
        questions=[q.model_dump(mode="json") for q in questions],
        assumptions=[],
        directions=[],
    )
    session.add(onboarding)
    await session.flush()

    if questions:
        status = OnboardingStatus.CLARIFYING
    else:
        status = OnboardingStatus.DIRECTIONS
        cards = await _build_direction_cards(raw_intent, genre, {}, provider=provider)
        onboarding.directions = [c.model_dump(mode="json") for c in cards]

    await append_event(
        session,
        work_id=work.id,
        event_type="work.created",
        data={"title": work.title, "state": work.state},
        branch_id=work.branch_id,
    )
    return work, OnboardingOut(
        status=status.value,
        clarify_round=onboarding.clarify_round,
        question_count=onboarding.question_count,
        questions=questions,
        assumptions=[],
        directions=[DirectionCard(**c) for c in (onboarding.directions or [])],
    )


async def answer_clarify(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    answers: dict[str, str],
    accept_defaults: bool = False,
    provider: Any | None = None,
) -> OnboardingOut:
    """G-21：只允许 1 轮。第二轮直接以 assumptions 收口，不再追问。"""
    provider = _tx_free_provider(provider, session)
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if work.state != StoryWorkState.ONBOARDING.value:
        raise InvalidState("onboarding already finished", current=work.state)

    onboarding = await session.scalar(
        select(OnboardingSessionModel).where(OnboardingSessionModel.work_id == work_id)
    )
    if onboarding is None:
        raise NotFound("onboarding session not found")

    stored_questions = [ClarifyQuestion(**q) for q in (onboarding.questions or [])]
    assumptions: list[str] = []
    resolved: dict[str, str] = dict(answers or {})

    for q in stored_questions:
        value = (resolved.get(q.question_id) or "").strip()
        if not value and q.default_assumption:
            value = q.default_assumption
            assumptions.append(f"{q.prompt}（未回答，按默认：{q.default_assumption}）")
        if value:
            resolved[q.question_id] = value

    # 第二轮或接受默认 → 强制收口
    if onboarding.clarify_round >= MAX_CLARIFY_ROUNDS or accept_defaults:
        for q in stored_questions:
            if not resolved.get(q.question_id) and q.default_assumption:
                resolved[q.question_id] = q.default_assumption
                note = f"{q.prompt}（未回答，按默认：{q.default_assumption}）"
                if note not in assumptions:
                    assumptions.append(note)

    goal = await session.scalar(
        select(StoryGoalModel)
        .where(StoryGoalModel.work_id == work_id)
        .order_by(StoryGoalModel.version.desc())
        .limit(1)
    )
    if goal is not None:
        from regent.novel.domain.story_direction import (
            ASSUMPTION_PREFIX,
            assumption_line,
            keywords_from_assumptions,
        )

        # 保留开书时写入的方向关键词；澄清答案追加，不得整表覆盖冲掉。
        preserved: list[str] = []
        existing_kw = keywords_from_assumptions(goal.assumptions or [])
        if existing_kw:
            preserved.append(assumption_line(existing_kw))
        for line in goal.assumptions or []:
            text = str(line or "").strip()
            if not text or text.startswith(ASSUMPTION_PREFIX):
                continue
            if text.startswith("澄清："):
                continue
            preserved.append(text)

        answer_notes = [
            f"澄清：{q.prompt} → {resolved[q.question_id]}"
            for q in stored_questions
            if resolved.get(q.question_id)
        ]
        goal.assumptions = preserved + answer_notes + list(assumptions)
        goal.normalized_goal = (
            f"{resolved.get('genre', work.genre or DEFAULT_GENRES[0])}｜"
            f"主角想要{resolved.get('desire', '变强')}｜"
            f"阻碍：{resolved.get('conflict', '一个更强的对手')}"
        )

    cards = await _build_direction_cards(
        goal.raw_intent if goal else "",
        resolved.get("genre", work.genre),
        resolved,
        provider=provider,
    )
    onboarding.assumptions = list(assumptions)
    onboarding.directions = [c.model_dump(mode="json") for c in cards]
    onboarding.question_count = len(stored_questions)
    await session.flush()

    await append_event(
        session,
        work_id=work_id,
        event_type="onboarding.clarified",
        data={"assumptions": assumptions, "directions": len(cards)},
        branch_id=work.branch_id,
    )
    return OnboardingOut(
        status=OnboardingStatus.DIRECTIONS.value,
        clarify_round=onboarding.clarify_round,
        question_count=onboarding.question_count,
        questions=[],
        assumptions=assumptions,
        directions=cards,
    )


async def get_onboarding(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
) -> OnboardingOut | None:
    """刷新恢复：返回仍在引导中的 onboarding 会话；已完成/不存在则 None。

    前端刷新后只调 GET /runs——而后端对不存在的 run 返回 QUEUED 进度，
    于是 UI 误入「实时生成中」假象、作品永远停在 ONBOARDING（M2 浏览器
    旅程实测发现的断链）。此端点让前端恢复路径能回到澄清/方向卡。
    """
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if work.state != StoryWorkState.ONBOARDING.value:
        return None
    onboarding = await session.scalar(
        select(OnboardingSessionModel).where(OnboardingSessionModel.work_id == work_id)
    )
    if onboarding is None:
        return None
    bible = dict(onboarding.world_bible or {})
    if bible.get("world_premise") or bible.get("personas"):
        return _onboarding_out(onboarding)
    if onboarding.directions:
        # 澄清已收口：直接回到方向卡，不重复追问
        return OnboardingOut(
            status=OnboardingStatus.DIRECTIONS.value,
            clarify_round=int(onboarding.clarify_round or 0),
            question_count=int(onboarding.question_count or 0),
            questions=[],
            assumptions=list(onboarding.assumptions or []),
            directions=[DirectionCard(**c) for c in (onboarding.directions or [])],
        )
    questions = [ClarifyQuestion(**q) for q in (onboarding.questions or [])]
    if not questions:
        return None
    return OnboardingOut(
        status=OnboardingStatus.CLARIFYING.value,
        clarify_round=int(onboarding.clarify_round or 0),
        question_count=int(onboarding.question_count or 0),
        questions=questions,
        assumptions=list(onboarding.assumptions or []),
        directions=[],
    )


async def revise_directions(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    feedback: str,
    provider: Any | None = None,
) -> OnboardingOut:
    """方向卡都不合适：按用户意见重出卡，仍停留在 ONBOARDING 选择页。"""
    provider = _tx_free_provider(provider, session)
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if work.state != StoryWorkState.ONBOARDING.value:
        raise InvalidState("direction already confirmed", current=work.state)
    onboarding = await session.scalar(
        select(OnboardingSessionModel).where(OnboardingSessionModel.work_id == work_id)
    )
    if onboarding is None:
        raise NotFound("onboarding session not found")

    goal = await session.scalar(
        select(StoryGoalModel)
        .where(StoryGoalModel.work_id == work_id)
        .order_by(StoryGoalModel.version.desc())
        .limit(1)
    )
    note = f"方向修订意见：{feedback.strip()}"
    if goal is not None:
        kept = [str(x) for x in (goal.assumptions or []) if str(x).strip()]
        kept = [x for x in kept if not x.startswith("方向修订意见：")]
        goal.assumptions = [*kept, note]
    onboarding.assumptions = [*list(onboarding.assumptions or []), note]

    prev = list(onboarding.directions or [])
    cards = await _build_direction_cards(
        goal.raw_intent if goal else "",
        work.genre or "",
        {},
        provider=provider,
        revision_feedback=feedback.strip(),
        previous_cards=prev,
    )
    onboarding.directions = [c.model_dump(mode="json") for c in cards]
    onboarding.questions = []
    onboarding.question_count = 0
    await session.flush()
    await append_event(
        session,
        work_id=work.id,
        event_type="onboarding.directions_revised",
        data={"feedback": feedback.strip()[:200], "directions": len(cards)},
        branch_id=work.branch_id,
    )
    return OnboardingOut(
        status=OnboardingStatus.DIRECTIONS.value,
        clarify_round=int(onboarding.clarify_round or 0),
        question_count=0,
        questions=[],
        assumptions=list(onboarding.assumptions or []),
        directions=cards,
    )


async def confirm_direction(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    card_id: str,
    provider: Any | None = None,
    custom_direction: str = "",
) -> ConfirmDirectionOut:
    """锁定方向 → 生成关键路径 → 编剧写世界书草稿；作品保持 ONBOARDING。"""
    provider = _tx_free_provider(provider, session)
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    onboarding = await session.scalar(
        select(OnboardingSessionModel).where(OnboardingSessionModel.work_id == work_id)
    )
    custom = (custom_direction or "").strip()
    effective_card_id = (card_id or "").strip() or ("card-custom" if custom else "")

    if work.state != StoryWorkState.ONBOARDING.value:
        if (
            onboarding is not None
            and onboarding.selected_card_id == effective_card_id
            and (work.story_bible or {})
        ):
            # 已锁定世界并 READY：回放路径
            return ConfirmDirectionOut(
                path=await get_critical_path(session, owner_id=owner_id, work_id=work_id),
                onboarding=OnboardingOut(
                    status=OnboardingStatus.READY.value,
                    clarify_round=int(onboarding.clarify_round or 0),
                    question_count=0,
                    questions=[],
                    assumptions=list(onboarding.assumptions or []),
                    directions=[DirectionCard(**c) for c in (onboarding.directions or [])],
                    world_bible=dict(work.story_bible or {}),
                ),
            )
        raise InvalidState("direction already confirmed", current=work.state)

    assert onboarding is not None

    # 同卡幂等：已有世界书草稿则回放，不重复造书
    existing_bible = dict(onboarding.world_bible or {})
    if onboarding.selected_card_id == effective_card_id and (
        existing_bible.get("world_premise") or existing_bible.get("personas")
    ):
        path = await get_critical_path(session, owner_id=owner_id, work_id=work_id)
        return ConfirmDirectionOut(path=path, onboarding=_onboarding_out(onboarding))

    cards = [DirectionCard(**c) for c in (onboarding.directions or [])]
    chosen = next((c for c in cards if c.card_id == effective_card_id), None)
    if chosen is None and custom:
        title = custom[:18] + ("…" if len(custom) > 18 else "")
        chosen = DirectionCard(
            card_id="card-custom",
            title=title or "用户自定方向",
            protagonist_desire=custom[:400],
            core_conflict="按用户意见推进，具体阻力由大纲与导演发明",
            genre_promise=custom[:400],
            pacing="按用户意见安排",
            differentiator="用户跳过候选卡，直接以自填意见为故事承诺",
        )
        effective_card_id = "card-custom"
    if chosen is None:
        raise ValidationFailed("unknown direction card")

    onboarding.selected_card_id = effective_card_id
    onboarding.locked_at = datetime.now(UTC)

    goal = await session.scalar(
        select(StoryGoalModel)
        .where(StoryGoalModel.work_id == work_id)
        .order_by(StoryGoalModel.version.desc())
        .limit(1)
    )
    if goal is not None:
        goal.locked_at = datetime.now(UTC)
        goal.normalized_goal = (
            f"{chosen.title}｜{chosen.protagonist_desire}｜{chosen.core_conflict}"
        )
        import json as _json

        from regent.novel.domain.story_direction import ASSUMPTION_PREFIX

        lock_payload = {
            "card_id": chosen.card_id,
            "title": chosen.title,
            "protagonist_desire": chosen.protagonist_desire,
            "core_conflict": chosen.core_conflict,
            "genre_promise": chosen.genre_promise,
            "differentiator": chosen.differentiator,
        }
        lock_line = "locked_direction:" + _json.dumps(lock_payload, ensure_ascii=False)
        kept = [
            str(x)
            for x in (goal.assumptions or [])
            if str(x).strip() and not str(x).startswith("locked_direction:")
        ]
        kw_lines = [x for x in kept if x.startswith(ASSUMPTION_PREFIX)]
        other = [x for x in kept if not x.startswith(ASSUMPTION_PREFIX)]
        goal.assumptions = [*kw_lines, lock_line, *other]

    from regent.novel.domain.story_direction import (
        keywords_from_assumptions,
        locked_direction_from_assumptions,
    )

    direction_kw = keywords_from_assumptions(goal.assumptions or []) if goal else []
    locked_dir = locked_direction_from_assumptions(goal.assumptions or []) if goal else {}

    # 已有路径则复用（同卡重提中途失败后恢复）
    existing_path = await session.scalar(
        select(CriticalPathModel)
        .where(CriticalPathModel.work_id == work_id)
        .order_by(CriticalPathModel.version.desc())
        .limit(1)
    )
    if existing_path is None:
        outline = None
        if provider is not None and goal is not None:
            try:
                from regent.novel.application.generation import generate_outline

                outline = await generate_outline(
                    provider,
                    raw_intent=goal.raw_intent,
                    genre=work.genre or "",
                    direction_title=chosen.title,
                    protagonist_desire=chosen.protagonist_desire,
                    core_conflict=chosen.core_conflict,
                    genre_promise=chosen.genre_promise,
                    direction_keywords=direction_kw,
                )
            except Exception:
                outline = None

        if outline is not None:
            from regent.novel.domain.models import CriticalNode, PathNodeType

            nodes = []
            for idx, o_node in enumerate(outline.nodes):
                ntype_str = o_node.node_type.upper()
                try:
                    ntype = PathNodeType(ntype_str)
                except ValueError:
                    ntype = PathNodeType.TURN
                nodes.append(
                    CriticalNode(
                        node_id=f"n{idx + 1:02d}",
                        ordinal=idx + 1,
                        title=o_node.title,
                        node_type=ntype,
                        promise=o_node.promise,
                        preconditions=list(o_node.preconditions),
                        consequences=list(o_node.consequences),
                    )
                )
            vol_title = outline.volume_title or "第一卷"
            vol_realm = outline.cultivation_realm or ""
            # 人物不在此入库：锁定世界书时由编剧 personas 写入
        else:
            nodes = _default_path_nodes(work.genre, "变强", chosen.core_conflict)
            vol_title = "第一卷"
            vol_realm = ""
        if not MIN_PATH_NODES <= len(nodes) <= MAX_PATH_NODES:
            if len(nodes) > MAX_PATH_NODES:
                nodes = nodes[:MAX_PATH_NODES]
            elif len(nodes) < MIN_PATH_NODES:
                raise GuardViolation("default critical path must contain 10-20 nodes")

        path = CriticalPathModel(
            id=uuid.uuid4(),
            work_id=work_id,
            version=1,
            frozen_through_chapter=0,
            node_count=len(nodes),
            dependency_edges=[],
        )
        session.add(path)
        await session.flush()
        for node in nodes:
            session.add(
                CriticalNodeModel(
                    id=uuid.uuid4(),
                    path_id=path.id,
                    node_id=node.node_id,
                    ordinal=node.ordinal,
                    title=node.title,
                    node_type=node.node_type.value,
                    promise=node.promise,
                    preconditions=list(node.preconditions),
                    consequences=list(node.consequences),
                    requires_human=node.requires_human,
                    locked=node.locked,
                    volume_no=1,
                    arc_no=(node.ordinal - 1) // CHAPTERS_PER_NODE + 1,
                )
            )
        await session.flush()

        chapters_in_vol = len(nodes) * CHAPTERS_PER_NODE
        vol1 = VolumeModel(
            id=uuid.uuid4(),
            work_id=work_id,
            volume_no=1,
            title=vol_title,
            summary=[],
            cultivation_realm=vol_realm,
            start_chapter_no=1,
            end_chapter_no=chapters_in_vol,
            state="ACTIVE",
        )
        session.add(vol1)
        await session.flush()
        arc_count = max(1, len(nodes) // 3)
        for arc_idx in range(arc_count):
            start_ord = arc_idx * 3 + 1
            end_ord = min((arc_idx + 1) * 3, len(nodes))
            session.add(
                ArcNodeModel(
                    id=uuid.uuid4(),
                    volume_id=vol1.id,
                    arc_no=arc_idx + 1,
                    title=nodes[start_ord - 1].title if start_ord <= len(nodes) else "",
                    arc_type="STANDARD",
                    chapter_range_start=(start_ord - 1) * CHAPTERS_PER_NODE + 1,
                    chapter_range_end=end_ord * CHAPTERS_PER_NODE,
                )
            )
        work.total_volume_count = 1
        await session.flush()
    else:
        nodes = []  # path out via get_critical_path

    # 编剧写世界书
    bible_data: dict[str, Any] = {}
    if provider is not None and goal is not None:
        try:
            from regent.novel.application.generation import generate_world_bible

            bible = await generate_world_bible(
                provider,
                raw_intent=goal.raw_intent,
                genre=work.genre or "",
                direction_keywords=direction_kw,
                locked_direction=locked_dir,
            )
            bible_data = bible.model_dump(mode="json")
        except Exception:
            bible_data = {}
    if not bible_data:
        from regent.novel.domain.world_bible import fallback_bible_from_direction

        bible_data = fallback_bible_from_direction(
            chosen,
            raw_intent=(goal.raw_intent if goal else "") or "",
        )
    onboarding.world_bible = bible_data
    onboarding.world_bible_locked_at = None
    await session.flush()

    # 保持 ONBOARDING，不进入 READY
    work.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="work.direction_confirmed",
        data={"card_id": effective_card_id, "world_bible_draft": True},
        branch_id=work.branch_id,
    )
    path_out = await get_critical_path(session, owner_id=owner_id, work_id=work_id)
    return ConfirmDirectionOut(path=path_out, onboarding=_onboarding_out(onboarding))
