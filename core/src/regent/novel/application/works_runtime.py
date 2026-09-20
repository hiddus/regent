"""章节运行推进：后台领取与前章依赖屏障。

从 ``works`` 抽出，供 Worker 与 API 共用同一推进实现；不经兼容入口反向依赖。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from regent.model import ModelProvider
from regent.novel.application.production import TransactionFreeProvider
from regent.novel.domain.models import RunProgressOut
from regent.novel.domain.states import ChapterRunState, StoryWorkState
from regent.novel.infrastructure.models import ChapterRunModel, StoryWorkModel

logger = logging.getLogger(__name__)

# 候选窗口：屏障挡住的任务不得占满整窗饿死其他作品（C04）。
_CANDIDATE_LIMIT = 64
_IN_FLIGHT = (
    ChapterRunState.QUEUED.value,
    ChapterRunState.RUNNING.value,
    ChapterRunState.PENDING_DECISION.value,
    ChapterRunState.AWAITING_INPUT.value,
    ChapterRunState.RETRYABLE_FAILED.value,
)


def _earlier_barrier_exists():
    """QUEUED 候选：同作品同分支存在更早在途章时不可领取。"""
    earlier = aliased(ChapterRunModel)
    return exists(
        select(earlier.id).where(
            earlier.work_id == ChapterRunModel.work_id,
            earlier.branch_id == ChapterRunModel.branch_id,
            earlier.chapter_no < ChapterRunModel.chapter_no,
            earlier.state.in_(_IN_FLIGHT),
        )
    )


async def advance_background_run(
    session: AsyncSession, *, provider: ModelProvider
) -> RunProgressOut | None:
    """由 durable worker 每次领取一个章节检查点；网页关闭后仍继续。

    D-02 依赖屏障：``QUEUED``（新章起跑）不得越过同作品同分支更早章节的
    在途运行。屏障尽量下推 SQL，并扩大窗口，避免阻塞任务饿死其他作品。
    推进按锁定到的 ``run_id``，不只传章节号。
    """
    from regent.novel.application.works_advance import advance_step
    from regent.novel.application.works_continuation import compensate_continuation

    try:
        await compensate_continuation(session)
    except Exception:
        logger.exception("compensate_continuation failed; continuing claim")
    provider = TransactionFreeProvider(provider, session)
    barrier = _earlier_barrier_exists()
    claimable = or_(
        ChapterRunModel.state.in_(
            (ChapterRunState.RUNNING.value, ChapterRunState.RETRYABLE_FAILED.value)
        ),
        and_(ChapterRunModel.state == ChapterRunState.QUEUED.value, ~barrier),
    )
    candidates = list(
        (
            await session.scalars(
                select(ChapterRunModel)
                .join(StoryWorkModel, StoryWorkModel.id == ChapterRunModel.work_id)
                .where(
                    StoryWorkModel.state == StoryWorkState.RUNNING.value,
                    StoryWorkModel.deleted_at.is_(None),
                    ChapterRunModel.branch_id == StoryWorkModel.branch_id,
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
                    claimable,
                )
                .order_by(ChapterRunModel.updated_at, ChapterRunModel.chapter_no)
                .limit(_CANDIDATE_LIMIT)
            )
        ).all()
    )
    for candidate in candidates:
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
        # 锁内再复核屏障（竞态窗口）
        if candidate.state == ChapterRunState.QUEUED.value:
            earlier_in_flight = await session.scalar(
                select(func.count(ChapterRunModel.id)).where(
                    ChapterRunModel.work_id == candidate.work_id,
                    ChapterRunModel.branch_id == candidate.branch_id,
                    ChapterRunModel.chapter_no < candidate.chapter_no,
                    ChapterRunModel.state.in_(_IN_FLIGHT),
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
            run_id=candidate.id,
        )
    return None
