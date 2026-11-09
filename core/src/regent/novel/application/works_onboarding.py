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
    GENRE_MAX_CHARS,
    GENRE_TAGS,
    GENRE_UNRESOLVED,
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


def _genre_question() -> ClarifyQuestion:
    return ClarifyQuestion(
        question_id="genre",
        prompt="想写哪个题材？",
        options=list(GENRE_TAGS),
        default_assumption=GENRE_UNRESOLVED,
    )


def _ensure_genre_question(
    questions: list[ClarifyQuestion], *, genre: str
) -> list[ClarifyQuestion]:
    """题材题必问：LLM 动态题会整组替换写死模板，把它挤掉。

    genre 是改进库 ``genre_tag`` 的检索键，缺了这本书就只能吃到全局经验，同类型
    经验永远匹配不上。占满上限时挤掉最后一道动态题——题材跨书复用，前提细节在
    方向卡阶段还会再问一次。
    """
    if genre.strip() or any(q.question_id == "genre" for q in questions):
        return questions
    kept = questions[: MAX_QUESTIONS_PER_ROUND - 1]
    return [*kept, _genre_question()]


def _build_clarify_questions(intent: str, genre: str) -> list[ClarifyQuestion]:
    """确定性生成澄清问题。信息不足时写入 assumptions 后继续，不得无限追问。"""
    text = (intent or "").strip()
    questions: list[ClarifyQuestion] = []
    if not genre:
        questions.append(_genre_question())
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
                options=["做成眼前这件事", "保护某个人", "弄清真相", "活下去"],
                default_assumption="做成眼前这件事",
            )
        )
    if "冲突" not in text and "敌人" not in text and "对手" not in text:
        questions.append(
            ClarifyQuestion(
                question_id="conflict",
                prompt="谁或什么在挡着他？",
                options=["眼前的局面", "另一个人", "自己的过去", "还没说清的阻碍"],
                default_assumption="眼前的局面",
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
    desire = answers.get("desire") or "把眼前的事做完"
    conflict = answers.get("conflict") or "眼前的阻碍"
    g = answers.get("genre") or genre or "这部小说"

    return [
        DirectionCard(
            card_id="card-fast",
            title=f"{g}·开篇就看见结果",
            protagonist_desire=f"{desire}，而且这一章就要看到这件事发生",
            core_conflict=f"{conflict}，并且这件事做完后处境立刻不同",
            genre_promise="读者当场看见一件事做完，章末处境已经变了",
            pacing="每章一件事，做完就进入新处境",
            differentiator="最贴合前提本身，不另加用户没写的规则",
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
    """模型没给出大纲时的结构空位。不预设题材，不写具体情节。"""
    g = genre or "这部小说"
    seed = [
        ("开局：眼前的局面", PathNodeType.INCITING),
        ("第一次做出选择", PathNodeType.ESCALATION),
        ("选择带来直接后果", PathNodeType.ESCALATION),
        ("有人隐瞒了一件事", PathNodeType.REVELATION),
        ("原来的办法走不通", PathNodeType.REVERSAL),
        ("关系发生变化", PathNodeType.REVELATION),
        ("一次真正的失败", PathNodeType.REVERSAL),
        ("真相露出一角", PathNodeType.REVELATION),
        ("必须做一次取舍", PathNodeType.BETRAYAL),
        ("局面被改写", PathNodeType.REVERSAL),
        ("阻碍升级", PathNodeType.ESCALATION),
        ("关键信息落地", PathNodeType.REVELATION),
        ("付出代价", PathNodeType.REVERSAL),
        ("这一段的冲突摊开", PathNodeType.CLIMAX),
        ("冲突见分晓", PathNodeType.CLIMAX),
        ("这一段的结果成立", PathNodeType.RESOLUTION),
        ("下一程的处境出现", PathNodeType.ESCALATION),
        ("收束这一段", PathNodeType.RESOLUTION),
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
        genre=genre[:GENRE_MAX_CHARS],
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
        genre = format_direction_line(kw)[:GENRE_MAX_CHARS]
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
    questions = _ensure_genre_question(questions, genre=genre)
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

    # 澄清答案里的题材必须落到作品上：改进库按 genre_tag 检索同类型经验，
    # works.genre 空着就等于这本书永远看不到别人攒下的同类型经验。只写进
    # goal.assumptions 不够——那不是检索键。未选题材时宁缺毋滥，不写占位值。
    answered_genre = str(resolved.get("genre") or "").strip()
    if answered_genre and answered_genre != GENRE_UNRESOLVED:
        work.genre = answered_genre[:GENRE_MAX_CHARS]

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
    outline_cast: list[dict[str, str]] = []
    if existing_path is None:
        outline = None
        if provider is not None and goal is not None:
            from regent.novel.application.generation import generate_outline, rhythm_problems

            outline_args = {
                "raw_intent": goal.raw_intent,
                "genre": work.genre or "",
                "direction_title": chosen.title,
                "protagonist_desire": chosen.protagonist_desire,
                "core_conflict": chosen.core_conflict,
                "genre_promise": chosen.genre_promise,
                "direction_keywords": direction_kw,
            }
            try:
                outline = await generate_outline(provider, **outline_args)
            except Exception:
                outline = None
            problems = rhythm_problems(outline.rhythm) if outline is not None else []
            if problems:
                try:
                    redo = await generate_outline(
                        provider, **outline_args, rhythm_feedback=problems
                    )
                except Exception:
                    redo = None
                if redo is not None and len(rhythm_problems(redo.rhythm)) <= len(problems):
                    outline = redo
        if outline is not None:
            outline_cast = [p for p in outline.personas if isinstance(p, dict)]

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
        rhythm = outline.rhythm if outline is not None else None
        if rhythm is not None and not (rhythm.stake and rhythm.arc_end):
            from regent.novel.application.generation import BookRhythm

            rhythm = BookRhythm(
                opening=rhythm.opening,
                stake=chosen.protagonist_desire or chosen.core_conflict,
                stages=list(rhythm.stages),
                payoff_limit=rhythm.payoff_limit or 1,
                arc_end=chosen.genre_promise or chosen.core_conflict,
            )
        vol_summary: list[dict[str, object]] = []
        if rhythm is not None and rhythm.stake and rhythm.arc_end:
            stages = [s.strip() for s in rhythm.stages if str(s or "").strip()][:5]
            vol_summary.append(
                {
                    "kind": "book_rhythm",
                    "opening": rhythm.opening,
                    "stake": rhythm.stake,
                    "stages": stages if len(stages) >= 3 else [],
                    "payoff_limit": max(1, min(3, int(rhythm.payoff_limit or 1))),
                    "arc_end": rhythm.arc_end,
                }
            )
        vol1 = VolumeModel(
            id=uuid.uuid4(),
            work_id=work_id,
            volume_no=1,
            title=vol_title,
            summary=vol_summary,
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
                outline_cast=outline_cast,
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
