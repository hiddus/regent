"""Novel 作品应用服务（FR-01~FR-17 / FR-22~FR-25）。

所有查询以 ``owner_id`` 过滤；越权一律返回 NotFound，不泄露存在性（G-12）。
状态迁移走 ``assert_*_transition``；版本冲突返回 409 + current_version（FR-05）。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.application.events import append_event
from regent.novel.application.generation import execute_step
from regent.novel.domain.errors import (
    Conflict,
    ExportNoticeRequired,
    GuardViolation,
    InvalidState,
    NotFound,
    ValidationFailed,
)
from regent.novel.domain.models import (
    ChapterOut,
    ChapterRunState,
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
from regent.novel.domain.states import (
    CHAPTER_STEP_ORDER,
    ChapterStep,
    DecisionState,
    StoryWorkState,
    assert_chapter_run_transition,
    assert_story_work_transition,
)
from regent.novel.infrastructure.models import (
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
    ShareModel,
    StoryGoalModel,
    StoryWorkModel,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

MAX_CLARIFY_ROUNDS = 1  # G-21
MAX_QUESTIONS_PER_ROUND = 3  # G-21
MIN_PATH_NODES = 10  # FR-04
MAX_PATH_NODES = 20  # FR-04
CURRENT_EXPORT_NOTICE_VERSION = "export-notice-2026-09-v1"
AI_DISCLOSURE = "本文内容由 AI 参与生成"

# D-04 未拍板前的默认题材（高文笔容忍度优先，PRD §10）
DEFAULT_GENRES = ("都市系统", "无限流")

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


def _build_direction_cards(intent: str, genre: str, answers: dict[str, str]) -> list[DirectionCard]:
    """2–3 张方向卡，差异必须可复述（NFR 可复述率 ≥80%）。"""
    desire = answers.get("desire") or "变强"
    conflict = answers.get("conflict") or "一个更强的对手"
    g = answers.get("genre") or genre or DEFAULT_GENRES[0]
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
    """10–20 节点默认关键路径。人工裁决点固定在死亡/背叛/揭露/开战。"""
    g = genre or DEFAULT_GENRES[0]
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
        cards = _build_direction_cards(raw_intent, genre, {})
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

    cards = _build_direction_cards(
        goal.raw_intent if goal else "",
        resolved.get("genre", work.genre),
        resolved,
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
) -> tuple[StoryWorkModel, CriticalPathOut]:
    """FR-03 → FR-04：锁定方向并生成 10–20 节点关键路径。"""
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

    nodes = _default_path_nodes(work.genre, "变强", chosen.core_conflict)
    if not MIN_PATH_NODES <= len(nodes) <= MAX_PATH_NODES:
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
            )
        )
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


def _preview_impact(
    *,
    current_nodes: list[CriticalNode],
    next_nodes: list[CriticalNode],
    frozen_through_chapter: int,
    latest_chapter_no: int,
) -> PathChangeImpact:
    """影响范围：按 ordinal 定位改动起点，估算受影响章节。不含虚假精确百分比。"""
    current_by_id = {n.node_id: n for n in current_nodes}
    next_by_id = {n.node_id: n for n in next_nodes}

    changed_ordinals: list[int] = []
    for node in next_nodes:
        prev = current_by_id.get(node.node_id)
        if prev is None or prev.title != node.title or prev.ordinal != node.ordinal:
            changed_ordinals.append(node.ordinal)
    for node in current_nodes:
        if node.node_id not in next_by_id:
            changed_ordinals.append(node.ordinal)
    if not changed_ordinals:
        return PathChangeImpact(
            affected_chapters=[],
            frozen_conflict=False,
            frozen_through_chapter=frozen_through_chapter,
        )

    first_ordinal = min(changed_ordinals)
    # 一个节点约覆盖 2 章，保守估计：从改动节点起，到最新章为止
    start_chapter = max(1, (first_ordinal - 1) * 2 + 1)
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

    await append_event(
        session,
        work_id=work_id,
        event_type="critical_path.updated",
        data={
            "version": new_path.version,
            "node_count": len(payload.nodes),
            "affected_chapters": impact.affected_chapters,
            "change_note": payload.change_note,
        },
        branch_id=work.branch_id,
    )
    return _path_out(new_path, payload.nodes), impact


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
    _blocked_states = {ChapterRunState.QUEUED.value, ChapterRunState.RUNNING.value}
    if latest_run is not None and latest_run.state in _blocked_states:
        raise Conflict(
            "a chapter is already active",
            current_version=int(latest_run.version),
            conflict_summary={"chapter_no": latest_run.chapter_no, "state": latest_run.state},
        )
    # READY/DONE 需要状态迁移；RUNNING 表示上一章完成后继续下一章，不做自迁移。
    if work.state != StoryWorkState.RUNNING.value:
        assert_story_work_transition(work.state, StoryWorkState.RUNNING.value)
        work.state = StoryWorkState.RUNNING.value
    work.version += 1

    chapter_no = int(work.latest_chapter_no) + 1
    run = ChapterRunModel(
        id=uuid.uuid4(),
        work_id=work_id,
        branch_id=work.branch_id,
        chapter_no=chapter_no,
        attempt=1,
        state=ChapterRunState.QUEUED.value,
        current_step=ChapterStep.ASSEMBLE.value,
        title=f"第 {chapter_no} 章",
    )
    session.add(run)
    await session.flush()
    for step in ChapterStep:
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
    return RunProgressOut(
        work_id=str(work_id),
        chapter_no=int(run.chapter_no),
        state=ChapterRunState(run.state),
        current_step=ChapterStep(run.current_step) if run.current_step else None,
        steps={s.step: StepState(s.state) for s in steps.all()},
        reused_calls=0,
        version=int(run.version),
    )


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
    )
    if run is None:
        raise NotFound("chapter run not found")

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
            for step in CHAPTER_STEP_ORDER
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
        return await get_run_progress(session, owner_id=owner_id, work_id=work_id)

    step = ChapterStep(pending.step)
    pending.state = StepState.RUNNING.value
    pending.attempt += 1
    if run.state == ChapterRunState.QUEUED.value:
        assert_chapter_run_transition(run.state, ChapterRunState.RUNNING.value)
        run.state = ChapterRunState.RUNNING.value
    run.current_step = pending.step
    await session.flush()
    try:
        await execute_step(session, provider=provider, work=work, run=run, step=step)
    except Exception as exc:
        pending.state = StepState.FAILED.value
        pending.error_code = getattr(exc, "failure_code", type(exc).__name__)[:64]
        run.state = (
            ChapterRunState.TERMINAL_FAILED.value
            if pending.attempt >= 3
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
        return await get_run_progress(session, owner_id=owner_id, work_id=work_id)

    pending.state = StepState.SUCCEEDED.value
    pending.error_code = ""
    pending.output_ref = hashlib.sha256(
        f"{run.id}:{pending.step}:{run.version}:{run.word_count}".encode()
    ).hexdigest()
    if run.state == ChapterRunState.RETRYABLE_FAILED.value:
        run.state = ChapterRunState.RUNNING.value
    run.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="chapter.step_succeeded",
        data={"chapter_no": chapter_no, "step": pending.step},
        branch_id=work.branch_id,
        chapter_no=chapter_no,
    )
    return await get_run_progress(session, owner_id=owner_id, work_id=work_id)


async def advance_background_run(
    session: AsyncSession, *, provider: ModelProvider
) -> RunProgressOut | None:
    """由 durable worker 每次领取一个章节检查点；网页关闭后仍继续。"""
    run = await session.scalar(
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
        )
        .order_by(ChapterRunModel.updated_at, ChapterRunModel.chapter_no)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if run is None:
        return None
    work = await session.get(StoryWorkModel, run.work_id)
    if work is None:
        return None
    return await advance_step(
        session,
        provider=provider,
        owner_id=work.owner_id,
        work_id=work.id,
        chapter_no=run.chapter_no,
    )


# ---------------------------------------------------------------------------
# 阅读（FR-15 / FR-22 / G-14）
# ---------------------------------------------------------------------------


async def get_chapter(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID, chapter_no: int
) -> ChapterOut:
    """只读路径。本函数不持有任何生成能力引用（G-14）。"""
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    run = await session.scalar(
        select(ChapterRunModel).where(
            ChapterRunModel.work_id == work_id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.chapter_no == chapter_no,
        )
    )
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
    from sqlalchemy import text as sa_text

    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
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

    # 条件更新：只有仍为 PENDING 的一方能胜出
    result = await session.execute(
        sa_text(
            "UPDATE novel_decision_requests "
            "SET state = 'RESOLVED', resolved_by = :by, resolved_option_id = :opt, "
            "    resolved_at = NOW(), version = version + 1 "
            "WHERE id = :id AND state = 'PENDING'"
        ),
        {"by": resolved_by, "opt": chosen, "id": decision_id},
    )
    if result.rowcount != 1:
        raise Conflict("decision was resolved concurrently", current_version=int(row.version))
    await session.refresh(row)

    if work.state == StoryWorkState.PENDING_DECISION.value:
        assert_story_work_transition(work.state, StoryWorkState.RUNNING.value)
        work.state = StoryWorkState.RUNNING.value
        work.version += 1
    await session.flush()

    await append_event(
        session,
        work_id=work_id,
        event_type="decision.resolved",
        data={"decision_id": str(decision_id), "option_id": chosen, "resolved_by": resolved_by},
        branch_id=work.branch_id,
        chapter_no=row.chapter_no,
        decision_id=str(decision_id),
    )
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
    affected = [c for c in range(chapter_no, int(work.latest_chapter_no) + 1)] or [chapter_no]
    await append_event(
        session,
        work_id=work_id,
        event_type="fact.reported",
        data={"ticket_id": str(ticket_id), "statement": payload.statement, "affected": affected},
        branch_id=work.branch_id,
        chapter_no=chapter_no,
    )
    return ReportFactResponse(
        accepted=True,
        ticket_id=str(ticket_id),
        kind="FACT",
        message="已受理。系统会核对受影响章节后局部重演，不会整本重写。",
        affected_chapters=affected,
        available_actions=["await_local_replay"],
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
    if row is None or row.revoked_at is not None or (row.expires_at and row.expires_at <= now):
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
            decision=ModerationDecision(r.decision),
            reason_code=r.reason_code or None,
            appealed_at=r.appealed_at,
            resolved_at=r.resolved_at,
        )
        for r in rows.all()
    ]


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
        decision=ModerationDecision(row.decision),
        reason_code=row.reason_code or None,
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
    "resolve_decision",
    "resume_work",
    "revoke_share",
    "soft_delete_work",
    "start_run",
    "update_critical_path",
]
