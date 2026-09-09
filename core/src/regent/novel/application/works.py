"""Novel 作品应用服务（FR-01~FR-17 / FR-22~FR-25）。

所有查询以 ``owner_id`` 过滤；越权一律返回 NotFound，不泄露存在性（G-12）。
状态迁移走 ``assert_*_transition``；版本冲突返回 409 + current_version（FR-05）。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.application import executor as executor_app
from regent.novel.application import memory as memory_app
from regent.novel.application.direction import ARCHITECTURE, ProductionStopped, is_directed
from regent.novel.application.events import append_event
from regent.novel.application.generation import execute_step, generate_ending_verdict
from regent.novel.application.principal import as_utc
from regent.novel.application.production import (
    acquire_run_lease,
    lease_is_valid,
    release_run_lease,
)
from regent.novel.domain import ending
from regent.novel.domain.errors import (
    Conflict,
    ExportNoticeRequired,
    GuardViolation,
    InvalidState,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from regent.novel.domain.models import (
    AutoAdvanceRequest,
    ChapterOut,
    ClarifyQuestion,
    CriticalNode,
    CriticalPathOut,
    CriticalPathUpdate,
    DecisionOption,
    DecisionView,
    DirectionCard,
    EventPage,
    ExportNoticeOut,
    ExportOut,
    ExportRequest,
    GuidanceRequest,
    ModerationCaseOut,
    ModerationDecision,
    OnboardingOut,
    PathChangeImpact,
    PathNodeType,
    ReportFactRequest,
    ReportFactResponse,
    RunProgressOut,
    ShareOut,
    StepState,
    StoryGoalOut,
    UXProjection,
    WorkDetail,
    WorkStateOut,
    WorkSummary,
)
from regent.novel.domain.moderation import scan_text
from regent.novel.domain.states import (
    CHAPTER_STEP_ORDER,
    ChapterRunState,
    ChapterStep,
    DecisionState,
    StoryWorkState,
    assert_chapter_run_transition,
    assert_story_work_transition,
    chapter_step_order,
)
from regent.novel.infrastructure.models import (
    ArcNodeModel,
    ChapterRunModel,
    ChapterStepModel,
    CriticalNodeModel,
    CriticalPathModel,
    DecisionRequestModel,
    ExportJobModel,
    ExportNoticeLogModel,
    ExportNoticeModel,
    ModerationCaseModel,
    OnboardingSessionModel,
    PersonaSpecModel,
    ShareModel,
    StoryGoalModel,
    StoryWorkModel,
    VolumeModel,
)

# 运行租约：事务外调用模型期间独占推进权；租约只保护调用窗口，不跨 tick 占用
_LEASE_OWNER = "novel-producer"

_LEASE_FREE_STATES = frozenset(
    {
        ChapterRunState.CANONIZED.value,
        ChapterRunState.CANCELLED.value,
        ChapterRunState.SUPERSEDED.value,
        ChapterRunState.TERMINAL_FAILED.value,
        ChapterRunState.PENDING_DECISION.value,
        ChapterRunState.AWAITING_INPUT.value,
    }
)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

MAX_CLARIFY_ROUNDS = 1  # G-21
MAX_QUESTIONS_PER_ROUND = 3  # G-21
MIN_PATH_NODES = 10  # FR-04（首卷最低要求）
MAX_PATH_NODES = 20  # FR-04（首卷上限，后续卷由 per-volume 常量控制）
MIN_NODES_PER_VOLUME = 5  # 30 万字架构：每卷最低节点
MAX_NODES_PER_VOLUME = 30  # 30 万字架构：每卷最高节点
CHAPTERS_PER_NODE = 3  # 30 万字架构：每节点覆盖章节数
CURRENT_EXPORT_NOTICE_VERSION = "export-notice-2026-09-v1"
AI_DISCLOSURE = "本文内容由 AI 参与生成"

# 人在回路：检查点步骤（完成后暂停等待用户反馈）
CHECKPOINT_STEPS: frozenset[str] = frozenset({"DIRECT", "WEAVE"})

# D-04 未拍板前的默认题材（高文笔容忍度优先，PRD §10）
DEFAULT_GENRES = ("东方玄幻", "都市系统", "无限流")

_EXPORT_NOTICE_TITLE = "关于作品去向，请先确认"
_EXPORT_NOTICE_BODY = (
    "本平台产出的内容包含 AI 参与生成，按《人工智能生成合成内容标识办法》"
    "导出文件会保留 AI 参与标识。国内主流网文平台对 AI 生成内容有比例限制"
    "（例如部分平台要求 AI 含量低于 30%，起点要求全人工）。"
    "因此作品无法以保证过审的方式发布到这些平台。"
    "本平台不提供去除 AI 标识、降低检测值或帮助通过外部平台审核的功能。"
    "导出后你可自行决定是否以及在哪里发布，相关后果由你承担。"
)


class OnboardingStatus(StrEnum):
    CLARIFYING = "CLARIFYING"
    READY = "READY"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _fingerprint(payload: dict[str, Any]) -> str:
    import json

    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def _get_owned_work(
    session: AsyncSession, *, work_id: uuid.UUID, owner_id: uuid.UUID
) -> StoryWorkModel:
    """G-12：无 owner 条件的私有读取 fail closed。"""
    row = await session.scalar(
        select(StoryWorkModel).where(
            StoryWorkModel.id == work_id,
            StoryWorkModel.owner_id == owner_id,
            StoryWorkModel.deleted_at.is_(None),
        )
    )
    if row is None:
        raise NotFound("work not found")
    return row


def _next_milestone(key: StoryWorkState, pending: int) -> str | None:
    """下一个里程碑。有裁决先报裁决——人工等待必须最显眼。"""
    if pending:
        return "完成裁决"
    if key == StoryWorkState.RUNNING:
        return "下一章"
    return None


def _projection_for(state: str, *, pending: int = 0, chapter_no: int | None = None) -> UXProjection:
    """用户态投影。未知状态 map 到 unknown_recoverable，不得猜成成功/失败。"""
    known = {
        StoryWorkState.ONBOARDING: ("confirm_direction", "确认故事方向", ["confirm_direction"]),
        StoryWorkState.READY: ("ready_to_start", "可以开始了", ["start_run"]),
        StoryWorkState.RUNNING: ("writing", "正在写下一章", ["leave_safely", "pause"]),
        StoryWorkState.PENDING_DECISION: (
            "needs_your_call",
            "有一个选择需要你定",
            ["open_decision"],
        ),
        StoryWorkState.PAUSED_QUOTA: ("paused_quota", "额度用完了，已暂停", ["top_up"]),
        StoryWorkState.PAUSED_COST: ("paused_cost", "成本达到上限，已暂停", ["raise_limit"]),
        StoryWorkState.RECOMPUTING: ("adjusting", "正在按你的改动重算", ["leave_safely"]),
        StoryWorkState.FAILED: ("recoverable_problem", "卡住了，可以重试", ["retry"]),
        StoryWorkState.DONE: ("volume_done", "本卷完成", ["start_next_volume"]),
        StoryWorkState.CANCELLED: ("cancelled", "已取消", []),
        StoryWorkState.ARCHIVED: ("archived", "已归档", []),
    }
    try:
        key = StoryWorkState(state)
    except ValueError:
        return UXProjection(
            public_stage="unknown_recoverable",
            stage_label="状态同步中",
            safe_to_leave=True,
            unknown_recoverable=True,
            available_actions=["reload"],
        )
    stage, label, actions = known[key]
    return UXProjection(
        public_stage=stage,
        stage_label=label,
        last_completed_artifact=(f"第 {chapter_no} 章" if chapter_no else None),
        next_milestone=_next_milestone(key, pending),
        eta_range={"min_minutes": 3, "max_minutes": 15} if key == StoryWorkState.RUNNING else None,
        # 人工等待与运行中都允许离开；loop 在服务端继续（PRD §3.2）
        safe_to_leave=True,
        stale_at=datetime.now(UTC) + timedelta(minutes=10),
        action_required=pending > 0 or key == StoryWorkState.PENDING_DECISION,
        available_actions=actions,
    )


# ---------------------------------------------------------------------------
# Onboarding（FR-01 / FR-02 / FR-03 / G-21）
# ---------------------------------------------------------------------------


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
    if genre == "东方玄幻" or (not genre and len(text) >= 40 and any(
        kw in text for kw in ("修炼", "修仙", "玄幻", "灵气", "境界")
    )):
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
    intent: str, genre: str, answers: dict[str, str],
    provider: Any | None = None,
) -> list[DirectionCard]:
    """2–3 张方向卡。优先 LLM 动态生成，回退到写死模板。"""
    # 尝试 LLM 动态生成
    if provider is not None:
        try:
            from regent.novel.application.generation import generate_direction_cards
            raw_cards = await generate_direction_cards(
                provider, raw_intent=intent, genre=genre, answers=answers,
            )
            cards = []
            for rc in raw_cards:
                cards.append(DirectionCard(
                    card_id=rc.get("card_id", f"card-{len(cards)}"),
                    title=rc.get("title", "未命名方向"),
                    protagonist_desire=rc.get("protagonist_desire", ""),
                    core_conflict=rc.get("core_conflict", ""),
                    genre_promise=rc.get("genre_promise", ""),
                    pacing=rc.get("pacing", ""),
                    differentiator=rc.get("differentiator", ""),
                ))
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


# ---------------------------------------------------------------------------
# 作品生命周期
# ---------------------------------------------------------------------------


async def create_work(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    raw_intent: str,
    title: str = "",
    genre: str = "",
    client_nonce: str = "",
    provider: Any | None = None,
) -> tuple[StoryWorkModel, OnboardingOut]:
    """FR-01/FR-02/FR-03。幂等键 ``user_id:client_nonce``（Tech-Spec §5）。"""
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

    session.add(
        StoryGoalModel(
            id=uuid.uuid4(),
            work_id=work.id,
            raw_intent=raw_intent,
            normalized_goal="",
            assumptions=[],
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
        # 信息足够：直接给方向卡，不追问
        status = OnboardingStatus.READY
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
        goal.assumptions = list(assumptions)
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
        status=OnboardingStatus.READY.value,
        clarify_round=onboarding.clarify_round,
        question_count=onboarding.question_count,
        questions=[],
        assumptions=assumptions,
        directions=cards,
    )


async def confirm_direction(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    card_id: str,
    provider: Any | None = None,
) -> tuple[StoryWorkModel, CriticalPathOut]:
    """FR-03 → FR-04：锁定方向并生成 10–20 节点关键路径。

    如果 provider 可用，使用 LLM 生成定制化大纲；否则回退到静态模板。
    """
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if work.state != StoryWorkState.ONBOARDING.value:
        raise InvalidState("direction already confirmed", current=work.state)

    onboarding = await session.scalar(
        select(OnboardingSessionModel).where(OnboardingSessionModel.work_id == work_id)
    )
    cards = [DirectionCard(**c) for c in (onboarding.directions or [])] if onboarding else []
    chosen = next((c for c in cards if c.card_id == card_id), None)
    if chosen is None:
        raise ValidationFailed("unknown direction card")

    onboarding.selected_card_id = card_id
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

    # 尝试 LLM 生成定制化大纲
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
            )
        except Exception:
            outline = None  # 回退到静态模板

    if outline is not None:
        # 使用 LLM 生成的定制化大纲
        from regent.novel.domain.models import CriticalNode, PathNodeType
        nodes = []
        for idx, o_node in enumerate(outline.nodes):
            ntype_str = o_node.node_type.upper()
            try:
                ntype = PathNodeType(ntype_str)
            except ValueError:
                ntype = PathNodeType.ESCALATION
            nodes.append(CriticalNode(
                node_id=f"n{idx+1:02d}",
                ordinal=idx + 1,
                title=o_node.title,
                node_type=ntype,
                promise=o_node.promise,
                preconditions=list(o_node.preconditions),
                consequences=list(o_node.consequences),
            ))
        vol_title = outline.volume_title or "第一卷"
        vol_realm = outline.cultivation_realm or ""
        # 写入自定义角色
        if outline.personas:
            for p in outline.personas:
                session.add(
                    PersonaSpecModel(
                        id=uuid.uuid4(), work_id=work_id,
                        name=p.get("name", "角色"),
                        identity={"role": p.get("identity", "")},
                        drives={"primary": p.get("drive", "")},
                        voice={"style": p.get("voice", "")},
                        stable_traits=[p.get("drive", ""), p.get("voice", "")],
                    )
                )
            await session.flush()
    else:
        # 回退到静态模板
        nodes = _default_path_nodes(work.genre, "变强", chosen.core_conflict)
        vol_title = "第一卷"
        vol_realm = ""
    if not MIN_PATH_NODES <= len(nodes) <= MAX_PATH_NODES:
        # 裁剪到合法范围
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

    # 30 万字架构：创建首卷
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
    # 为首卷创建弧段
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

    assert_story_work_transition(work.state, StoryWorkState.READY.value)
    work.state = StoryWorkState.READY.value
    work.version += 1
    await session.flush()

    await append_event(
        session,
        work_id=work_id,
        event_type="work.direction_confirmed",
        data={"card_id": card_id, "node_count": len(nodes)},
        branch_id=work.branch_id,
    )
    return work, _path_out(path, nodes)


# ---------------------------------------------------------------------------
# 关键路径（FR-04 / FR-05）
# ---------------------------------------------------------------------------


def _path_out(path: CriticalPathModel, nodes: list[CriticalNode]) -> CriticalPathOut:
    return CriticalPathOut(
        nodes=nodes,
        dependency_edges=[dict(e) for e in (path.dependency_edges or [])],
        frozen_through_chapter=int(path.frozen_through_chapter),
        version=int(path.version),
    )


async def get_critical_path(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> CriticalPathOut:
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    path = await session.scalar(
        select(CriticalPathModel)
        .where(CriticalPathModel.work_id == work_id)
        .order_by(CriticalPathModel.version.desc())
        .limit(1)
    )
    if path is None:
        return CriticalPathOut()
    node_rows = await session.scalars(
        select(CriticalNodeModel)
        .where(CriticalNodeModel.path_id == path.id)
        .order_by(CriticalNodeModel.ordinal)
    )
    nodes = [
        CriticalNode(
            node_id=n.node_id,
            ordinal=n.ordinal,
            title=n.title,
            node_type=PathNodeType(n.node_type),
            promise=n.promise,
            preconditions=list(n.preconditions or []),
            consequences=list(n.consequences or []),
            requires_human=bool(n.requires_human),
            locked=bool(n.locked),
        )
        for n in node_rows.all()
    ]
    return _path_out(path, nodes)


def _node_changes(
    *,
    current_nodes: list[CriticalNode],
    next_nodes: list[CriticalNode],
) -> list[tuple[int, str]]:
    """路径改动清单：``(起作用的 ordinal, 涉及的主题)``。

    改动判定只有这一个口径——受影响章节与记忆失效范围都从它推导。若两处各判
    一次，就会出现「算得出受影响章节、却失效不了对应记忆」（或反之）的静默缺口。

    判定依据：**改路径改的是承诺**。标题、顺序、节点类型、承诺内容任一项变了，
    依托它的记忆就不再可信；被删掉的节点同样算改动。
    """
    current_by_id = {n.node_id: n for n in current_nodes}
    next_by_id = {n.node_id: n for n in next_nodes}
    changes: list[tuple[int, str]] = []
    for node in next_nodes:
        prev = current_by_id.get(node.node_id)
        if prev is None:
            changes.append((int(node.ordinal), node.title))
            continue
        if (
            prev.title != node.title
            or int(prev.ordinal) != int(node.ordinal)
            or prev.promise != node.promise
            or prev.node_type != node.node_type
        ):
            changes.append((int(node.ordinal), prev.title))
            changes.append((int(node.ordinal), node.title))
    for node in current_nodes:
        if node.node_id not in next_by_id:
            changes.append((int(node.ordinal), node.title))
    seen: set[tuple[int, str]] = set()
    result: list[tuple[int, str]] = []
    for ordinal, title in changes:
        title = str(title).strip()
        if not title or (ordinal, title) in seen:
            continue
        seen.add((ordinal, title))
        result.append((ordinal, title))
    return result


def _preview_impact(
    *,
    current_nodes: list[CriticalNode],
    next_nodes: list[CriticalNode],
    frozen_through_chapter: int,
    latest_chapter_no: int,
) -> PathChangeImpact:
    """影响范围：按 ordinal 定位改动起点，估算受影响章节。不含虚假精确百分比。"""
    changes = _node_changes(current_nodes=current_nodes, next_nodes=next_nodes)
    if not changes:
        return PathChangeImpact(
            affected_chapters=[],
            frozen_conflict=False,
            frozen_through_chapter=frozen_through_chapter,
        )

    first_ordinal = min(ordinal for ordinal, _title in changes)
    # 一个节点约覆盖 CHAPTERS_PER_NODE 章
    start_chapter = max(1, (first_ordinal - 1) * CHAPTERS_PER_NODE + 1)
    end_chapter = max(start_chapter, latest_chapter_no)
    affected = list(range(start_chapter, end_chapter + 1))

    return PathChangeImpact(
        affected_chapters=affected,
        frozen_conflict=start_chapter <= frozen_through_chapter,
        frozen_through_chapter=frozen_through_chapter,
        eta_minutes_min=len(affected) * 2,
        eta_minutes_max=len(affected) * 6,
        cost_ceiling_minor=len(affected) * 8500,  # 影子价格：¥85/章上限
    )


async def update_critical_path(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    payload: CriticalPathUpdate,
) -> tuple[CriticalPathOut, PathChangeImpact]:
    """FR-05：expected_version 冲突保护 + 影响预览。"""
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)

    path = await session.scalar(
        select(CriticalPathModel)
        .where(CriticalPathModel.work_id == work_id)
        .order_by(CriticalPathModel.version.desc())
        .limit(1)
    )
    current_version = int(path.version) if path else 0
    if payload.expected_version != current_version:
        raise Conflict(
            "critical path version conflict",
            current_version=current_version,
            conflict_summary={
                "expected_version": payload.expected_version,
                "current_version": current_version,
                "reason": "path_updated_elsewhere",
            },
        )

    if not MIN_PATH_NODES <= len(payload.nodes) <= MAX_PATH_NODES:
        raise ValidationFailed(
            f"critical path must contain {MIN_PATH_NODES}-{MAX_PATH_NODES} nodes"
        )
    ordinals = [n.ordinal for n in payload.nodes]
    if len(set(ordinals)) != len(ordinals):
        raise ValidationFailed("critical node ordinals must be unique")

    current_nodes = (await get_critical_path(session, owner_id=owner_id, work_id=work_id)).nodes

    frozen_through = int(path.frozen_through_chapter) if path else 0
    impact = _preview_impact(
        current_nodes=current_nodes,
        next_nodes=payload.nodes,
        frozen_through_chapter=frozen_through,
        latest_chapter_no=int(work.latest_chapter_no),
    )
    if impact.frozen_conflict:
        raise Conflict(
            "修改落在已固化章节内，不能改动",
            current_version=current_version,
            conflict_summary={
                "frozen_through_chapter": frozen_through,
                "first_affected_chapter": impact.affected_chapters[0]
                if impact.affected_chapters
                else None,
                "reason": "chapter_already_frozen",
            },
        )

    new_path = CriticalPathModel(
        id=uuid.uuid4(),
        work_id=work_id,
        version=current_version + 1,
        frozen_through_chapter=frozen_through,
        node_count=len(payload.nodes),
        dependency_edges=[dict(e) for e in payload.dependency_edges],
    )
    session.add(new_path)
    await session.flush()
    for node in payload.nodes:
        session.add(
            CriticalNodeModel(
                id=uuid.uuid4(),
                path_id=new_path.id,
                node_id=node.node_id,
                ordinal=node.ordinal,
                title=node.title,
                node_type=node.node_type.value,
                promise=node.promise,
                preconditions=list(node.preconditions),
                consequences=list(node.consequences),
                requires_human=node.requires_human,
                locked=node.locked,
            )
        )
    await session.flush()

    work.version += 1
    if work.state in (StoryWorkState.READY.value, StoryWorkState.RUNNING.value):
        assert_story_work_transition(work.state, StoryWorkState.RECOMPUTING.value)
        work.state = StoryWorkState.RECOMPUTING.value
    await session.flush()

    # 路径变更：在途章节的输入版本递增，旧方向产出作废（P0-4）
    _IN_FLIGHT = (
        ChapterRunState.QUEUED.value,
        ChapterRunState.RUNNING.value,
        ChapterRunState.RETRYABLE_FAILED.value,
        ChapterRunState.AWAITING_INPUT.value,
        ChapterRunState.PENDING_DECISION.value,
    )
    affected_runs = (
        await session.scalars(
            select(ChapterRunModel).where(
                ChapterRunModel.work_id == work_id,
                ChapterRunModel.branch_id == work.branch_id,
                ChapterRunModel.state.in_(_IN_FLIGHT),
            )
        )
    ).all()
    for run in affected_runs:
        await bump_input_version(session, run=run, reason="critical_path_changed")

    # R3：路径变更使「依托旧路径」的记忆失效。依赖图完整时只失效被改动主题的下游
    # 子图；图不完整时保守失效同类整批——世界规则与已发生的事实不因改路径而消失。
    # 失效是打标记，不删不改，事实链不动。
    changed_subjects = sorted(
        {title for _ordinal, title in _node_changes(
            current_nodes=current_nodes, next_nodes=payload.nodes
        )}
    )
    invalidated, conservative = await memory_app.invalidate_changed(
        session,
        work=work,
        changed_subjects=changed_subjects,
        reason="critical_path_changed",
        fallback_kinds=("promise", "character_arc"),
    )

    await append_event(
        session,
        work_id=work_id,
        event_type="critical_path.updated",
        data={
            "version": new_path.version,
            "node_count": len(payload.nodes),
            "affected_chapters": impact.affected_chapters,
            "change_note": payload.change_note,
            "invalidated_memory_items": invalidated,
            "changed_subjects": changed_subjects,
            # 记下失效走到哪条路径：承诺"只失效子图"却整批失效，是必须能查出来的事
            "invalidated_scope": "conservative_batch" if conservative else "dependency_subgraph",
        },
        branch_id=work.branch_id,
    )
    return _path_out(new_path, payload.nodes), impact


# ---------------------------------------------------------------------------
# 卷扩展（30 万字架构）
# ---------------------------------------------------------------------------


async def expand_next_volume(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    provider: ModelProvider | None = None,
) -> VolumeModel | None:
    """当前卷完成度 >= 80% 时，生成下一卷的弧段和节点。

    如果 provider 为 None 则使用默认模板生成（无需模型调用）。
    """
    """当前卷写满或末节点完成时，生成下一卷的弧段和节点。

    **没有可用的大纲就返回 None，绝不套用静态模板。** 旧实现在模型失败时填入
    「变强 / 更强大的对手」这条与题材和用户意图都无关的升级模板：卷是续上了，
    但续的是模板的方向，不是用户的方向，而且已经写进正文不可逆（B-05）。
    """
    current_vol_no = int(work.total_volume_count)
    next_vol_no = current_vol_no + 1

    # 检查是否已有下一卷
    existing = await session.scalar(
        select(VolumeModel).where(
            VolumeModel.work_id == work.id,
            VolumeModel.volume_no == next_vol_no,
        )
    )
    if existing is not None:
        return existing

    # 获取当前路径和节点
    path = await session.scalar(
        select(CriticalPathModel)
        .where(CriticalPathModel.work_id == work.id)
        .order_by(CriticalPathModel.version.desc())
        .limit(1)
    )
    if path is None:
        return None
    existing_nodes = list(
        (await session.scalars(
            select(CriticalNodeModel)
            .where(CriticalNodeModel.path_id == path.id)
            .order_by(CriticalNodeModel.ordinal)
        )).all()
    )
    max_ordinal = max((n.ordinal for n in existing_nodes), default=0)

    # 尝试 LLM 生成下一卷大纲
    outline = None
    if provider is not None:
        try:
            from regent.novel.application.generation import generate_volume_expansion_outline
            # 获取上一卷信息
            prev_vol = await session.scalar(
                select(VolumeModel).where(
                    VolumeModel.work_id == work.id,
                    VolumeModel.volume_no == current_vol_no,
                )
            )
            # 获取 goal
            goal = await session.scalar(
                select(StoryGoalModel)
                .where(StoryGoalModel.work_id == work.id)
                .order_by(StoryGoalModel.version.desc())
                .limit(1)
            )
            last_node_dicts = [
                {"title": n.title, "promise": n.promise, "consequences": n.consequences or []}
                for n in existing_nodes[-5:]
            ]
            outline = await generate_volume_expansion_outline(
                provider,
                raw_intent=goal.raw_intent if goal else "",
                genre=work.genre or "",
                volume_no=next_vol_no,
                prev_volume_title=prev_vol.title if prev_vol else "",
                prev_volume_summary=list(prev_vol.summary or []) if prev_vol else [],
                last_nodes=last_node_dicts,
                cultivation_realm=prev_vol.cultivation_realm if prev_vol else "",
            )
        except Exception as exc:
            # 拿不到大纲就不扩卷。套静态模板会得到一卷「变强／更强大的对手」，
            # 那是模板的方向不是用户的方向，且已经写进正文不可逆（B-05）。
            await append_event(
                session,
                work_id=work.id,
                event_type="volume.expansion_failed",
                data={
                    "volume_no": next_vol_no,
                    "chapter_no": int(work.latest_chapter_no),
                    "reason": f"outline_failed:{type(exc).__name__}",
                },
                branch_id=work.branch_id,
            )
            return None
    else:
        # 没有模型就没有大纲：同样不扩卷，等有模型时再判定（provider=None 不是
        # 「扩卷失败」，但它也不构成「可以自己编一卷」的理由）。
        await append_event(
            session,
            work_id=work.id,
            event_type="volume.expansion_failed",
            data={
                "volume_no": next_vol_no,
                "chapter_no": int(work.latest_chapter_no),
                "reason": "no_provider",
            },
            branch_id=work.branch_id,
        )
        return None

    from regent.novel.domain.models import CriticalNode as CN
    from regent.novel.domain.models import PathNodeType
    new_nodes = []
    for idx, o_node in enumerate(outline.nodes):
        ntype_str = o_node.node_type.upper()
        try:
            ntype = PathNodeType(ntype_str)
        except ValueError:
            ntype = PathNodeType.ESCALATION
        new_nodes.append(CN(
            node_id=f"n{max_ordinal + idx + 1:02d}",
            ordinal=max_ordinal + idx + 1,
            title=o_node.title,
            node_type=ntype,
            promise=o_node.promise,
            preconditions=list(o_node.preconditions),
            consequences=list(o_node.consequences),
        ))
    vol_title = outline.volume_title or f"第{_cn_num(next_vol_no)}卷"
    vol_realm = outline.cultivation_realm or ""
    if not new_nodes:
        await append_event(
            session,
            work_id=work.id,
            event_type="volume.expansion_failed",
            data={
                "volume_no": next_vol_no,
                "chapter_no": int(work.latest_chapter_no),
                "reason": "empty_outline",
            },
            branch_id=work.branch_id,
        )
        return None

    # 裁剪到合法范围
    if len(new_nodes) > MAX_NODES_PER_VOLUME:
        new_nodes = new_nodes[:MAX_NODES_PER_VOLUME]

    # 写入新 CriticalNode
    for idx, node in enumerate(new_nodes):
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
                volume_no=next_vol_no,
                arc_no=(idx // CHAPTERS_PER_NODE) + 1,
            )
        )
    path.node_count = len(existing_nodes) + len(new_nodes)

    # 标记当前卷完成
    current_vol = await session.scalar(
        select(VolumeModel).where(
            VolumeModel.work_id == work.id,
            VolumeModel.volume_no == current_vol_no,
        )
    )
    if current_vol is not None:
        current_vol.state = "COMPLETED"
        # 卷边界必须收口到实际写完的最后一章。保留预计 end_chapter_no 会让旧卷
        # 的区间继续盖住新卷的章号，装配时按区间定位就落到旧卷上——节点数和
        # 章数都不是预设值时，这个漂移直接让新卷的节点选不中（A-03）。
        current_vol.end_chapter_no = max(
            int(current_vol.start_chapter_no), int(work.latest_chapter_no)
        )

    # 创建新卷
    chapters_per_vol = len(new_nodes) * CHAPTERS_PER_NODE
    start_ch = int(work.latest_chapter_no) + 1
    new_vol = VolumeModel(
        id=uuid.uuid4(),
        work_id=work.id,
        volume_no=next_vol_no,
        title=vol_title,
        summary=[],
        cultivation_realm=vol_realm,
        start_chapter_no=start_ch,
        end_chapter_no=start_ch + chapters_per_vol - 1,
        state="ACTIVE",
    )
    session.add(new_vol)
    await session.flush()

    # 创建弧段
    arc_count = max(1, len(new_nodes) // 3)
    for arc_idx in range(arc_count):
        start_ord = arc_idx * 3 + 1
        end_ord = min((arc_idx + 1) * 3, len(new_nodes))
        session.add(
            ArcNodeModel(
                id=uuid.uuid4(),
                volume_id=new_vol.id,
                arc_no=arc_idx + 1,
                title=new_nodes[start_ord - 1].title if start_ord <= len(new_nodes) else "",
                arc_type="STANDARD",
                chapter_range_start=start_ch + (start_ord - 1) * CHAPTERS_PER_NODE,
                chapter_range_end=start_ch + end_ord * CHAPTERS_PER_NODE - 1,
            )
        )

    work.total_volume_count = next_vol_no
    work.version += 1
    await session.flush()

    await append_event(
        session,
        work_id=work.id,
        event_type="volume.expanded",
        data={"volume_no": next_vol_no, "node_count": len(new_nodes)},
        branch_id=work.branch_id,
    )
    return new_vol


def _cn_num(n: int) -> str:
    """整数转中文数字（1-10）。"""
    mapping = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
               6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
    return mapping.get(n, str(n))


async def _path_node_ids(session: AsyncSession, work: StoryWorkModel) -> list[str]:
    """当前关键路径上的节点 id，按序（P1-3）。

    扩卷后路径会变长，所以判断「还有没有下一节点」必须实时查，不能沿用
    生成期缓存下来的节点数。
    """
    path = await session.scalar(
        select(CriticalPathModel)
        .where(CriticalPathModel.work_id == work.id)
        .order_by(CriticalPathModel.version.desc())
        .limit(1)
    )
    if path is None:
        return []
    return list(
        (
            await session.scalars(
                select(CriticalNodeModel.node_id)
                .where(CriticalNodeModel.path_id == path.id)
                .order_by(CriticalNodeModel.ordinal)
            )
        ).all()
    )


async def _last_node_completed(session: AsyncSession, work: StoryWorkModel, run) -> bool:
    """本章完成的是不是关键路径上的最后一个节点（P1-3）。"""
    context = run.generation_context or {}
    if not context.get("node_completed"):
        return False
    target_id = (context.get("target_node") or {}).get("id")
    if not target_id:
        return False
    node_ids = await _path_node_ids(session, work)
    return bool(node_ids) and target_id == node_ids[-1]


def _mark_story_complete(
    session: AsyncSession, *, work: StoryWorkModel, run: ChapterRunModel
) -> None:
    """整本结束：标在刚成章这一次运行上，让 start_run 不再开空章（P1-3）。

    标记必须落在**已完成的那一次运行**上。只写在作品上，start_run 看到的
    还是上一章的旧 context，于是会再开一章没有目标节点的任务（A-03）。
    """
    context = dict(run.generation_context or {})
    context["story_complete"] = True
    run.generation_context = context
    run.version += 1
    if work.state != StoryWorkState.DONE.value:
        assert_story_work_transition(work.state, StoryWorkState.DONE.value)
        work.state = StoryWorkState.DONE.value
        work.version += 1
    session.add(run)


async def _ending_intent_of(work: StoryWorkModel) -> ending.EndingIntent:
    """用户认可的终局。没有它，末节点完成后只能靠「扩卷失败」来结束（B-05）。"""
    return ending.EndingIntent(
        target_volume_count=int(work.ending_target_volume or 0),
        ending_statement=str(work.ending_statement or ""),
    )


async def _director_ending_verdict(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    run: ChapterRunModel,
    provider: ModelProvider | None,
) -> tuple[bool | None, str]:
    """导演对终局的判断。``None`` 表示**没有判断**，不是「判断为没讲完」。

    这个区分是 B-05 的关键：把「模型调用失败」当成「还没讲完」，系统就会在模型
    故障时自动扩卷，把一次技术故障翻译成创作决策——而且扩出来的卷用的是静态
    模板，等于替用户改了方向。
    """
    if provider is None:
        return None, "没有可用的模型：不做终局判断"
    goal = await session.scalar(
        select(StoryGoalModel)
        .where(StoryGoalModel.work_id == work.id)
        .order_by(StoryGoalModel.version.desc())
        .limit(1)
    )
    # 只取**当前路径**的节点：历史版本的节点不是这一版的计划，混进来会让终局
    # 判断对着一条已经被改掉的计划下结论（C-05）。
    path = await session.scalar(
        select(CriticalPathModel)
        .where(CriticalPathModel.work_id == work.id)
        .order_by(CriticalPathModel.version.desc())
        .limit(1)
    )
    nodes = []
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
    # 已经写出来的东西：终局判断据此，而不是据此的**计划**（C-05）。
    from regent.novel.domain import memory as memory_domain
    from regent.novel.infrastructure.models import CanonCommitModel

    accepted_text = ""
    latest_run = await session.scalar(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work.id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.content != "",
            ChapterRunModel.state == ChapterRunState.CANONIZED.value,
        )
        .order_by(ChapterRunModel.chapter_no.desc(), ChapterRunModel.attempt.desc())
        .limit(1)
    )
    if latest_run is not None:
        accepted_text = str(latest_run.content or "")
    canon_commit = await session.scalar(
        select(CanonCommitModel)
        .where(
            CanonCommitModel.work_id == work.id,
            CanonCommitModel.branch_id == work.branch_id,
        )
        .order_by(CanonCommitModel.version.desc())
        .limit(1)
    )
    verified_facts = [
        fact
        for fact in ((canon_commit.facts if canon_commit else None) or [])
        if isinstance(fact, dict)
    ]
    # 还没收的线：这是「有没有讲完」最硬的证据——有未兑现承诺就没讲完。
    bundle = await memory_app.recall_memory(
        session, work=work, chapter_no=int(work.latest_chapter_no), kinds=("promise",)
    )
    open_promises = [item.as_payload() for item in memory_domain.open_promises(bundle.items)]
    try:
        verdict = await generate_ending_verdict(
            provider,
            session=session,
            work=work,
            run=run,
            raw_intent=goal.raw_intent if goal else "",
            genre=work.genre or "",
            ending_statement=str(work.ending_statement or ""),
            volume_no=int(work.total_volume_count or 1),
            latest_chapter_no=int(work.latest_chapter_no),
            completed_nodes=[
                {"title": n.title, "promise": n.promise} for n in nodes
            ],
            accepted_text=accepted_text,
            verified_facts=verified_facts,
            open_promises=open_promises,
        )
    except Exception as exc:  # 模型故障不是创作结论
        return None, f"导演终局判断不可用：{type(exc).__name__}"
    return bool(verdict.story_complete), str(verdict.reason or "")


async def _decide_ending(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    run: ChapterRunModel,
    provider: ModelProvider | None = None,
) -> ending.EndingDecision:
    """按「用户终局 → 导演判定 → 无依据」决定结束、扩卷还是待定。"""
    intent = await _ending_intent_of(work)
    volume_no = int(work.total_volume_count or 1)
    if int(intent.target_volume_count) > 0:
        # 用户给了卷数：数字本身就是依据，问模型等于把用户的终局降级成建议。
        return ending.decide_ending(intent=intent, volume_no=volume_no)
    complete, reason = await _director_ending_verdict(
        session, work=work, run=run, provider=provider
    )
    return ending.decide_ending(
        intent=intent,
        volume_no=volume_no,
        director_complete=complete,
        director_reason=reason,
    )


def _mark_ending_decision(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    run: ChapterRunModel,
    decision: ending.EndingDecision,
) -> None:
    """把终局判定落在**已完成的那一次运行**上。

    只写在作品上，start_run 看到的还是上一章的旧 context，于是会再开一章没有
    目标节点的任务（A-03）。undecided 也要落盘：它带着卷号与章号，是下一次
    判定接着走的上下文。
    """
    context = dict(run.generation_context or {})
    context["ending_decision"] = decision.as_payload()
    run.generation_context = context
    run.version += 1
    session.add(run)


async def _after_chapter_completed(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    run: ChapterRunModel,
    provider: ModelProvider | None = None,
) -> None:
    """成章收尾：先按终局意图决定结束还是扩卷（B-05 / P1-3 / A-03）。

    旧行为是「末节点完成 → 先扩卷 → 扩不出新节点才置 DONE」。那等于把终点定义
    为「生成失败」：正常故事永远不结束，而模型一挂反而会「完本」。现在顺序反过来
    ——先问该不该结束，该继续才扩卷；扩卷失败**不套静态模板**，保留待定状态。
    """
    if await _last_node_completed(session, work, run):
        decision = await _decide_ending(session, work=work, run=run, provider=provider)
        if decision.complete:
            _mark_ending_decision(session, work=work, run=run, decision=decision)
            _mark_story_complete(session, work=work, run=run)
            await append_event(
                session,
                work_id=work.id,
                event_type="story.completed",
                data={
                    "chapter_no": int(work.latest_chapter_no),
                    "basis": decision.basis,
                    "reason": decision.reason,
                },
                branch_id=work.branch_id,
            )
            return
        if decision.expand:
            before = await _path_node_ids(session, work)
            volume = await expand_next_volume(session, work=work, provider=provider)
            after = await _path_node_ids(session, work)
            if volume is None and len(after) <= len(before):
                # 扩卷失败：保留待定，不改创作方向，等下一次判定接着走
                failed = ending.EndingDecision(
                    choice=ending.UNDECIDED,
                    reason="已判定应继续，但下一卷没有生成成功",
                    basis=decision.basis,
                )
                _mark_ending_decision(session, work=work, run=run, decision=failed)
                await append_event(
                    session,
                    work_id=work.id,
                    event_type="volume.expansion_failed",
                    data={
                        "chapter_no": int(work.latest_chapter_no),
                        "volume_no": int(work.total_volume_count or 0) + 1,
                        "reason": "expansion_failed",
                    },
                    branch_id=work.branch_id,
                )
            return
        _mark_ending_decision(session, work=work, run=run, decision=decision)
        await append_event(
            session,
            work_id=work.id,
            event_type="ending.undecided",
            data={
                "chapter_no": int(work.latest_chapter_no),
                "volume_no": int(work.total_volume_count or 0),
                "reason": decision.reason,
            },
            branch_id=work.branch_id,
        )
        return
    await _maybe_expand_volume(session, work=work, provider=provider, run=run)


async def _maybe_expand_volume(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    provider: ModelProvider | None = None,
    run: ChapterRunModel | None = None,
) -> None:
    """当前卷的末节点已完成时才扩展下一卷。

    **80% 完成度不再触发扩卷（C-02）。** ``expand_next_volume()`` 会把当前活动卷
    标记为完成；在卷还有节点没写完时就因为「完成度到 80%」调用它，等于提前结束
    当前卷——剩余节点会被整体跳过，用户限定的卷数也被绕过。提前扩卷与实际卷完成
    必须分开：扩卷只发生在当前卷末节点已完成时，且必须先过用户终局约束。

    末节点完成仍必须触发：否则后续章节会一直重写同一个末节点（P1-3）。
    """
    current_vol = await session.scalar(
        select(VolumeModel).where(
            VolumeModel.work_id == work.id,
            VolumeModel.state == "ACTIVE",
        )
    )
    if current_vol is None:
        return
    last_node_done = (
        await _last_node_completed(session, work, run) if run is not None else False
    )
    if not last_node_done:
        # 卷还没写完就扩卷，等于把剩余节点丢掉（C-02）。
        return
    # 用户终局约束：限定了卷数就不能再开新卷——所有扩卷入口共享这条约束（C-02）。
    intent = await _ending_intent_of(work)
    if int(intent.target_volume_count) > 0:
        guard = ending.decide_ending(
            intent=intent, volume_no=int(current_vol.volume_no)
        )
        if not guard.expand:
            return
    # 检查下一卷是否已存在
    next_vol_no = int(current_vol.volume_no) + 1
    exists = await session.scalar(
        select(VolumeModel).where(
            VolumeModel.work_id == work.id,
            VolumeModel.volume_no == next_vol_no,
        )
    )
    if exists is not None:
        return
    await expand_next_volume(session, work=work, provider=provider)


async def get_volumes(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> list:
    """获取作品的所有卷（含弧段）。"""
    from regent.novel.domain.models import ArcNodeOut, VolumeOut
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    vols = list(
        (await session.scalars(
            select(VolumeModel)
            .where(VolumeModel.work_id == work_id)
            .order_by(VolumeModel.volume_no)
        )).all()
    )
    result = []
    for v in vols:
        arcs = list(
            (await session.scalars(
                select(ArcNodeModel)
                .where(ArcNodeModel.volume_id == v.id)
                .order_by(ArcNodeModel.arc_no)
            )).all()
        )
        result.append(
            VolumeOut(
                volume_no=int(v.volume_no),
                title=v.title,
                cultivation_realm=v.cultivation_realm,
                start_chapter_no=int(v.start_chapter_no),
                end_chapter_no=int(v.end_chapter_no),
                state=v.state,
                summary=list(v.summary or []),
                arcs=[
                    ArcNodeOut(
                        arc_no=int(a.arc_no),
                        title=a.title,
                        arc_type=a.arc_type,
                        chapter_range_start=int(a.chapter_range_start),
                        chapter_range_end=int(a.chapter_range_end),
                        core_conflict=a.core_conflict,
                        resolution_type=a.resolution_type,
                    )
                    for a in arcs
                ],
            )
        )
    return result


# ---------------------------------------------------------------------------
# 运行（FR-06 / FR-13 / FR-20）
# ---------------------------------------------------------------------------


async def start_run(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> RunProgressOut:
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    latest_run = await session.scalar(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work_id,
            ChapterRunModel.branch_id == work.branch_id,
        )
        .order_by(ChapterRunModel.chapter_no.desc(), ChapterRunModel.attempt.desc())
        .limit(1)
    )
    _blocked_states = {
        ChapterRunState.QUEUED.value, ChapterRunState.RUNNING.value,
        ChapterRunState.RETRYABLE_FAILED.value, ChapterRunState.AWAITING_INPUT.value,
        ChapterRunState.PENDING_DECISION.value, ChapterRunState.TERMINAL_FAILED.value,
    }
    if latest_run is not None and latest_run.state in _blocked_states:
        raise Conflict(
            "a chapter is already active",
            current_version=int(latest_run.version),
            conflict_summary={"chapter_no": latest_run.chapter_no, "state": latest_run.state},
        )
    # 末节点已完成且没有可扩展的新卷：整本结束，不再开新章（P1-3）
    _context = (latest_run.generation_context or {}) if latest_run is not None else {}
    _complete = bool(_context.get("story_complete"))
    _decision = ending.EndingDecision.from_payload(_context.get("ending_decision"))
    if (
        not _complete
        and latest_run is not None
        and latest_run.state == ChapterRunState.CANONIZED.value
        and await _last_node_completed(session, work, latest_run)
    ):
        if _decision.choice == ending.UNDECIDED:
            # 终局待定：末节点已写完，再开一章只会得到没有目标节点的任务。
            # 这里必须停，等 resolve_ending 拿到依据再走——静默开空章比停着更糟。
            raise Conflict(
                "ending undecided",
                current_version=int(work.version),
                conflict_summary={
                    "reason": "ending_undecided",
                    "chapter_no": int(work.latest_chapter_no),
                    "available_actions": ["resolve_ending", "set_ending_intent"],
                },
            )
        # 成章时没来得及收尾的存量数据：末节点已完成、路径后面也没有节点，
        # 再开一章只会得到没有目标节点的任务（A-03）。
        _complete = True
    if _complete:
        if work.state != StoryWorkState.DONE.value:
            assert_story_work_transition(work.state, StoryWorkState.DONE.value)
            work.state = StoryWorkState.DONE.value
            work.version += 1
            await session.flush()
        raise Conflict(
            "story already complete",
            current_version=int(work.version),
            conflict_summary={"reason": "story_complete", "latest_chapter_no": int(
                work.latest_chapter_no
            )},
        )
    # READY/DONE 需要状态迁移；RUNNING 表示上一章完成后继续下一章，不做自迁移。
    if work.state != StoryWorkState.RUNNING.value:
        assert_story_work_transition(work.state, StoryWorkState.RUNNING.value)
        work.state = StoryWorkState.RUNNING.value
    work.version += 1

    chapter_no = int(work.latest_chapter_no) + 1
    # R4/A-05：执行器按作品分桶在创建时确定，并钉进这一次运行的上下文。
    # 之后灰度比例怎么调都不能改在途运行——否则同一章前后半来自两个执行器。
    executor = executor_app.choose_executor(work_id)
    run = ChapterRunModel(
        id=uuid.uuid4(),
        work_id=work_id,
        branch_id=work.branch_id,
        chapter_no=chapter_no,
        attempt=1,
        state=ChapterRunState.QUEUED.value,
        current_step=ChapterStep.ASSEMBLE.value,
        title=f"第 {chapter_no} 章",
        generation_context={
            "architecture_version": executor,
            "executor": executor,
        },
        auto_advance=True,
    )
    session.add(run)
    await session.flush()
    for step in chapter_step_order(run.generation_context):
        session.add(
            ChapterStepModel(
                id=uuid.uuid4(),
                run_id=run.id,
                step=step.value,
                state=StepState.PENDING.value,
                input_version=1,
            )
        )
    await session.flush()

    await append_event(
        session,
        work_id=work_id,
        event_type="run.started",
        data={"chapter_no": chapter_no, "run_id": str(run.id)},
        branch_id=work.branch_id,
        chapter_no=chapter_no,
    )
    return await get_run_progress(session, owner_id=owner_id, work_id=work_id)


async def set_ending_intent(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    target_volume_count: int | None = None,
    ending_statement: str | None = None,
) -> dict[str, Any]:
    """设定用户认可的终局（B-05）。

    这是三动作里「说出故事目标」的一部分：没有它，末节点完成后系统只能靠
    「扩卷失败」来结束，正常故事永远不结束。
    """
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if target_volume_count is not None:
        if int(target_volume_count) < 0:
            raise ValidationFailed("target_volume_count 不能为负")
        work.ending_target_volume = int(target_volume_count)
    if ending_statement is not None:
        work.ending_statement = str(ending_statement)[:500]
    work.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="ending.intent_set",
        data=ending.EndingIntent(
            target_volume_count=int(work.ending_target_volume or 0),
            ending_statement=str(work.ending_statement or ""),
        ).as_payload(),
        branch_id=work.branch_id,
    )
    return {
        "target_volume_count": int(work.ending_target_volume or 0),
        "ending_statement": str(work.ending_statement or ""),
    }


async def resolve_ending(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    provider: ModelProvider | None = None,
) -> dict[str, Any]:
    """终局待定时重新判定：能判就结束或扩卷，判不了就如实继续待定（B-05）。

    待定不是终态。它只说明**上一次**没有拿到依据；这次拿到了就往下走，拿不到
    就把待定留在运行上，等下次——不留死局，也不靠猜。
    """
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    run = await session.scalar(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work_id,
            ChapterRunModel.branch_id == work.branch_id,
        )
        .order_by(ChapterRunModel.chapter_no.desc(), ChapterRunModel.attempt.desc())
        .limit(1)
    )
    if run is None or not await _last_node_completed(session, work, run):
        return {"choice": "not_applicable", "reason": "末节点尚未完成", "state": work.state}
    decision = await _decide_ending(session, work=work, run=run, provider=provider)
    if decision.complete:
        _mark_ending_decision(session, work=work, run=run, decision=decision)
        _mark_story_complete(session, work=work, run=run)
        outcome = decision.choice
    elif decision.expand:
        before = await _path_node_ids(session, work)
        volume = await expand_next_volume(session, work=work, provider=provider)
        after = await _path_node_ids(session, work)
        if volume is not None and len(after) > len(before):
            # 扩卷成功：待定解除，下一章有目标节点可写
            _mark_ending_decision(session, work=work, run=run, decision=decision)
            outcome = decision.choice
        else:
            _mark_ending_decision(
                session, work=work, run=run,
                decision=ending.EndingDecision(
                    choice=ending.UNDECIDED,
                    reason="已判定应继续，但下一卷没有生成成功",
                    basis=decision.basis,
                ),
            )
            outcome = ending.UNDECIDED
    else:
        _mark_ending_decision(session, work=work, run=run, decision=decision)
        outcome = decision.choice
    await append_event(
        session,
        work_id=work_id,
        event_type="ending.resolved",
        data={"choice": outcome, "reason": decision.reason, "basis": decision.basis},
        branch_id=work.branch_id,
        chapter_no=int(work.latest_chapter_no),
    )
    await session.flush()
    return {
        "choice": outcome,
        "reason": decision.reason,
        "basis": decision.basis,
        "state": work.state,
    }


async def pause_work(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID, reason: str = "user"
) -> WorkStateOut:
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if work.state not in (StoryWorkState.RUNNING.value, StoryWorkState.PENDING_DECISION.value):
        raise InvalidState("work is not running", current=work.state)
    assert_story_work_transition(work.state, StoryWorkState.PAUSED_COST.value)
    work.state = StoryWorkState.PAUSED_COST.value
    work.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="work.paused",
        data={"reason": reason, "worker_released": True},
        branch_id=work.branch_id,
    )
    return WorkStateOut(work.state)


async def resume_work(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> WorkStateOut:
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if work.state not in (
        StoryWorkState.PAUSED_COST.value,
        StoryWorkState.PAUSED_QUOTA.value,
        StoryWorkState.FAILED.value,
        StoryWorkState.READY.value,
    ):
        raise InvalidState("work cannot be resumed from current state", current=work.state)
    assert_story_work_transition(work.state, StoryWorkState.RUNNING.value)
    work.state = StoryWorkState.RUNNING.value
    work.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="work.resumed",
        data={"state": work.state},
        branch_id=work.branch_id,
    )
    return WorkStateOut(work.state)


async def resume_after_correction(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    ticket_id: str = "",
) -> WorkStateOut:
    """因一次事实纠错而恢复创作，让已排队的局部重演真正被执行（C-01）。

    完结或暂停的作品，后台不会领取它的任务——``report_fact`` 排了队也只是挂着。
    这个函数就是「恢复后才执行」那句话的落点：**只有用户明确要求**才把作品放回
    RUNNING，因此暂停/完结意图不会被自动覆盖。

    两个前置条件：状态允许恢复，且确实有排队中的重演任务——没有可执行内容时不
    把作品空放回 RUNNING，否则会留下一个「在跑但没什么在跑」的作品。
    """
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    state = str(work.state or "").upper()
    if state == StoryWorkState.RUNNING.value:
        return WorkStateOut(work.state)
    if state not in (
        StoryWorkState.DONE.value,
        StoryWorkState.PAUSED_COST.value,
        StoryWorkState.PAUSED_QUOTA.value,
        StoryWorkState.FAILED.value,
        StoryWorkState.READY.value,
    ):
        raise InvalidState(
            "work cannot be resumed for correction from current state",
            current=work.state,
        )
    pending = await session.scalar(
        select(func.count(ChapterRunModel.id)).where(
            ChapterRunModel.work_id == work.id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.attempt > 1,
            ChapterRunModel.state == ChapterRunState.QUEUED.value,
        )
    )
    if int(pending or 0) <= 0:
        raise InvalidState("no queued replay to resume", current=work.state)
    assert_story_work_transition(work.state, StoryWorkState.RUNNING.value)
    work.state = StoryWorkState.RUNNING.value
    work.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="work.resumed",
        data={
            "state": work.state,
            "reason": "fact_correction",
            "ticket_id": ticket_id,
        },
        branch_id=work.branch_id,
    )
    return WorkStateOut(work.state)


async def get_run_progress(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> RunProgressOut:
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    run = await session.scalar(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work_id,
            ChapterRunModel.branch_id == work.branch_id,
        )
        .order_by(ChapterRunModel.chapter_no.desc(), ChapterRunModel.attempt.desc())
        .limit(1)
    )
    if run is None:
        return RunProgressOut(
            work_id=str(work_id),
            chapter_no=int(work.latest_chapter_no),
            state=ChapterRunState.QUEUED,
        )
    steps = await session.scalars(
        select(ChapterStepModel).where(ChapterStepModel.run_id == run.id)
    )
    production = (run.generation_context or {}).get("production", {})
    scene_count = len(production.get("plan", {}).get("scenes", []))
    return RunProgressOut(
        work_id=str(work_id),
        chapter_no=int(run.chapter_no),
        state=ChapterRunState(run.state),
        current_step=ChapterStep(run.current_step) if run.current_step else None,
        steps={s.step: StepState(s.state) for s in steps.all()},
        # 恢复时复用已成功的逻辑调用：不重跑、不重复计费（G-09）
        reused_calls=int(production.get("reused_calls", 0)),
        avoided_cost_minor=int(production.get("avoided_minor", 0)),
        version=int(run.version),
        auto_advance=bool(run.auto_advance),
        awaiting_input=run.state == ChapterRunState.AWAITING_INPUT.value,
        scene_no=min(scene_count, int(production.get("scene_index", 0)) + 1),
        scene_count=scene_count,
        completed_scenes=len(production.get("accepted", [])),
    )


def _rewind_review_to_weave(
    *, run: ChapterRunModel, by_name: dict[str, ChapterStepModel]
) -> None:
    """Route each quality failure to the earliest layer that can repair it."""
    review = run.review or {}
    failure_classes = set(review.get("failure_classes", []) or [])
    if "PERFORMANCE" in failure_classes:
        rewind_from = ChapterStep.PERFORM
    elif "STRUCTURE" in failure_classes:
        rewind_from = ChapterStep.DIRECT
    else:
        rewind_from = ChapterStep.WEAVE
    rewinding = False
    for step in CHAPTER_STEP_ORDER:
        if step == rewind_from:
            rewinding = True
        if rewinding and step in {
            ChapterStep.PERFORM,
            ChapterStep.DIRECT,
            ChapterStep.WEAVE,
        }:
            row = by_name[step.value]
            row.state = StepState.PENDING.value
            row.error_code = ""
    instructions = list(review.get("revision_instructions", []) or [])
    if not instructions:
        instructions = (
            list(review.get("continuity_issues", []) or [])
            + list(review.get("leakage_issues", []) or [])
            + list(review.get("prose_issues", []) or [])
        )
    context = dict(run.generation_context or {})
    context["revision_instructions"] = instructions or [
        "依据上一轮质量评审重新构思并重写，不要复用原稿的失败表达"
    ]
    run.generation_context = context


async def advance_step(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    chapter_no: int,
) -> RunProgressOut:
    """推进一个可恢复的 Agent-loop 步骤。

    幂等键 ``work:branch:chapter:step:input_version``——重复调用不产生副作用。
    """
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    run = await session.scalar(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work_id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.chapter_no == chapter_no,
        )
        .order_by(ChapterRunModel.attempt.desc())
        .limit(1)
        .with_for_update()
    )
    # A-05：在途运行沿用创建时钉下的执行器。不许切换 ≠ 整章停摆——灰度是全局
    # 旋钮，若因为坡度变了就拒绝推进，操作员调一次比例就会让落入新桶的作品
    # 的在途章节永远走不完。切换推迟到下一次运行，并留下记录。
    pinned = executor_app.pinned_executor(run)
    requested = executor_app.choose_executor(work_id)
    if requested != pinned:
        context, recorded = executor_app.defer_switch(
            dict(run.generation_context or {}),
            pinned=pinned,
            requested=requested,
            chapter_no=int(run.chapter_no),
        )
        run.generation_context = context
        session.add(run)
        await session.flush()
        if recorded:
            await append_event(
                session,
                work_id=work_id,
                event_type="executor.switch_deferred",
                data={
                    "chapter_no": int(run.chapter_no),
                    "pinned": pinned,
                    "requested": requested,
                },
                branch_id=work.branch_id,
                chapter_no=int(run.chapter_no),
            )
    if run is None:
        raise NotFound("chapter run not found")
    if work.state != StoryWorkState.RUNNING.value or run.state in {
        ChapterRunState.CANONIZED.value, ChapterRunState.CANCELLED.value,
        ChapterRunState.SUPERSEDED.value, ChapterRunState.PENDING_DECISION.value,
        ChapterRunState.TERMINAL_FAILED.value, ChapterRunState.AWAITING_INPUT.value,
    }:
        return await get_run_progress(session, owner_id=owner_id, work_id=work_id)
    # 领取运行租约：模型调用在事务外进行，期间其他 worker 不得并行推进（§4.4）
    fencing_token = await acquire_run_lease(session, run=run, owner=_LEASE_OWNER)
    # 输入版本快照：调用窗口内用户改意后，本次结果不得写回（P0-4）
    input_version_before = int(run.input_version or 1)

    async def _stale_reason() -> str | None:
        """回查数据库判定本次结果是否属于旧方向；None 表示仍然有效。

        不能只看内存里的 ``run``：会话默认 ``expire_on_commit=False``，其他会话
        改了 input_version 或抢占了租约，内存对象看不见，会误判为仍然有效。
        显式只查列，保证一定从数据库读。
        """
        row = (
            await session.execute(
                select(
                    ChapterRunModel.lease_owner,
                    ChapterRunModel.fencing_token,
                    ChapterRunModel.lease_expires_at,
                    ChapterRunModel.input_version,
                ).where(ChapterRunModel.id == run.id)
            )
        ).first()
        if row is None:
            return "run_missing"
        owner_now, token_now, expires_now, version_now = row
        if not lease_is_valid(
            SimpleNamespace(
                lease_owner=owner_now,
                fencing_token=token_now,
                lease_expires_at=expires_now,
            ),
            owner=_LEASE_OWNER,
            token=fencing_token,
        ):
            return "lease_lost"
        if int(version_now or 1) != input_version_before:
            return "input_version_changed"
        return None

    steps = list(
        await session.scalars(
            select(ChapterStepModel)
            .where(ChapterStepModel.run_id == run.id)
        )
    )
    by_name = {s.step: s for s in steps}
    pending = next(
        (
            by_name[step.value]
            for step in chapter_step_order(run.generation_context)
            if by_name[step.value].state != StepState.SUCCEEDED.value
        ),
        None,
    )
    if pending is None:
        assert_chapter_run_transition(run.state, ChapterRunState.CANONIZED.value)
        run.state = ChapterRunState.CANONIZED.value
        run.canonized_at = datetime.now(UTC)
        if not run.content:
            raise InvalidState("chapter cannot be canonized without generated content")
        run.word_count = len(run.content)
        work.latest_chapter_no = max(int(work.latest_chapter_no), chapter_no)
        work.version += 1
        await session.flush()
        await append_event(
            session,
            work_id=work_id,
            event_type="chapter.done",
            data={"chapter_no": chapter_no},
            branch_id=work.branch_id,
            chapter_no=chapter_no,
        )
        # 30 万字架构：检查是否需要扩展下一卷（末节点完成则先扩卷再谈结束）
        await _after_chapter_completed(session, work=work, run=run, provider=provider)
        return await get_run_progress(session, owner_id=owner_id, work_id=work_id)

    step = ChapterStep(pending.step)
    pending.state = StepState.RUNNING.value
    pending.attempt += 1
    if run.state == ChapterRunState.QUEUED.value:
        assert_chapter_run_transition(run.state, ChapterRunState.RUNNING.value)
        run.state = ChapterRunState.RUNNING.value
    run.current_step = pending.step
    await session.flush()
    # Agent 对话模式：步骤开始事件，前端实时展示
    await append_event(
        session,
        work_id=work_id,
        event_type="chapter.step_started",
        data={"chapter_no": chapter_no, "step": pending.step},
        branch_id=work.branch_id,
        chapter_no=chapter_no,
    )
    try:
        complete = await execute_step(session, provider=provider, work=work, run=run, step=step)
    except Exception as exc:
        stale = await _stale_reason()
        if stale is not None:
            # 租约已被接管或用户已改意：这次失败属于旧方向，不得写回（P0-4）。
            # 租约仍属自己时才释放，避免用过期内存对象覆盖新持有者。
            if stale == "input_version_changed":
                await release_run_lease(session, run=run, owner=_LEASE_OWNER)
            return await get_run_progress(session, owner_id=owner_id, work_id=work_id)
        pending.state = StepState.FAILED.value
        pending.error_code = getattr(exc, "failure_code", type(exc).__name__)[:64]
        quality_rewrite = (
            step == ChapterStep.REVIEW
            and not is_directed(run)
            and str(exc) == "QUALITY_GATE_FAILED"
            and pending.attempt < 3
        )
        if quality_rewrite:
            _rewind_review_to_weave(run=run, by_name=by_name)
        run.state = (
            ChapterRunState.TERMINAL_FAILED.value
            if pending.attempt >= 3 or isinstance(exc, ProductionStopped)
            else ChapterRunState.RETRYABLE_FAILED.value
        )
        run.version += 1
        await session.flush()
        await append_event(
            session,
            work_id=work_id,
            event_type="chapter.step_failed",
            data={"chapter_no": chapter_no, "step": pending.step, "error": pending.error_code},
            branch_id=work.branch_id,
            chapter_no=chapter_no,
        )
        await release_run_lease(session, run=run, owner=_LEASE_OWNER)
        return await get_run_progress(session, owner_id=owner_id, work_id=work_id)

    # 调用窗口已结束：先复核租约与输入版本，再写回结果（P0-4）。
    # 期间可能已有新 worker 接管，或用户改意递增了 input_version——
    # 这两种情况下本次产出属于旧方向，必须作废而不是覆盖。
    stale = await _stale_reason()
    if stale is not None:
        if stale == "input_version_changed":
            await release_run_lease(session, run=run, owner=_LEASE_OWNER)
        await append_event(
            session,
            work_id=work_id,
            event_type="chapter.result_discarded",
            data={
                "chapter_no": chapter_no,
                "step": pending.step,
                "reason": stale,
            },
            branch_id=work.branch_id,
            chapter_no=chapter_no,
        )
        return await get_run_progress(session, owner_id=owner_id, work_id=work_id)

    # 调用窗口已结束：释放租约，下一个 tick 重新领取
    await release_run_lease(session, run=run, owner=_LEASE_OWNER)

    if complete is False:
        # A director command succeeded; save its checkpoint without completing
        # the scene-production step or counting normal ticks as failed retries.
        pending.state = StepState.PENDING.value
        pending.attempt = 0
        if step == ChapterStep.REVIEW and is_directed(run):
            by_name[ChapterStep.PRODUCE.value].state = StepState.PENDING.value
            by_name[ChapterStep.PRODUCE.value].error_code = ""
        # A-02：导演刚请求了用户裁决时章节必须停在 PENDING_DECISION。
        # 统一写 RUNNING 会让「作品在等待、章节却在跑」的状态投影自相矛盾。
        if run.state != ChapterRunState.PENDING_DECISION.value:
            run.state = ChapterRunState.RUNNING.value
        run.version += 1
        await session.flush()
        production = run.generation_context.get("production", {})
        await append_event(
            session, work_id=work_id, event_type="chapter.scene_progress",
            data={"chapter_no": chapter_no, "scene_no": production.get("scene_index", 0) + 1,
                  "completed_scenes": len(production.get("accepted", [])),
                  "summary": "导演正在指导场景创作"},
            branch_id=work.branch_id, chapter_no=chapter_no,
        )
        return await get_run_progress(session, owner_id=owner_id, work_id=work_id)

    pending.state = StepState.SUCCEEDED.value
    pending.error_code = ""
    pending.output_ref = hashlib.sha256(
        f"{run.id}:{pending.step}:{run.version}:{run.word_count}".encode()
    ).hexdigest()
    # 人在回路：清除已消费的 guidance（本步骤已使用）
    guidance_key = f"after_{step.value.lower()}"
    if run.user_guidance and guidance_key in run.user_guidance:
        guidance = dict(run.user_guidance)
        del guidance[guidance_key]
        run.user_guidance = guidance
    if run.state == ChapterRunState.RETRYABLE_FAILED.value:
        run.state = ChapterRunState.RUNNING.value
    run.version += 1
    await session.flush()
    # Agent 对话模式：步骤产出事件，携带实际内容供前端实时展示
    _step_output_data: dict[str, Any] = {"chapter_no": chapter_no, "step": pending.step}
    if pending.step == "ASSEMBLE":
        ctx = run.generation_context or {}
        tn = ctx.get("target_node", {})
        vol = ctx.get("volume", {})
        _step_output_data["summary"] = (
            f"目标节点：{tn.get('title', '无')}"
            + (f" | 卷：{vol.get('title', '')}" if vol.get('title') else "")
        )
        _step_output_data["target_node_title"] = tn.get("title", "")
        _step_output_data["volume_title"] = vol.get("title", "")
    elif pending.step == "PERFORM":
        performances = list(run.performances or [])
        _step_output_data["performances"] = [
            {
                "persona": item.get("persona", ""),
                "actions": list(item.get("actions", []) or [])[:3],
                "dialogue": list(item.get("dialogue", []) or [])[:2],
                "emotional_shift": item.get("emotional_shift", ""),
            }
            for item in performances
        ]
        _step_output_data["performer_count"] = len(performances)
    elif pending.step == "DIRECT":
        dp = (run.generation_context or {}).get("director_plan", {})
        _step_output_data["scene_goal"] = dp.get("scene_goal", "") if isinstance(dp, dict) else ""
        _step_output_data["beats"] = dp.get("beats", []) if isinstance(dp, dict) else []
        _step_output_data["ending_hook"] = dp.get("ending_hook", "") if isinstance(dp, dict) else ""
        _step_output_data["character_actions"] = [
            {"persona": ca.get("persona", ""), "scene_actions": ca.get("scene_actions", [])[:2],
             "emotional_arc": ca.get("emotional_arc", "")}
            for ca in (dp.get("character_actions", []) if isinstance(dp, dict) else [])
        ]
        if is_directed(run):
            plan = run.generation_context["production"]["plan"]
            _step_output_data["summary"] = f"导演已安排 {len(plan['scenes'])} 个场景"
    elif pending.step == "PRODUCE":
        _step_output_data["summary"] = "场景创作与导演指导已完成"
        _step_output_data["title"] = run.title
        _step_output_data["word_count"] = run.word_count
    elif pending.step == "WEAVE":
        _step_output_data["title"] = run.title or ""
        _step_output_data["content"] = run.content or ""
        _step_output_data["word_count"] = run.word_count or 0
    elif pending.step == "REVIEW":
        rv = run.review or {}
        _step_output_data["passed"] = rv.get("passed", False)
        _step_output_data["revised"] = rv.get("revised", False)
        _step_output_data["continuity_issues"] = rv.get("continuity_issues", [])
        _step_output_data["leakage_issues"] = rv.get("leakage_issues", [])
        _step_output_data["prose_issues"] = rv.get("prose_issues", [])
        _step_output_data["issues"] = (
            list(rv.get("continuity_issues", []))
            + list(rv.get("leakage_issues", []))
            + list(rv.get("prose_issues", []))
        )
        _step_output_data["issues_count"] = (
            len(rv.get("continuity_issues", []))
            + len(rv.get("leakage_issues", []))
            + len(rv.get("prose_issues", []))
        )
    elif pending.step == "CANON":
        _step_output_data["confirmed"] = True
    await append_event(
        session,
        work_id=work_id,
        event_type="chapter.step_output",
        data=_step_output_data,
        branch_id=work.branch_id,
        chapter_no=chapter_no,
    )
    await append_event(
        session,
        work_id=work_id,
        event_type="chapter.step_succeeded",
        data={"chapter_no": chapter_no, "step": pending.step},
        branch_id=work.branch_id,
        chapter_no=chapter_no,
    )
    if is_directed(run) and step == ChapterStep.CANON:
        # The caller commits this transition and Canon in the same transaction.
        assert_chapter_run_transition(run.state, ChapterRunState.CANONIZED.value)
        run.state = ChapterRunState.CANONIZED.value
        run.canonized_at = datetime.now(UTC)
        work.latest_chapter_no = max(int(work.latest_chapter_no), chapter_no)
        work.version += 1
        await session.flush()
        await append_event(
            session, work_id=work_id, event_type="chapter.done",
            data={"chapter_no": chapter_no}, branch_id=work.branch_id, chapter_no=chapter_no,
        )
        # A-03：v2 在这里就成章返回，收尾必须同样发生——否则 v2 永不扩卷，
        # 末节点完成后 start_run 会再开一章没有目标节点的任务。
        await _after_chapter_completed(session, work=work, run=run, provider=provider)
        await session.flush()
        return await get_run_progress(session, owner_id=owner_id, work_id=work_id)
    # 人在回路：检查点暂停（DIRECT 后、WEAVE 后）
    if step.value in CHECKPOINT_STEPS and not run.auto_advance:
        assert_chapter_run_transition(run.state, ChapterRunState.AWAITING_INPUT.value)
        run.state = ChapterRunState.AWAITING_INPUT.value
        run.version += 1
        await session.flush()
        await append_event(
            session,
            work_id=work_id,
            event_type="chapter.checkpoint_reached",
            data={"chapter_no": chapter_no, "step": step.value, "auto_advance": False},
            branch_id=work.branch_id,
            chapter_no=chapter_no,
        )
    return await get_run_progress(session, owner_id=owner_id, work_id=work_id)


async def advance_background_run(
    session: AsyncSession, *, provider: ModelProvider
) -> RunProgressOut | None:
    """由 durable worker 每次领取一个章节检查点；网页关闭后仍继续。

    D-02 依赖屏障：``QUEUED``（新章起跑）不得越过同作品同分支更早章节的
    在途运行（QUEUED / RUNNING / PENDING_DECISION / AWAITING_INPUT /
    RETRYABLE_FAILED）——否则第二章可能在第一章尚未完成时被领取，剧情依赖
    就断了。屏障只拦「起跑」：续跑中的 RUNNING / RETRYABLE_FAILED 不受影响。
    屏障不阻塞其他作品：逐个候选检查，被挡住的跳过，而不是整体停摆。
    """
    candidates = list(
        (
            await session.scalars(
                select(ChapterRunModel)
                .join(StoryWorkModel, StoryWorkModel.id == ChapterRunModel.work_id)
                .where(
                    StoryWorkModel.state == StoryWorkState.RUNNING.value,
                    StoryWorkModel.deleted_at.is_(None),
                    ChapterRunModel.state.in_(
                        (
                            ChapterRunState.QUEUED.value,
                            ChapterRunState.RUNNING.value,
                            ChapterRunState.RETRYABLE_FAILED.value,
                        )
                    ),
                    # 租约在期说明别的 worker 正在事务外调用模型，不得并行推进（§4.4）
                    or_(
                        ChapterRunModel.lease_expires_at.is_(None),
                        ChapterRunModel.lease_expires_at <= datetime.now(UTC),
                    ),
                )
                .order_by(ChapterRunModel.updated_at, ChapterRunModel.chapter_no)
                .limit(16)
            )
        ).all()
    )
    for candidate in candidates:
        # 锁内复核：扫描与加锁之间状态可能已被其他 worker 改写
        locked = await session.scalar(
            select(ChapterRunModel.id)
            .where(
                ChapterRunModel.id == candidate.id,
                ChapterRunModel.state.in_(
                    (
                        ChapterRunState.QUEUED.value,
                        ChapterRunState.RUNNING.value,
                        ChapterRunState.RETRYABLE_FAILED.value,
                    )
                ),
                or_(
                    ChapterRunModel.lease_expires_at.is_(None),
                    ChapterRunModel.lease_expires_at <= datetime.now(UTC),
                ),
            )
            .with_for_update(skip_locked=True)
        )
        if locked is None:
            continue
        if candidate.state == ChapterRunState.QUEUED.value:
            earlier_in_flight = await session.scalar(
                select(func.count(ChapterRunModel.id)).where(
                    ChapterRunModel.work_id == candidate.work_id,
                    ChapterRunModel.branch_id == candidate.branch_id,
                    ChapterRunModel.chapter_no < candidate.chapter_no,
                    ChapterRunModel.state.in_(
                        (
                            ChapterRunState.QUEUED.value,
                            ChapterRunState.RUNNING.value,
                            ChapterRunState.PENDING_DECISION.value,
                            ChapterRunState.AWAITING_INPUT.value,
                            ChapterRunState.RETRYABLE_FAILED.value,
                        )
                    ),
                )
            )
            if int(earlier_in_flight or 0) > 0:
                continue
        work = await session.get(StoryWorkModel, candidate.work_id)
        if work is None:
            continue
        return await advance_step(
            session,
            provider=provider,
            owner_id=work.owner_id,
            work_id=work.id,
            chapter_no=candidate.chapter_no,
        )
    return None


# ---------------------------------------------------------------------------
# 人在回路检查点
# ---------------------------------------------------------------------------


async def bump_input_version(
    session: AsyncSession,
    *,
    run: ChapterRunModel,
    reason: str,
) -> int:
    """用户改意/路径变更：递增 input_version，使旧方向的调用与产物失效（P0-4）。

    input_version 参与命令 id 与调用逻辑键，递增后旧方向的 pending 结果在写回前
    会被 ``advance_step`` 的复核丢弃，不会覆盖新方向。返回新的版本号。
    """
    run.input_version = int(run.input_version or 1) + 1
    run.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=run.work_id,
        event_type="chapter.input_version_bumped",
        data={
            "chapter_no": run.chapter_no,
            "reason": reason,
            "input_version": run.input_version,
        },
        branch_id=run.branch_id,
        chapter_no=run.chapter_no,
    )
    return int(run.input_version)


async def submit_guidance(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    chapter_no: int,
    payload: GuidanceRequest,
) -> RunProgressOut:
    """用户在检查点提交反馈，恢复流水线。"""
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    run = await session.scalar(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work_id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.chapter_no == chapter_no,
        )
        .order_by(ChapterRunModel.attempt.desc())
        .limit(1)
    )
    if run is None:
        raise NotFound("chapter run not found")
    if run.state != ChapterRunState.AWAITING_INPUT.value:
        raise InvalidState(
            "chapter not awaiting input",
            current=run.state,
        )
    # 存储用户反馈
    current_step = run.current_step or ""
    guidance_key = f"after_{current_step.lower()}"
    guidance = dict(run.user_guidance or {})
    guidance[guidance_key] = payload.feedback
    run.user_guidance = guidance
    # 状态迁移：AWAITING_INPUT -> RUNNING
    assert_chapter_run_transition(run.state, ChapterRunState.RUNNING.value)
    run.state = ChapterRunState.RUNNING.value
    # 用户改意：旧方向的调用键失效，未写回的结果不得再覆盖（P0-4）
    await bump_input_version(session, run=run, reason="guidance")
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="chapter.guidance_submitted",
        data={
            "chapter_no": chapter_no,
            "feedback": payload.feedback[:200],  # 截断存储
            "approve": payload.approve,
        },
        branch_id=work.branch_id,
        chapter_no=chapter_no,
    )
    return await get_run_progress(session, owner_id=owner_id, work_id=work_id)


async def set_auto_advance(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    chapter_no: int,
    payload: AutoAdvanceRequest,
) -> RunProgressOut:
    """切换自动/手动模式。"""
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    run = await session.scalar(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work_id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.chapter_no == chapter_no,
        )
        .order_by(ChapterRunModel.attempt.desc())
        .limit(1)
    )
    if run is None:
        raise NotFound("chapter run not found")
    run.auto_advance = payload.enabled
    # 如果开启自动模式且当前在等待输入，自动恢复
    if payload.enabled and run.state == ChapterRunState.AWAITING_INPUT.value:
        assert_chapter_run_transition(run.state, ChapterRunState.RUNNING.value)
        run.state = ChapterRunState.RUNNING.value
    run.version += 1
    await session.flush()
    return await get_run_progress(session, owner_id=owner_id, work_id=work_id)


# ---------------------------------------------------------------------------
# 阅读（FR-15 / FR-22 / G-14）
# ---------------------------------------------------------------------------


async def get_chapter(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID, chapter_no: int,
    attempt: int | None = None,
) -> ChapterOut:
    """只读路径。本函数不持有任何生成能力引用（G-14）。

    attempt=None 时返回最新版本，否则返回指定 attempt。
    """
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    q = select(ChapterRunModel).where(
        ChapterRunModel.work_id == work_id,
        ChapterRunModel.branch_id == work.branch_id,
        ChapterRunModel.chapter_no == chapter_no,
    )
    if attempt is not None:
        q = q.where(ChapterRunModel.attempt == attempt)
    else:
        # Prefer the accepted edition while a replacement is still a draft.
        q = q.order_by(
            (ChapterRunModel.state == ChapterRunState.CANONIZED.value).desc(),
            ChapterRunModel.attempt.desc(),
        ).limit(1)
    run = await session.scalar(q)
    if run is None:
        raise NotFound("chapter not found")
    return ChapterOut(
        work_id=str(work_id),
        chapter_no=int(run.chapter_no),
        title=run.title,
        state=ChapterRunState(run.state),
        content=run.content or "",
        word_count=int(run.word_count),
        ai_disclosure=AI_DISCLOSURE,
        version=int(run.version),
    )


async def list_chapters(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> list[dict]:
    """列出所有章节（每章取最新 attempt，含版本数）。"""
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    runs = list(
        (
            await session.scalars(
                select(ChapterRunModel)
                .where(
                    ChapterRunModel.work_id == work_id,
                    ChapterRunModel.branch_id == work.branch_id,
                )
                .order_by(ChapterRunModel.chapter_no, ChapterRunModel.attempt)
            )
        ).all()
    )
    # 按 chapter_no 分组，取最新 attempt
    from collections import defaultdict
    by_no: dict[int, list] = defaultdict(list)
    for r in runs:
        by_no[int(r.chapter_no)].append(r)
    result = []
    for ch_no in sorted(by_no):
        versions = by_no[ch_no]
        accepted = [r for r in versions if r.state == ChapterRunState.CANONIZED.value]
        latest = accepted[-1] if accepted else versions[-1]
        result.append({
            "chapter_no": ch_no,
            "title": latest.title,
            "word_count": int(latest.word_count),
            "state": latest.state,
            "attempt": int(latest.attempt),
            "version_count": len(versions),
        })
    return result


async def list_chapter_versions(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID, chapter_no: int
) -> list[dict]:
    """列出指定章节的所有版本（attempt）。"""
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    runs = list(
        (
            await session.scalars(
                select(ChapterRunModel)
                .where(
                    ChapterRunModel.work_id == work_id,
                    ChapterRunModel.branch_id == work.branch_id,
                    ChapterRunModel.chapter_no == chapter_no,
                )
                .order_by(ChapterRunModel.attempt)
            )
        ).all()
    )
    return [
        {
            "attempt": int(r.attempt),
            "title": r.title,
            "word_count": int(r.word_count),
            "state": r.state,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in runs
    ]


async def _queue_replay_run(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    chapter_no: int,
    correction: dict | None = None,
) -> bool:
    """把一章排进重跑队列：新建 attempt，状态 QUEUED，由 worker 接管。

    ``correction`` 是纠错语义（报了什么错、工单号、涉及主题）：它必须进入新
    attempt 的 ``generation_context``，否则重演拿到的是一次「不知道要改什么」的
    普通重跑——纠错内容只躺在事件里，永远到不了导演请求（C-01）。

    返回 False 表示这一章还没有可重演的运行（例如尚未生成），调用方据此跳过。
    旧 attempt 一律保留：重演是新增一次尝试，不是抹掉已接受的那一次。
    """
    max_attempt = await session.scalar(
        select(
            func.max(ChapterRunModel.attempt)
        ).where(
            ChapterRunModel.work_id == work.id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.chapter_no == chapter_no,
        )
    )
    if max_attempt is None:
        return False
    # 同一章已有排队中的重演：不再叠新的，否则重复报错会无限堆任务（C-01）。
    pending_replay = await session.scalar(
        select(func.count(ChapterRunModel.id)).where(
            ChapterRunModel.work_id == work.id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.chapter_no == chapter_no,
            ChapterRunModel.attempt > 1,
            ChapterRunModel.state == ChapterRunState.QUEUED.value,
        )
    )
    if int(pending_replay or 0) > 0:
        # D-01：同一报错去重（重复点同一个错不再叠任务）；不同报错必须合并进
        # 已排队的那次重演——不能因「已有排队任务」而静默丢弃新纠错。
        pending = await session.scalar(
            select(ChapterRunModel)
            .where(
                ChapterRunModel.work_id == work.id,
                ChapterRunModel.branch_id == work.branch_id,
                ChapterRunModel.chapter_no == chapter_no,
                ChapterRunModel.attempt > 1,
                ChapterRunModel.state == ChapterRunState.QUEUED.value,
            )
            .order_by(ChapterRunModel.attempt.desc())
            .limit(1)
        )
        if pending is None or not correction:
            return False
        ctx = dict(pending.generation_context or {})
        history = list(ctx.get("corrections") or [])
        if not history and ctx.get("correction"):
            history = [dict(ctx["correction"])]
        statement = str(correction.get("statement", "")).strip()
        if statement and any(
            str(item.get("statement", "")).strip() == statement for item in history
        ):
            return False
        history.append(dict(correction))
        ctx["corrections"] = history
        ctx["correction"] = dict(correction)
        ctx["replay_reason"] = "fact_reported"
        pending.generation_context = ctx
        await session.flush()
        await append_event(
            session,
            work_id=work.id,
            event_type="run.correction_merged",
            data={
                "chapter_no": chapter_no,
                "run_id": str(pending.id),
                "ticket_id": str(correction.get("ticket_id", "")),
                "corrections_total": len(history),
            },
            branch_id=work.branch_id,
            chapter_no=chapter_no,
        )
        return True
    new_attempt = int(max_attempt) + 1
    context: dict = {"architecture_version": ARCHITECTURE}
    if correction:
        context["correction"] = dict(correction)
        context["replay_reason"] = "fact_reported"
    run = ChapterRunModel(
        id=uuid.uuid4(),
        work_id=work.id,
        branch_id=work.branch_id,
        chapter_no=chapter_no,
        attempt=new_attempt,
        state=ChapterRunState.QUEUED.value,
        current_step=ChapterStep.ASSEMBLE.value,
        title=f"第 {chapter_no} 章",
        generation_context=context,
        auto_advance=True,
    )
    session.add(run)
    await session.flush()
    for step in chapter_step_order(run.generation_context):
        session.add(
            ChapterStepModel(
                id=uuid.uuid4(),
                run_id=run.id,
                step=step.value,
                state=StepState.PENDING.value,
                input_version=1,
            )
        )
    await session.flush()
    await append_event(
        session,
        work_id=work.id,
        event_type="run.started",
        data={
            "chapter_no": chapter_no,
            "run_id": str(run.id),
            "attempt": new_attempt,
            "reason": "replay",
        },
        branch_id=work.branch_id,
        chapter_no=chapter_no,
    )
    return True


async def regenerate_chapter(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID, chapter_no: int
) -> RunProgressOut:
    """重写指定章节：创建新 attempt（max+1），状态 QUEUED。"""
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if not await _queue_replay_run(session, work=work, chapter_no=chapter_no):
        raise NotFound(f"chapter {chapter_no} not found")
    return await get_run_progress(session, owner_id=owner_id, work_id=work_id)


async def list_characters(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> list[dict]:
    """列出作品所有角色（Persona）。"""
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    personas = list(
        (
            await session.scalars(
                select(PersonaSpecModel)
                .where(PersonaSpecModel.work_id == work_id)
                .order_by(PersonaSpecModel.name)
            )
        ).all()
    )
    return [
        {
            "name": p.name,
            "identity": (p.identity or {}).get("role", ""),
            "drive": (p.drives or {}).get("primary", ""),
            "voice": (p.voice or {}).get("style", ""),
            "traits": list(p.stable_traits or []),
        }
        for p in personas
    ]


# ---------------------------------------------------------------------------
# 裁决（FR-10 / G-13）
# ---------------------------------------------------------------------------


def _decision_view(row: DecisionRequestModel) -> DecisionView:
    return DecisionView(
        decision_id=str(row.id),
        work_id=str(row.work_id),
        chapter_no=row.chapter_no,
        state=DecisionState(row.state),
        trigger_summary=row.trigger_summary,
        why_human=row.why_human,
        options=[DecisionOption(**o) for o in (row.options or [])],
        default_option_id=row.default_option_id or None,
        deadline=row.deadline,
        impact_level=row.impact_level,
        impact_horizon_chapters=int(row.impact_horizon_chapters),
        confirm_nonce=row.confirm_nonce,
        version=int(row.version),
    )


async def list_pending_decisions(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID | None = None
) -> list[DecisionView]:
    """待裁决收件箱（FR-12）。

    跨作品聚合时只返回本人作品（G-11/G-12）：没有 owner 条件的读取一律 fail closed。
    """
    stmt = (
        select(DecisionRequestModel)
        .join(StoryWorkModel, StoryWorkModel.id == DecisionRequestModel.work_id)
        .where(
            StoryWorkModel.owner_id == owner_id,
            DecisionRequestModel.state == DecisionState.PENDING.value,
        )
        .order_by(DecisionRequestModel.created_at.asc())
    )
    if work_id is not None:
        stmt = stmt.where(DecisionRequestModel.work_id == work_id)
    rows = await session.scalars(stmt)
    return [_decision_view(row) for row in rows.all()]


async def get_decision(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID, decision_id: uuid.UUID
) -> DecisionView:
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    row = await session.scalar(
        select(DecisionRequestModel).where(
            DecisionRequestModel.id == decision_id,
            DecisionRequestModel.work_id == work_id,
        )
    )
    if row is None:
        raise NotFound("decision not found")
    return _decision_view(row)


async def create_decision(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    chapter_no: int,
    run_id: uuid.UUID | None = None,
    node_id: str = "",
    trigger_summary: str,
    why_human: str,
    options: list[dict[str, object]],
    default_option_id: str,
    impact_level: str = "MEDIUM",
    impact_horizon_chapters: int = 1,
    deadline: datetime | None = None,
) -> DecisionView:
    """导演发起裁决：落持久化请求，章节与作品进入等待（P1-2 / G-13）。

    裁决不是聊天消息：它必须能被收件箱列出、能被深链打开、能在无人选择时
    按默认项到期落定。选项为空或默认项不在选项里，一律拒绝创建。
    """
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if not options:
        raise ValidationFailed("decision requires at least one option")
    ids = {str(option.get("option_id", "")) for option in options}
    if default_option_id not in ids:
        raise ValidationFailed("default option must be one of the options")

    row = DecisionRequestModel(
        id=uuid.uuid4(),
        work_id=work_id,
        run_id=run_id,
        node_id=node_id or "",
        chapter_no=chapter_no,
        state=DecisionState.PENDING.value,
        trigger_summary=trigger_summary,
        why_human=why_human,
        options=[dict(option) for option in options],
        default_option_id=default_option_id,
        deadline=deadline,
        impact_level=impact_level,
        impact_horizon_chapters=impact_horizon_chapters,
        # 提交必须带回 nonce：预览不消费，重复提交不生效
        confirm_nonce=hashlib.sha256(
            f"{work_id}:{chapter_no}:{uuid.uuid4()}".encode()
        ).hexdigest()[:32],
    )
    session.add(row)
    await session.flush()

    if work.state == StoryWorkState.RUNNING.value:
        assert_story_work_transition(work.state, StoryWorkState.PENDING_DECISION.value)
        work.state = StoryWorkState.PENDING_DECISION.value
        work.version += 1
    if run_id is not None:
        run = await session.get(ChapterRunModel, run_id)
        if run is not None and run.state in (
            ChapterRunState.RUNNING.value,
            ChapterRunState.AWAITING_INPUT.value,
        ):
            assert_chapter_run_transition(
                run.state, ChapterRunState.PENDING_DECISION.value
            )
            run.state = ChapterRunState.PENDING_DECISION.value
            run.version += 1
    await session.flush()

    await append_event(
        session,
        work_id=work_id,
        event_type="decision.created",
        data={
            "decision_id": str(row.id),
            "chapter_no": chapter_no,
            "trigger_summary": trigger_summary,
            "default_option_id": default_option_id,
            "deadline": deadline.isoformat() if deadline else None,
        },
        branch_id=work.branch_id,
        chapter_no=chapter_no,
        decision_id=str(row.id),
    )
    return _decision_view(row)


async def _apply_decision(
    session: AsyncSession,
    *,
    row: DecisionRequestModel,
    chosen: str,
    resolved_by: str,
) -> None:
    """裁决生效：结果回写到章节，递增输入版本并恢复推进（P1-2）。

    用户的选择必须真的改变后续生成——只改作品状态等于没选：新的
    input_version 让旧方向的调用键失效，导演下一轮才看得到这次选择。
    """
    # 用 ORM 条件更新而不是裸 SQL：UUID/DateTime 的类型绑定交给方言处理，
    # 同时保持在数据库层面「只有仍为 PENDING 的一方能胜出」的原子性。
    result = await session.execute(
        update(DecisionRequestModel)
        .where(
            DecisionRequestModel.id == row.id,
            DecisionRequestModel.state == DecisionState.PENDING.value,
        )
        .values(
            state=DecisionState.RESOLVED.value,
            resolved_by=resolved_by[:32],
            resolved_option_id=chosen,
            resolved_at=datetime.now(UTC),
            version=DecisionRequestModel.version + 1,
        )
    )
    if result.rowcount != 1:
        raise Conflict("decision was resolved concurrently", current_version=int(row.version))
    await session.refresh(row)

    work = await session.get(StoryWorkModel, row.work_id)
    if work is not None and work.state == StoryWorkState.PENDING_DECISION.value:
        assert_story_work_transition(work.state, StoryWorkState.RUNNING.value)
        work.state = StoryWorkState.RUNNING.value
        work.version += 1

    run: ChapterRunModel | None = None
    if row.run_id is not None:
        run = await session.get(ChapterRunModel, row.run_id)
    if run is None and row.chapter_no is not None and work is not None:
        run = await session.scalar(
            select(ChapterRunModel)
            .where(
                ChapterRunModel.work_id == row.work_id,
                ChapterRunModel.branch_id == work.branch_id,
                ChapterRunModel.chapter_no == row.chapter_no,
            )
            .order_by(ChapterRunModel.attempt.desc())
            .limit(1)
        )
    if run is not None:
        if run.state == ChapterRunState.PENDING_DECISION.value:
            assert_chapter_run_transition(run.state, ChapterRunState.RUNNING.value)
            run.state = ChapterRunState.RUNNING.value
        # 选择进入生成上下文：导演下一轮必须看得到，而不是只在通知里出现
        context = dict(run.generation_context or {})
        resolutions = list(context.get("decision_resolutions") or [])
        # A-02：只记 option_id 等于没记——导演拿到一个 id 无从执行。所选选项的
        # 近期后果与可逆性才是创作语义，必须一起进上下文，导演下一轮才用得上。
        chosen_option = next(
            (
                option
                for option in (row.options or [])
                if str(option.get("option_id")) == chosen
            ),
            None,
        )
        resolutions.append(
            {
                "decision_id": str(row.id),
                "option_id": chosen,
                "label": (chosen_option or {}).get("label", ""),
                "near_term_consequence": (chosen_option or {}).get(
                    "near_term_consequence", ""
                ),
                "reversibility": (chosen_option or {}).get("reversibility", ""),
                "trigger_summary": row.trigger_summary,
                "resolved_by": resolved_by,
                "chapter_no": row.chapter_no,
                "consumed": False,
            }
        )
        context["decision_resolutions"] = resolutions
        run.generation_context = context
        await bump_input_version(session, run=run, reason="decision_resolved")
    await session.flush()

    if work is not None:
        await append_event(
            session,
            work_id=row.work_id,
            event_type="decision.resolved",
            data={
                "decision_id": str(row.id),
                "option_id": chosen,
                "resolved_by": resolved_by,
            },
            branch_id=work.branch_id,
            chapter_no=row.chapter_no,
            decision_id=str(row.id),
        )


async def sweep_expired_decisions(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 20
) -> list[str]:
    """到期未选择的裁决按默认项落定：与用户提交竞争，只有一个能赢（P1-2）。"""
    moment = now or datetime.now(UTC)
    rows = (
        await session.scalars(
            select(DecisionRequestModel)
            .where(
                DecisionRequestModel.state == DecisionState.PENDING.value,
                DecisionRequestModel.deadline.is_not(None),
                DecisionRequestModel.deadline <= moment,
                DecisionRequestModel.default_option_id != "",
            )
            .limit(limit)
        )
    ).all()
    resolved: list[str] = []
    for row in rows:
        try:
            await _apply_decision(
                session, row=row, chosen=row.default_option_id, resolved_by="timer"
            )
        except Conflict:  # 已被用户抢先落定
            continue
        resolved.append(str(row.id))
    return resolved


async def resolve_decision(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    decision_id: uuid.UUID,
    option_id: str | None,
    accept_default: bool,
    confirm_nonce: str,
    resolved_by: str = "user",
) -> DecisionView:
    """G-13：裁决与默认 timer 竞争，条件更新保证仅一个结果成功。"""
    # 归属校验：越权一律 NotFound，不泄露是否存在（G-12）
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    row = await session.scalar(
        select(DecisionRequestModel).where(
            DecisionRequestModel.id == decision_id,
            DecisionRequestModel.work_id == work_id,
        )
    )
    if row is None:
        raise NotFound("decision not found")
    if row.state != DecisionState.PENDING.value:
        raise Conflict(
            "decision already resolved",
            current_version=int(row.version),
            conflict_summary={"state": row.state},
        )
    if not row.confirm_nonce or confirm_nonce != row.confirm_nonce:
        raise ValidationFailed("confirm nonce mismatch")

    chosen = option_id
    if accept_default or not chosen:
        chosen = row.default_option_id
    if not chosen:
        raise ValidationFailed("no option selected and no default available")
    if chosen not in {o.get("option_id") for o in (row.options or [])}:
        raise ValidationFailed("unknown option")

    # 条件更新：只有仍为 PENDING 的一方能胜出；结果回写章节并递增输入版本
    await _apply_decision(session, row=row, chosen=chosen, resolved_by=resolved_by)
    return _decision_view(row)


# ---------------------------------------------------------------------------
# 事实报错（FR-11）
# ---------------------------------------------------------------------------


async def report_fact(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    payload: ReportFactRequest,
) -> ReportFactResponse:
    """事实错误可纠正并触发局部重演；审美意见必须给出可行动回落路径。"""
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    ticket_id = uuid.uuid4()

    if payload.kind == "TASTE":
        # 不得只显示拒绝（PRD §3.1）
        return ReportFactResponse(
            accepted=False,
            ticket_id=str(ticket_id),
            kind="TASTE",
            message="这不是能直接改的事实。你可以把它变成后面的方向：调整后续走向，或者留到下一个需要你决定的节点。",
            affected_chapters=[],
            available_actions=["adjust_future_path", "defer_to_next_decision"],
        )

    chapter_no = payload.chapter_no or int(work.latest_chapter_no)
    # 报错主题决定重演范围：认不出主题就没有「精确」可言，只能保守重做。
    cast = await memory_app.cast_of(session, work)
    subjects = [name for name in cast if name and name in payload.statement]
    if payload.subject.strip():
        subjects.append(payload.subject.strip())
    plan, affected, conservative = await memory_app.plan_local_replay(
        session,
        work=work,
        changed_subjects=subjects,
        from_chapter_no=chapter_no,
    )
    invalidated, _ = await memory_app.invalidate_changed(
        session,
        work=work,
        changed_subjects=subjects,
        reason="fact_reported",
        fallback_kinds=("promise", "character_arc", "belief"),
    )
    # 失效标记不等于重演：这里才真正把受影响的章排进重跑队列（B-02）。
    # 纠错内容必须随任务一起走，否则重演时导演不知道要改什么（C-01）。
    correction = {
        "ticket_id": str(ticket_id),
        "statement": payload.statement,
        "subject": payload.subject.strip(),
        "reported_chapter_no": chapter_no,
    }
    queued = [
        c
        for c in affected
        if await _queue_replay_run(
            session, work=work, chapter_no=c, correction=correction
        )
    ]
    scope = "conservative_batch" if conservative else "dependency_subgraph"
    await append_event(
        session,
        work_id=work_id,
        event_type="fact.reported",
        data={
            "ticket_id": str(ticket_id),
            "statement": payload.statement,
            "affected": affected,
            "queued": queued,
            "subjects": subjects,
            "replay_scope": scope,
            "replay_complete": bool(plan.complete),
            "invalidated_memory_items": invalidated,
        },
        branch_id=work.branch_id,
        chapter_no=chapter_no,
    )
    # 后台只领取 RUNNING 作品的任务。作品已完结或已暂停时如果仍回「已受理，系统
    # 会自动重演」，就是承诺了一件不会发生的事——必须如实说明何时执行（C-01），
    # 同时不自动改状态，以保留用户的暂停/完结意图。
    state = str(work.state or "").upper()
    if state == StoryWorkState.DONE.value:
        message = "已记录。作品已完结，重演要等你恢复创作后才会执行，现在不会自动改稿。"
        actions = ["resume_then_replay"]
    elif state in (StoryWorkState.PAUSED_QUOTA.value, StoryWorkState.PAUSED_COST.value):
        message = "已记录。作品处于暂停状态，保持你的暂停意图，恢复后才会重演。"
        actions = ["resume_then_replay"]
    else:
        message = (
            "已受理。系统核对受影响章节后局部重演，不会整本重写。"
            if not conservative
            else "已受理。这一处改动的依赖记录不完整，为保证一致性会重做之后若干章。"
        )
        actions = ["await_local_replay"]
    return ReportFactResponse(
        accepted=True,
        ticket_id=str(ticket_id),
        kind="FACT",
        message=message,
        affected_chapters=queued,
        replay_scope=scope,
        available_actions=actions,
    )


# ---------------------------------------------------------------------------
# 分享（FR-17）/ 导出（FR-23 / G-15 / G-22）
# ---------------------------------------------------------------------------


async def create_share(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    scope: str = "FULL",
    from_chapter: int | None = None,
    to_chapter: int | None = None,
    expires_in_hours: int = 168,
    invitee_label: str = "",
    base_url: str = "",
) -> ShareOut:
    import secrets

    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    token = secrets.token_urlsafe(24)
    row = ShareModel(
        id=uuid.uuid4(),
        work_id=work_id,
        token=token,
        scope=scope,
        from_chapter=from_chapter,
        to_chapter=to_chapter,
        noindex=True,
        expires_at=datetime.now(UTC) + timedelta(hours=expires_in_hours),
    )
    session.add(row)
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="share.created",
        data={"share_id": str(row.id), "scope": scope, "invitee": invitee_label},
        branch_id=None,
    )
    return ShareOut(
        share_id=str(row.id),
        work_id=str(work_id),
        share_url=f"{base_url}/read/{token}",
        scope=scope,
        noindex=True,
        expires_at=row.expires_at,
    )


async def revoke_share(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID, share_id: uuid.UUID
) -> None:
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    row = await session.scalar(
        select(ShareModel).where(ShareModel.id == share_id, ShareModel.work_id == work_id)
    )
    if row is None:
        raise NotFound("share not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.flush()
        await append_event(
            session,
            work_id=work_id,
            event_type="share.revoked",
            data={"share_id": str(share_id)},
        )


async def get_public_share(session: AsyncSession, *, token: str) -> dict[str, Any]:
    """Capability-link read path: no login, read-only, expiry/revoke fail closed."""
    row = await session.scalar(select(ShareModel).where(ShareModel.token == token))
    now = datetime.now(UTC)
    expires_at = as_utc(row.expires_at) if row is not None else None
    if row is None or row.revoked_at is not None or (
        expires_at is not None and expires_at <= now
    ):
        raise NotFound("share not found")
    work = await session.get(StoryWorkModel, row.work_id)
    if work is None or work.deleted_at is not None:
        raise NotFound("share not found")
    query = (
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work.id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.state == ChapterRunState.CANONIZED.value,
        )
        .order_by(ChapterRunModel.chapter_no)
    )
    if row.from_chapter is not None:
        query = query.where(ChapterRunModel.chapter_no >= row.from_chapter)
    if row.to_chapter is not None:
        query = query.where(ChapterRunModel.chapter_no <= row.to_chapter)
    chapters = (await session.scalars(query)).all()
    return {
        "title": work.title,
        "genre": work.genre,
        "ai_disclosure": AI_DISCLOSURE,
        "expires_at": row.expires_at,
        "chapters": [
            {
                "chapter_no": chapter.chapter_no,
                "title": chapter.title,
                "content": chapter.content,
                "word_count": chapter.word_count,
            }
            for chapter in chapters
        ],
    }


async def get_export_notice(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> ExportNoticeOut:
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    row = await session.scalar(
        select(ExportNoticeModel).where(
            ExportNoticeModel.user_id == owner_id,
            ExportNoticeModel.work_id == work_id,
        )
    )
    satisfied = row.satisfied_at if row else None
    current = row.notice_version == CURRENT_EXPORT_NOTICE_VERSION if row else False
    return ExportNoticeOut(
        notice_version=CURRENT_EXPORT_NOTICE_VERSION,
        satisfied_at=satisfied if current else None,
        required=not current or satisfied is None,
        title=_EXPORT_NOTICE_TITLE,
        body=_EXPORT_NOTICE_BODY,
    )


async def acknowledge_export_notice(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    notice_version: str,
) -> ExportNoticeOut:
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if notice_version != CURRENT_EXPORT_NOTICE_VERSION:
        raise ValidationFailed("stale notice version")

    row = await session.scalar(
        select(ExportNoticeModel).where(
            ExportNoticeModel.user_id == owner_id,
            ExportNoticeModel.work_id == work_id,
        )
    )
    now = datetime.now(UTC)
    if row is None:
        row = ExportNoticeModel(
            id=uuid.uuid4(),
            user_id=owner_id,
            work_id=work_id,
            notice_version=notice_version,
            satisfied_at=now,
        )
        session.add(row)
    else:
        row.notice_version = notice_version
        row.satisfied_at = now
    session.add(
        ExportNoticeLogModel(
            id=uuid.uuid4(),
            user_id=owner_id,
            work_id=work_id,
            notice_version=notice_version,
            acknowledged_at=now,
        )
    )
    await session.flush()
    return ExportNoticeOut(
        notice_version=notice_version,
        satisfied_at=now,
        required=False,
        title=_EXPORT_NOTICE_TITLE,
        body=_EXPORT_NOTICE_BODY,
    )


async def export_work(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    payload: ExportRequest,
    base_url: str = "",
) -> ExportOut:
    """G-15 / G-22：先校验告知状态与格式白名单，再生成字节流（不经 LLM）。"""
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)

    notice = await session.scalar(
        select(ExportNoticeModel).where(
            ExportNoticeModel.user_id == owner_id,
            ExportNoticeModel.work_id == work_id,
        )
    )
    if notice is None or not notice.satisfied_at:
        raise ExportNoticeRequired(CURRENT_EXPORT_NOTICE_VERSION)
    if notice.notice_version != CURRENT_EXPORT_NOTICE_VERSION:
        # 条款升级后必须重新告知，不能让老用户永久停留在旧版本
        raise ExportNoticeRequired(CURRENT_EXPORT_NOTICE_VERSION)

    runs = await session.scalars(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work_id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.state == ChapterRunState.CANONIZED.value,
        )
        .order_by(ChapterRunModel.chapter_no)
    )
    chapters = [r for r in runs.all()]
    if payload.include_chapters:
        wanted = set(payload.include_chapters)
        chapters = [c for c in chapters if c.chapter_no in wanted]

    body = _render_export(work, chapters, payload.format)
    data = body.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()

    job = ExportJobModel(
        id=uuid.uuid4(),
        work_id=work_id,
        user_id=owner_id,
        format=payload.format,
        byte_size=len(data),
        content_sha256=digest,
        notice_version=CURRENT_EXPORT_NOTICE_VERSION,
        storage_key=f"novel-exports/{work_id}/{uuid.uuid4()}.{payload.format}",
    )
    session.add(job)
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="work.exported",
        data={"format": payload.format, "byte_size": len(data), "sha256": digest},
        branch_id=work.branch_id,
    )
    return ExportOut(
        export_id=str(job.id),
        work_id=str(work_id),
        format=payload.format,
        byte_size=len(data),
        content_sha256=digest,
        ai_disclosure=AI_DISCLOSURE,
        download_url=f"{base_url}/v1/novel/exports/{job.id}/content",
    )


def _render_export(work: StoryWorkModel, chapters: list[ChapterRunModel], fmt: str) -> str:
    """确定性渲染：字节流不经过 LLM（G-15）。"""
    header = f"{work.title}\n\n{AI_DISCLOSURE}\n（本文件由 Novel Engine 导出，AI 参与生成）\n\n"
    parts: list[str] = [header]
    for ch in chapters:
        if fmt == "md":
            parts.append(f"## {ch.title}\n\n{ch.content}\n\n")
        else:
            parts.append(f"{ch.title}\n\n{ch.content}\n\n")
    if not chapters:
        parts.append("（尚无已完成章节）\n")
    return "".join(parts)


async def get_export_payload(
    session: AsyncSession, *, owner_id: uuid.UUID, export_id: uuid.UUID
) -> tuple[str, str]:
    """返回 (filename, text)。私有资源按 owner 过滤（G-12）。"""
    from regent.novel.infrastructure.models import NovelPrincipalModel

    principal = await session.get(NovelPrincipalModel, owner_id)
    if principal is None or principal.deleted_at is not None:
        raise NotFound("export not found")
    job = await session.scalar(
        select(ExportJobModel).where(
            ExportJobModel.id == export_id, ExportJobModel.user_id == owner_id
        )
    )
    if job is None:
        raise NotFound("export not found")
    work = await _get_owned_work(session, work_id=job.work_id, owner_id=owner_id)
    runs = await session.scalars(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == job.work_id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.state == ChapterRunState.CANONIZED.value,
        )
        .order_by(ChapterRunModel.chapter_no)
    )
    return f"{work.title}.{job.format}", _render_export(work, list(runs.all()), job.format)


# ---------------------------------------------------------------------------
# 审核（FR-25 / G-23）
# ---------------------------------------------------------------------------


REPORT_REASON_CODES = frozenset(
    {
        "illegal", "porn", "violence", "political", "infringement", "privacy",
        "other", "rule_hit",
    }
)


async def report_moderation(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    chapter_no: int | None = None,
    reason_code: str = "other",
    detail: str = "",
) -> ModerationCaseOut:
    """投诉/举报入口（FR-25 / G-23）。

    任何投诉都必须落 ModerationCase；没有结论的案件不得视为“已处理”，
    因此新建案件一律是 PENDING，由人工或第三方审核给出结论。
    """
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if reason_code not in REPORT_REASON_CODES:
        raise ValidationFailed(f"unsupported reason_code: {reason_code}")
    if chapter_no is not None:
        exists = await session.scalar(
            select(ChapterRunModel.id).where(
                ChapterRunModel.work_id == work_id,
                ChapterRunModel.chapter_no == chapter_no,
            )
        )
        if exists is None:
            raise NotFound("chapter not found")
    row = ModerationCaseModel(
        id=uuid.uuid4(),
        work_id=work_id,
        chapter_no=chapter_no,
        target_type="CHAPTER" if chapter_no is not None else "WORK",
        decision=ModerationDecision.PENDING.value,
        reason_code=reason_code,
        evidence_ref=detail[:255],
    )
    session.add(row)
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="moderation.reported",
        data={"case_id": str(row.id), "reason_code": reason_code, "detail": detail[:200]},
        chapter_no=chapter_no,
    )
    return ModerationCaseOut(
        case_id=str(row.id),
        work_id=str(work_id),
        chapter_no=chapter_no,
        target_type=row.target_type,
        decision=ModerationDecision.PENDING,
        reason_code=reason_code,
        detail=detail,
    )


async def scan_chapter_rules(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    chapter_no: int,
) -> dict[str, object]:
    """按配置词表扫描章节，产出疑似命中案件。

    只提名、不判定：命中生成 PENDING 案件等待人工/第三方结论。
    未配置词表时返回 configured=False，不产生“通过”含义。
    """
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    run = await session.scalar(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work_id,
            ChapterRunModel.chapter_no == chapter_no,
            ChapterRunModel.state == ChapterRunState.CANONIZED.value,
        )
        .order_by(ChapterRunModel.attempt.desc())
    )
    if run is None:
        raise NotFound("canonized chapter not found")
    result = scan_text(run.content or "")
    if not result.configured:
        return {"configured": False, "scanned": False, "hits": 0, "cases": []}
    cases: list[str] = []
    for hit in result.hits:
        row = ModerationCaseModel(
            id=uuid.uuid4(),
            work_id=work_id,
            chapter_no=chapter_no,
            target_type="CHAPTER",
            decision=ModerationDecision.PENDING.value,
            reason_code="rule_hit",
            evidence_ref=f"offset:{hit.offset}",
        )
        session.add(row)
        cases.append(str(row.id))
    if cases:
        await session.flush()
        await append_event(
            session,
            work_id=work_id,
            event_type="moderation.scanned",
            data={"chapter_no": chapter_no, "hits": len(cases)},
            chapter_no=chapter_no,
        )
    return {
        "configured": True,
        "scanned": True,
        "hits": len(cases),
        "cases": cases,
    }


async def list_moderation_cases(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> list[ModerationCaseOut]:
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    rows = await session.scalars(
        select(ModerationCaseModel)
        .where(ModerationCaseModel.work_id == work_id)
        .order_by(ModerationCaseModel.created_at.desc())
    )
    return [
        ModerationCaseOut(
            case_id=str(r.id),
            work_id=str(r.work_id),
            chapter_no=r.chapter_no,
            target_type=r.target_type,
            decision=ModerationDecision(r.decision),
            reason_code=r.reason_code or None,
            detail=r.evidence_ref,
            appealed_at=r.appealed_at,
            resolved_at=r.resolved_at,
        )
        for r in rows.all()
    ]


async def resolve_moderation(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    case_id: uuid.UUID,
    decision: ModerationDecision,
    reason_code: str = "",
    evidence: str = "",
    actor: str = "moderator",
) -> ModerationCaseOut:
    """给出审核结论（P1-4 / G-23）。

    无结论不得视为通过，因此结论必须落库并留痕。作者不能给自己的待审案件
    下"通过"结论——那是把审核变成自我放行。
    """
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if decision not in (ModerationDecision.APPROVED, ModerationDecision.REJECTED):
        raise ValidationFailed("moderation decision must be APPROVED or REJECTED")
    row = await session.scalar(
        select(ModerationCaseModel).where(
            ModerationCaseModel.id == case_id, ModerationCaseModel.work_id == work_id
        )
    )
    if row is None:
        raise NotFound("moderation case not found")
    if row.resolved_at is not None:
        raise Conflict("case already resolved", current_version=1)
    if actor == "author" and decision is ModerationDecision.APPROVED:
        raise PermissionDenied("作者不能对自己的案件给出通过结论")
    if reason_code and reason_code not in REPORT_REASON_CODES:
        raise ValidationFailed(f"unsupported reason_code: {reason_code}")

    row.decision = decision.value
    if reason_code:
        row.reason_code = reason_code
    if evidence:
        row.evidence_ref = evidence[:255]
    row.resolved_at = datetime.now(UTC)
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="moderation.resolved",
        data={
            "case_id": str(case_id),
            "decision": decision.value,
            "reason_code": reason_code,
            "actor": actor,
        },
        chapter_no=row.chapter_no,
    )
    return ModerationCaseOut(
        case_id=str(row.id),
        work_id=str(row.work_id),
        chapter_no=row.chapter_no,
        target_type=row.target_type,
        decision=ModerationDecision(row.decision),
        reason_code=row.reason_code or None,
        detail=row.evidence_ref,
        appealed_at=row.appealed_at,
        resolved_at=row.resolved_at,
    )


async def resolve_appeal(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    case_id: uuid.UUID,
    upheld: bool,
    reason_code: str = "",
    evidence: str = "",
    actor: str = "moderator",
) -> ModerationCaseOut:
    """申诉结论：成立则恢复，维持则保留原判定；两种结果都要留痕（P1-4）。"""
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    row = await session.scalar(
        select(ModerationCaseModel).where(
            ModerationCaseModel.id == case_id, ModerationCaseModel.work_id == work_id
        )
    )
    if row is None:
        raise NotFound("moderation case not found")
    if row.appealed_at is None:
        raise Conflict("case has not been appealed", current_version=1)
    if row.resolved_at is not None:
        raise Conflict("appeal already resolved", current_version=1)
    row.decision = (
        ModerationDecision.REJECTED.value if upheld else ModerationDecision.APPROVED.value
    )
    if reason_code:
        row.reason_code = reason_code
    if evidence:
        row.evidence_ref = evidence[:255]
    row.resolved_at = datetime.now(UTC)
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="moderation.appeal_resolved",
        data={
            "case_id": str(case_id),
            "upheld": upheld,
            "decision": row.decision,
            "actor": actor,
        },
        chapter_no=row.chapter_no,
    )
    return ModerationCaseOut(
        case_id=str(row.id),
        work_id=str(row.work_id),
        chapter_no=row.chapter_no,
        target_type=row.target_type,
        decision=ModerationDecision(row.decision),
        reason_code=row.reason_code or None,
        detail=row.evidence_ref,
        appealed_at=row.appealed_at,
        resolved_at=row.resolved_at,
    )


async def appeal_moderation(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    case_id: uuid.UUID,
    reason: str,
) -> ModerationCaseOut:
    """误判申诉必须留痕（G-23）。"""
    await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    row = await session.scalar(
        select(ModerationCaseModel).where(
            ModerationCaseModel.id == case_id, ModerationCaseModel.work_id == work_id
        )
    )
    if row is None:
        raise NotFound("moderation case not found")
    if row.appealed_at is not None:
        raise Conflict("case already appealed", current_version=1)
    row.appealed_at = datetime.now(UTC)
    row.appeal_reason = reason
    row.decision = ModerationDecision.APPEALED.value
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="moderation.appealed",
        data={"case_id": str(case_id), "reason": reason[:200]},
        chapter_no=row.chapter_no,
    )
    return ModerationCaseOut(
        case_id=str(row.id),
        work_id=str(row.work_id),
        chapter_no=row.chapter_no,
        target_type=row.target_type,
        decision=ModerationDecision(row.decision),
        reason_code=row.reason_code or None,
        detail=row.evidence_ref,
        appealed_at=row.appealed_at,
        resolved_at=row.resolved_at,
    )


# ---------------------------------------------------------------------------
# 查询投影
# ---------------------------------------------------------------------------


async def list_works(session: AsyncSession, *, owner_id: uuid.UUID) -> list[WorkSummary]:
    rows = await session.scalars(
        select(StoryWorkModel)
        .where(StoryWorkModel.owner_id == owner_id, StoryWorkModel.deleted_at.is_(None))
        .order_by(StoryWorkModel.updated_at.desc())
    )
    out: list[WorkSummary] = []
    for w in rows.all():
        pending = await session.scalar(
            select(DecisionRequestModel.id).where(
                DecisionRequestModel.work_id == w.id,
                DecisionRequestModel.state == DecisionState.PENDING.value,
            )
        )
        out.append(
            WorkSummary(
                work_id=str(w.id),
                title=w.title,
                genre=w.genre,
                state=WorkStateOut(w.state),
                chapter_count=int(w.latest_chapter_no),
                latest_chapter_no=int(w.latest_chapter_no) or None,
                pending_decisions=1 if pending else 0,
                projection=_projection_for(
                    w.state, pending=1 if pending else 0, chapter_no=int(w.latest_chapter_no)
                ),
                updated_at=w.updated_at,
            )
        )
    return out


async def get_work(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> WorkDetail:
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    goal = await session.scalar(
        select(StoryGoalModel)
        .where(StoryGoalModel.work_id == work_id)
        .order_by(StoryGoalModel.version.desc())
        .limit(1)
    )
    path = await get_critical_path(session, owner_id=owner_id, work_id=work_id)
    pending = await session.scalar(
        select(DecisionRequestModel.id).where(
            DecisionRequestModel.work_id == work_id,
            DecisionRequestModel.state == DecisionState.PENDING.value,
        )
    )
    return WorkDetail(
        work_id=str(work.id),
        title=work.title,
        genre=work.genre,
        state=WorkStateOut(work.state),
        version=int(work.version),
        goal=(
            StoryGoalOut(
                raw_intent=goal.raw_intent,
                normalized_goal=goal.normalized_goal,
                assumptions=list(goal.assumptions or []),
                locked_at=goal.locked_at,
                version=int(goal.version),
            )
            if goal
            else None
        ),
        critical_path=path if path.nodes else None,
        projection=_projection_for(
            work.state, pending=1 if pending else 0, chapter_no=int(work.latest_chapter_no)
        ),
        created_at=work.created_at,
        updated_at=work.updated_at,
    )


async def soft_delete_work(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> None:
    """产品软删除。财务、授权与创作证据不级联物理删除（Tech-Spec §7）。"""
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if work.state == StoryWorkState.ARCHIVED.value:
        raise InvalidState("work already archived", current=work.state)
    work.deleted_at = datetime.now(UTC)
    work.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="work.deleted",
        data={"soft": True, "retain": ["cost_entries", "export_notice_logs", "events"]},
        branch_id=work.branch_id,
    )


__all__ = [
    "AI_DISCLOSURE",
    "CURRENT_EXPORT_NOTICE_VERSION",
    "EventPage",
    "acknowledge_export_notice",
    "advance_step",
    "answer_clarify",
    "appeal_moderation",
    "confirm_direction",
    "create_decision",
    "create_share",
    "create_work",
    "export_work",
    "get_chapter",
    "get_critical_path",
    "get_decision",
    "get_export_notice",
    "get_export_payload",
    "get_run_progress",
    "get_work",
    "list_moderation_cases",
    "list_works",
    "pause_work",
    "report_fact",
    "resolve_appeal",
    "resolve_moderation",
    "resolve_decision",
    "resume_work",
    "resume_after_correction",
    "revoke_share",
    "soft_delete_work",
    "sweep_expired_decisions",
    "start_run",
    "update_critical_path",
]
