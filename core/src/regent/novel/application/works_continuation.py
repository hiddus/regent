"""作品级连续创作：成章后幂等创建后继章。

与章内 ``run.auto_advance`` 语义分离：后者只跳过章内检查点。
连续创作必须显式授权，默认关闭；旧作品不因部署自动获得无限章。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application import executor as executor_app
from regent.novel.application.events import append_event
from regent.novel.domain.errors import ValidationFailed
from regent.novel.domain.states import (
    ChapterRunState,
    ChapterStep,
    StepState,
    StoryWorkState,
    assert_story_work_transition,
    chapter_step_order,
)
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    ChapterStepModel,
    StoryWorkModel,
    VolumeModel,
)

logger = logging.getLogger(__name__)

_BLOCKING_LATEST = frozenset(
    {
        ChapterRunState.QUEUED.value,
        ChapterRunState.RUNNING.value,
        ChapterRunState.RETRYABLE_FAILED.value,
        ChapterRunState.PENDING_DECISION.value,
        ChapterRunState.AWAITING_INPUT.value,
        ChapterRunState.TERMINAL_FAILED.value,
    }
)


@dataclass(frozen=True)
class ContinuationPolicy:
    enabled: bool = False
    target_chapter_no: int | None = None
    max_chapters: int | None = None
    volume_scope: str = "current"
    version: int = 1
    budget_grant_note: str = ""
    # 授权时钉扎的卷身份；current/authorized 都依赖它，避免扩卷后静默越界
    authorized_volume_no: int | None = None
    authorized_end_chapter_no: int | None = None


def _policy_raw(work: StoryWorkModel) -> dict[str, Any]:
    bible = dict(work.story_bible or {})
    raw = bible.get("continuation_policy")
    return dict(raw) if isinstance(raw, dict) else {}


def get_continuation_policy(work: StoryWorkModel) -> ContinuationPolicy:
    raw = _policy_raw(work)
    target = raw.get("target_chapter_no")
    max_ch = raw.get("max_chapters")
    auth_vol = raw.get("authorized_volume_no")
    auth_end = raw.get("authorized_end_chapter_no")
    return ContinuationPolicy(
        enabled=bool(raw.get("enabled")),
        target_chapter_no=int(target) if target not in (None, "", 0) else None,
        max_chapters=int(max_ch) if max_ch not in (None, "", 0) else None,
        volume_scope=str(raw.get("volume_scope") or "current"),
        version=int(raw.get("version") or 1),
        budget_grant_note=str(raw.get("budget_grant_note") or ""),
        authorized_volume_no=int(auth_vol) if auth_vol not in (None, "", 0) else None,
        authorized_end_chapter_no=(
            int(auth_end) if auth_end not in (None, "", 0) else None
        ),
    )


def set_continuation_policy(
    work: StoryWorkModel,
    *,
    enabled: bool,
    target_chapter_no: int | None = None,
    max_chapters: int | None = None,
    volume_scope: str = "current",
    budget_grant_note: str = "",
    expected_version: int | None = None,
    authorized_volume_no: int | None = None,
    authorized_end_chapter_no: int | None = None,
) -> ContinuationPolicy:
    scope = str(volume_scope or "current").strip().lower() or "current"
    if scope not in ("current", "authorized", "unbounded"):
        raise ValidationFailed("volume_scope must be current|authorized|unbounded")
    if target_chapter_no is not None and int(target_chapter_no) < 1:
        raise ValidationFailed("target_chapter_no must be >= 1")
    if max_chapters is not None and int(max_chapters) < 1:
        raise ValidationFailed("max_chapters must be >= 1")
    current = get_continuation_policy(work)
    if expected_version is not None and int(expected_version) != int(current.version):
        raise ValidationFailed(
            f"continuation policy version conflict: expected {expected_version}, "
            f"current {current.version}"
        )
    # current/authorized：钉扎卷身份。未显式传入时取作品当前卷号。
    pin_vol = authorized_volume_no
    pin_end = authorized_end_chapter_no
    if scope in ("current", "authorized"):
        if pin_vol is None:
            pin_vol = int(work.total_volume_count or 0) or None
        if pin_vol is not None and int(pin_vol) < 1:
            raise ValidationFailed("authorized_volume_no must be >= 1")
        if pin_end is not None and int(pin_end) < 1:
            raise ValidationFailed("authorized_end_chapter_no must be >= 1")
    else:
        pin_vol = None
        pin_end = None
    bible = dict(work.story_bible or {})
    policy = {
        "enabled": bool(enabled),
        "target_chapter_no": target_chapter_no,
        "max_chapters": max_chapters,
        "volume_scope": scope,
        "version": int((_policy_raw(work).get("version") or 0)) + 1,
        "budget_grant_note": budget_grant_note,
        "authorized_volume_no": pin_vol,
        "authorized_end_chapter_no": pin_end,
        "set_at": datetime.now(UTC).isoformat(),
    }
    bible["continuation_policy"] = policy
    work.story_bible = bible
    work.version += 1
    return get_continuation_policy(work)


def refresh_continuation_after_volume_expand(
    work: StoryWorkModel,
    *,
    new_volume_no: int,
    new_end_chapter_no: int | None = None,
) -> ContinuationPolicy | None:
    """扩卷确认后的连续授权处理。

    - volume_scope=current：扩卷即重新钉扎到新卷（确认扩卷=续写授权延展）
    - volume_scope=authorized：不自动延展，仍停在原授权卷/章尾
    - unbounded：无卷边界，仅 bump 备注
    """
    pol = get_continuation_policy(work)
    if not pol.enabled:
        return None
    scope = pol.volume_scope
    if scope == "authorized":
        return pol
    if scope == "unbounded":
        return pol
    # current：重新授权到新卷
    bible = dict(work.story_bible or {})
    raw = dict(_policy_raw(work))
    raw["authorized_volume_no"] = int(new_volume_no)
    if new_end_chapter_no is not None and int(new_end_chapter_no) > 0:
        raw["authorized_end_chapter_no"] = int(new_end_chapter_no)
    else:
        raw["authorized_end_chapter_no"] = None
    raw["version"] = int(raw.get("version") or 0) + 1
    raw["reauthorized_by"] = "volume_expand"
    raw["set_at"] = datetime.now(UTC).isoformat()
    bible["continuation_policy"] = raw
    work.story_bible = bible
    work.version += 1
    return get_continuation_policy(work)


async def _has_successor(
    session: AsyncSession, *, work: StoryWorkModel, chapter_no: int
) -> ChapterRunModel | None:
    return await session.scalar(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work.id,
            ChapterRunModel.branch_id == work.branch_id,
            ChapterRunModel.chapter_no == chapter_no,
        )
        .order_by(ChapterRunModel.attempt.desc())
        .limit(1)
    )


async def _earlier_blocks_continuation(
    session: AsyncSession, *, work: StoryWorkModel, before_chapter: int
) -> bool:
    """每章只看权威最新 attempt：旧 TERMINAL_FAILED + 新 CANONIZED 不得挡后继。"""
    rows = list(
        (
            await session.scalars(
                select(ChapterRunModel)
                .where(
                    ChapterRunModel.work_id == work.id,
                    ChapterRunModel.branch_id == work.branch_id,
                    ChapterRunModel.chapter_no < before_chapter,
                )
                .order_by(ChapterRunModel.chapter_no, ChapterRunModel.attempt.desc())
            )
        ).all()
    )
    latest_by_chapter: dict[int, ChapterRunModel] = {}
    for row in rows:
        ch = int(row.chapter_no)
        if ch not in latest_by_chapter:
            latest_by_chapter[ch] = row
    for run in latest_by_chapter.values():
        if str(run.state) in _BLOCKING_LATEST:
            return True
    return False


async def _current_volume_end_chapter(
    session: AsyncSession, *, work: StoryWorkModel
) -> int | None:
    vol_no = int(work.total_volume_count or 0)
    if vol_no <= 0:
        return None
    return await _volume_end_chapter(session, work=work, volume_no=vol_no)


async def _volume_end_chapter(
    session: AsyncSession, *, work: StoryWorkModel, volume_no: int
) -> int | None:
    if volume_no <= 0:
        return None
    vol = await session.scalar(
        select(VolumeModel).where(
            VolumeModel.work_id == work.id,
            VolumeModel.volume_no == int(volume_no),
        )
    )
    if vol is None:
        return None
    end = int(vol.end_chapter_no or 0)
    return end if end > 0 else None


async def ensure_next_run(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    completed_run: ChapterRunModel | None = None,
) -> ChapterRunModel | None:
    """幂等创建后继章任务。无授权/目标到齐/已有后继时返回已有任务或 None。

    锁作品行后复核；唯一约束冲突用保存点隔离并返回已有后继（F5）。
    """
    if work.deleted_at is not None:
        return None
    # 锁作品推进记录，避免双 Worker 同时补偿
    locked = await session.scalar(
        select(StoryWorkModel)
        .where(StoryWorkModel.id == work.id)
        .with_for_update()
    )
    if locked is None:
        return None
    work = locked
    if work.state not in (
        StoryWorkState.RUNNING.value,
        StoryWorkState.FAILED.value,
        StoryWorkState.READY.value,
    ):
        return None
    policy = get_continuation_policy(work)
    if not policy.enabled:
        return None
    if completed_run is None:
        completed_run = await session.scalar(
            select(ChapterRunModel)
            .where(
                ChapterRunModel.work_id == work.id,
                ChapterRunModel.branch_id == work.branch_id,
            )
            .order_by(ChapterRunModel.chapter_no.desc(), ChapterRunModel.attempt.desc())
            .limit(1)
        )
    if completed_run is None:
        return None
    if completed_run.state != ChapterRunState.CANONIZED.value:
        return None
    next_ch = int(completed_run.chapter_no) + 1
    if policy.target_chapter_no is not None and next_ch > int(policy.target_chapter_no):
        return None
    if policy.max_chapters is not None and next_ch > int(policy.max_chapters):
        return None
    # volume_scope：current/authorized 用钉扎卷尾；unbounded 不限卷
    if policy.volume_scope in ("current", "authorized"):
        end_ch = policy.authorized_end_chapter_no
        if end_ch is None and policy.authorized_volume_no is not None:
            end_ch = await _volume_end_chapter(
                session, work=work, volume_no=int(policy.authorized_volume_no)
            )
        if end_ch is None:
            # 兼容旧策略：未钉扎时回退到当前活跃卷尾
            end_ch = await _current_volume_end_chapter(session, work=work)
        if end_ch is not None and next_ch > end_ch:
            return None
    existing = await _has_successor(session, work=work, chapter_no=next_ch)
    if existing is not None:
        return existing
    if await _earlier_blocks_continuation(session, work=work, before_chapter=next_ch):
        return None
    if not work.story_bible_locked_at:
        return None

    executor = executor_app.choose_executor(work.id)
    run = ChapterRunModel(
        id=uuid.uuid4(),
        work_id=work.id,
        branch_id=work.branch_id,
        chapter_no=next_ch,
        attempt=1,
        state=ChapterRunState.QUEUED.value,
        current_step=ChapterStep.ASSEMBLE.value,
        title=f"第 {next_ch} 章",
        generation_context={
            "architecture_version": executor,
            "executor": executor,
            "executor_version": executor_app.executor_version(executor),
            "continuation": {
                "source_run_id": str(completed_run.id),
                "source_chapter_no": int(completed_run.chapter_no),
                "policy_version": policy.version,
            },
            "call_key_version": 2,
            "production": {"call_key_version": 2},
        },
        auto_advance=True,
    )
    try:
        async with session.begin_nested():
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
    except IntegrityError:
        logger.info(
            "continuation race: successor already exists work=%s chapter=%s",
            work.id,
            next_ch,
        )
        return await _has_successor(session, work=work, chapter_no=next_ch)

    if work.state != StoryWorkState.RUNNING.value:
        assert_story_work_transition(work.state, StoryWorkState.RUNNING.value)
        work.state = StoryWorkState.RUNNING.value
    work.latest_chapter_no = max(int(work.latest_chapter_no), next_ch)
    work.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=work.id,
        event_type="run.continued",
        data={
            "chapter_no": next_ch,
            "run_id": str(run.id),
            "source_chapter_no": int(completed_run.chapter_no),
            "policy_version": policy.version,
        },
        branch_id=work.branch_id,
        chapter_no=next_ch,
    )
    return run


async def compensate_continuation(session: AsyncSession) -> int:
    """补偿扫描：已授权连续、上一章已接受、无后继 → ensure_next_run。"""
    policy_works = (
        await session.scalars(
            select(StoryWorkModel).where(
                StoryWorkModel.deleted_at.is_(None),
                StoryWorkModel.state.in_(
                    (
                        StoryWorkState.RUNNING.value,
                        StoryWorkState.FAILED.value,
                        StoryWorkState.READY.value,
                    )
                ),
            )
        )
    ).all()
    created = 0
    for work in policy_works:
        if not get_continuation_policy(work).enabled:
            continue
        latest = await session.scalar(
            select(ChapterRunModel)
            .where(
                ChapterRunModel.work_id == work.id,
                ChapterRunModel.branch_id == work.branch_id,
            )
            .order_by(ChapterRunModel.chapter_no.desc(), ChapterRunModel.attempt.desc())
            .limit(1)
        )
        if latest is None or latest.state != ChapterRunState.CANONIZED.value:
            continue
        run = await ensure_next_run(session, work=work, completed_run=latest)
        if run is not None and getattr(run, "chapter_no", None) == int(latest.chapter_no) + 1:
            # 新创建或已有后继都算补偿命中；仅新插入时 version 会变，此处宽松计数
            created += 1
    return created


__all__ = [
    "ContinuationPolicy",
    "compensate_continuation",
    "ensure_next_run",
    "get_continuation_policy",
    "refresh_continuation_after_volume_expand",
    "set_continuation_policy",
]
