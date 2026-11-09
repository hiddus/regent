"""Novel 作品应用服务（FR-01~FR-17 / FR-22~FR-25）。

所有查询以 ``owner_id`` 过滤；越权一律返回 NotFound，不泄露存在性（G-12）。
状态迁移走 ``assert_*_transition``；版本冲突返回 409 + current_version（FR-05）。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.application import executor as executor_app
from regent.novel.application import memory as memory_app
from regent.novel.application.directing_protocol import is_directed
from regent.novel.domain.errors import BudgetExhausted, ProductionStopped
from regent.novel.application.events import append_event
from regent.novel.application.generation import execute_step, generate_ending_verdict
from regent.novel.application.principal import as_utc
from regent.novel.application.production import (
    TransactionFreeProvider,
    acquire_run_lease,
    lease_is_valid,
    release_run_lease,
)
from regent.novel.application.works_constants import (
    AI_DISCLOSURE,
    CHAPTERS_PER_NODE,
    CHECKPOINT_STEPS,
    CURRENT_EXPORT_NOTICE_VERSION,
    DEFAULT_GENRES,
    EXPORT_NOTICE_BODY as _EXPORT_NOTICE_BODY,
    EXPORT_NOTICE_TITLE as _EXPORT_NOTICE_TITLE,
    LEASE_OWNER as _LEASE_OWNER,
    MAX_CLARIFY_ROUNDS,
    MAX_NODES_PER_VOLUME,
    MAX_PATH_NODES,
    MAX_QUESTIONS_PER_ROUND,
    MIN_NODES_PER_VOLUME,
    MIN_PATH_NODES,
    OnboardingStatus,
    RETRY_BACKOFF as _RETRY_BACKOFF,
)
from regent.novel.application.works_projection import (
    _fingerprint,
    _next_milestone,
    _projection_for,
)
from regent.novel.application.works_query import get_work, list_characters, list_works
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
    ConfirmDirectionOut,
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
    WorldBibleOut,
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

# 步骤失败必须出声：2026-09-10 那次事故里，整条流水线停了 15 分钟而**一条日志
# 都没有**——失败只写进了数据库表，没人看表。健康检查照常 200。
logger = logging.getLogger(__name__)

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


from regent.novel.application.works_onboarding import (
    _build_clarify_questions,
    _build_direction_cards,
    _default_path_nodes,
    create_work,
    answer_clarify,
    get_onboarding,
    revise_directions,
    _onboarding_out,
    confirm_direction
)

from regent.novel.application.works_world_bible import (
    get_world_bible,
    revise_world_bible,
    lock_world_bible,
    ensure_legacy_story_bible
)

from regent.novel.application.works_volumes import (
    expand_next_volume,
    get_volumes,
    set_ending_intent,
    resolve_ending
)

from regent.novel.application.works_run_control import (
    start_run,
    pause_work,
    resume_work,
    authorize_budget,
    resume_after_correction,
    submit_guidance,
    set_auto_advance
)

from regent.novel.application.works_replay import (
    _queue_replay_run,
    regenerate_chapter,
    report_fact
)

from regent.novel.application.works_sharing import (
    create_share,
    revoke_share,
    get_public_share
)

from regent.novel.application.works_exports import (
    get_export_notice,
    acknowledge_export_notice,
    export_work,
    _render_export,
    get_export_payload
)

from regent.novel.application.works_moderation import (
    REPORT_REASON_CODES,
    report_moderation,
    scan_chapter_rules,
    list_moderation_cases,
    resolve_moderation,
    resolve_appeal,
    appeal_moderation
)

from regent.novel.application.works_lifecycle import (
    soft_delete_work
)

async def _get_owned_work(
    session: AsyncSession, *, work_id: uuid.UUID, owner_id: uuid.UUID
) -> StoryWorkModel:
    """G-12：无 owner 条件的私有读取 fail closed。"""
    from regent.novel.application.works_access import get_owned_work

    return await get_owned_work(session, work_id=work_id, owner_id=owner_id)


# ---------------------------------------------------------------------------
# Onboarding（FR-01 / FR-02 / FR-03 / G-21）
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# 作品生命周期
# ---------------------------------------------------------------------------





















# ---------------------------------------------------------------------------
# 关键路径（FR-04 / FR-05）
# ---------------------------------------------------------------------------


from regent.novel.application.works_path import (
    _node_changes, _path_out, _preview_impact, get_critical_path, update_critical_path,
)



from regent.novel.application.works_volumes import (
    _after_chapter_completed,
    _cn_num,
    _decide_ending,
    _director_ending_verdict,
    _ending_intent_of,
    _last_node_completed,
    _mark_ending_decision,
    _mark_story_complete,
    _maybe_expand_volume,
    _path_node_ids,
)



# ---------------------------------------------------------------------------
# 运行（FR-06 / FR-13 / FR-20）
# ---------------------------------------------------------------------------
















from regent.novel.application.works_run_control import (
    _latest_run,
    _progress_for_run,
    get_active_run_progress,
    get_run_progress,
)

def _rewind_review_to_weave(*, run: ChapterRunModel, by_name: dict[str, ChapterStepModel]) -> None:
    from regent.novel.application.works_advance import _rewind_review_to_weave as impl
    impl(run=run, by_name=by_name)


async def advance_step(
    session: AsyncSession, *, provider: ModelProvider, owner_id: uuid.UUID,
    work_id: uuid.UUID, chapter_no: int,
) -> RunProgressOut:
    from regent.novel.application.works_advance import advance_step as impl
    return await impl(
        session, provider=provider, owner_id=owner_id, work_id=work_id,
        chapter_no=chapter_no, execute_step_fn=execute_step,
        append_event_fn=append_event,
    )


async def advance_background_run(
    session: AsyncSession, *, provider: ModelProvider
) -> RunProgressOut | None:
    """兼容入口：实现已迁至 ``works_runtime.advance_background_run``。"""
    from regent.novel.application.works_runtime import (
        advance_background_run as _advance,
    )

    return await _advance(session, provider=provider)


# ---------------------------------------------------------------------------
# 人在回路检查点
# ---------------------------------------------------------------------------


async def bump_input_version(
    session: AsyncSession,
    *,
    run: ChapterRunModel,
    reason: str,
) -> int:
    """兼容入口：实现已迁至 ``works_input.bump_input_version``。"""
    from regent.novel.application.works_input import bump_input_version as _bump

    return await _bump(session, run=run, reason=reason)






# ---------------------------------------------------------------------------
# 阅读（FR-15 / FR-22 / G-14）
# ---------------------------------------------------------------------------


async def get_chapter(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID, chapter_no: int,
    attempt: int | None = None,
) -> ChapterOut:
    """兼容入口：实现已迁至 ``works_query.get_chapter``。"""
    from regent.novel.application.works_query import get_chapter as _get

    return await _get(
        session,
        owner_id=owner_id,
        work_id=work_id,
        chapter_no=chapter_no,
        attempt=attempt,
    )


async def list_chapters(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> list[dict]:
    """兼容入口：实现已迁至 ``works_query.list_chapters``。"""
    from regent.novel.application.works_query import list_chapters as _list

    return await _list(session, owner_id=owner_id, work_id=work_id)


async def list_chapter_versions(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID, chapter_no: int
) -> list[dict]:
    """兼容入口：实现已迁至 ``works_query.list_chapter_versions``。"""
    from regent.novel.application.works_query import list_chapter_versions as _list

    return await _list(
        session, owner_id=owner_id, work_id=work_id, chapter_no=chapter_no
    )








# ---------------------------------------------------------------------------
# 裁决（FR-10 / G-13）
# ---------------------------------------------------------------------------


def _decision_view(row: DecisionRequestModel) -> DecisionView:
    from regent.novel.application.decisions import decision_view

    return decision_view(row)


async def list_pending_decisions(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID | None = None
) -> list[DecisionView]:
    """待裁决收件箱（FR-12）。

    跨作品聚合时只返回本人作品（G-11/G-12）：没有 owner 条件的读取一律 fail closed。
    """
    from regent.novel.application.decisions import list_pending_decisions as _list

    return await _list(session, owner_id=owner_id, work_id=work_id)


async def get_decision(session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID, decision_id: uuid.UUID) -> DecisionView:
    from regent.novel.application.decisions import get_decision as impl
    return await impl(session, owner_id=owner_id, work_id=work_id, decision_id=decision_id)



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
    """兼容入口：实现已迁至 ``decisions.create_decision``。"""
    from regent.novel.application.decisions import create_decision as _create

    return await _create(
        session,
        owner_id=owner_id,
        work_id=work_id,
        chapter_no=chapter_no,
        run_id=run_id,
        node_id=node_id,
        trigger_summary=trigger_summary,
        why_human=why_human,
        options=options,
        default_option_id=default_option_id,
        impact_level=impact_level,
        impact_horizon_chapters=impact_horizon_chapters,
        deadline=deadline,
    )


async def _apply_decision(
    session: AsyncSession,
    *,
    row: DecisionRequestModel,
    chosen: str,
    resolved_by: str,
) -> None:
    """兼容入口：实现已迁至 ``decisions.apply_decision``。"""
    from regent.novel.application.decisions import apply_decision

    await apply_decision(session, row=row, chosen=chosen, resolved_by=resolved_by)


async def sweep_expired_decisions(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 20
) -> list[str]:
    """兼容入口：实现已迁至 ``decisions.sweep_expired_decisions``。"""
    from regent.novel.application.decisions import sweep_expired_decisions as _sweep

    return await _sweep(session, now=now, limit=limit)


async def resolve_decision(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID,
    decision_id: uuid.UUID, option_id: str | None, accept_default: bool,
    confirm_nonce: str, resolved_by: str = "user",
) -> DecisionView:
    from regent.novel.application.decisions import resolve_decision as impl
    return await impl(
        session, owner_id=owner_id, work_id=work_id, decision_id=decision_id,
        option_id=option_id, accept_default=accept_default,
        confirm_nonce=confirm_nonce, resolved_by=resolved_by,
    )



# ---------------------------------------------------------------------------
# 事实报错（FR-11）
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# 分享（FR-17）/ 导出（FR-23 / G-15 / G-22）
# ---------------------------------------------------------------------------


















# ---------------------------------------------------------------------------
# 审核（FR-25 / G-23）
# ---------------------------------------------------------------------------














# ---------------------------------------------------------------------------
# 查询投影
# ---------------------------------------------------------------------------








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
    "get_active_run_progress",
    "get_work",
    "list_moderation_cases",
    "list_works",
    "pause_work",
    "report_fact",
    "resolve_appeal",
    "resolve_moderation",
    "resolve_decision",
    "resume_work",
    "authorize_budget",
    "resume_after_correction",
    "revoke_share",
    "soft_delete_work",
    "sweep_expired_decisions",
    "start_run",
    "update_critical_path",
]
