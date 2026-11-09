"""作品／章节只读查询（从 works 抽出，无生成副作用）。"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application.works_access import get_owned_work
from regent.novel.application.works_constants import AI_DISCLOSURE
from regent.novel.application.works_path import get_critical_path
from regent.novel.application.works_projection import projection_for as _projection_for
from regent.novel.domain.errors import NotFound
from regent.novel.domain.models import (
    ChapterOut,
    StoryGoalOut,
    WorkDetail,
    WorkStateOut,
    WorkSummary,
)
from regent.novel.domain.states import ChapterRunState, DecisionState
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    DecisionRequestModel,
    PersonaSpecModel,
    StoryGoalModel,
    StoryWorkModel,
)

_get_owned_work = get_owned_work


def _pending_kind_from_run(run: ChapterRunModel | None) -> str | None:
    """区分普通裁决 vs 卷末扩卷确认（投影动作不同）。"""
    if run is None:
        return None
    ending = (run.generation_context or {}).get("ending_decision") or {}
    if not isinstance(ending, dict):
        return None
    reason = str(ending.get("reason") or "")
    choice = str(ending.get("choice") or "")
    if choice == "undecided" and ("扩卷" in reason or "下一卷" in reason):
        return "volume_expansion"
    if (run.generation_context or {}).get("volume_expansion_resolved"):
        return None
    return None


async def _latest_run_for_work(
    session: AsyncSession, *, work: StoryWorkModel
) -> ChapterRunModel | None:
    return await session.scalar(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work.id,
            ChapterRunModel.branch_id == work.branch_id,
        )
        .order_by(ChapterRunModel.chapter_no.desc(), ChapterRunModel.attempt.desc())
        .limit(1)
    )


async def get_chapter(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    chapter_no: int,
    attempt: int | None = None,
) -> ChapterOut:
    """只读路径。本函数不持有任何生成能力引用（G-14）。"""
    work = await get_owned_work(session, work_id=work_id, owner_id=owner_id)
    q = select(ChapterRunModel).where(
        ChapterRunModel.work_id == work_id,
        ChapterRunModel.branch_id == work.branch_id,
        ChapterRunModel.chapter_no == chapter_no,
    )
    if attempt is not None:
        q = q.where(ChapterRunModel.attempt == attempt)
    else:
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
    work = await get_owned_work(session, work_id=work_id, owner_id=owner_id)
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
    by_no: dict[int, list] = defaultdict(list)
    for r in runs:
        by_no[int(r.chapter_no)].append(r)
    result = []
    for ch_no in sorted(by_no):
        versions = by_no[ch_no]
        accepted = [r for r in versions if r.state == ChapterRunState.CANONIZED.value]
        latest = accepted[-1] if accepted else versions[-1]
        result.append(
            {
                "chapter_no": ch_no,
                "title": latest.title,
                "word_count": int(latest.word_count),
                "state": latest.state,
                "attempt": int(latest.attempt),
                "version_count": len(versions),
            }
        )
    return result


async def list_chapter_versions(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID, chapter_no: int
) -> list[dict]:
    """列出指定章节的所有版本（attempt）。"""
    work = await get_owned_work(session, work_id=work_id, owner_id=owner_id)
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
            "kind": (p.identity or {}).get("kind", ""),
            "bio": (p.identity or {}).get("bio", "") or (p.identity or {}).get("role", ""),
            "drive": (p.drives or {}).get("primary", ""),
            "voice": (p.voice or {}).get("style", ""),
            "traits": list(p.stable_traits or []),
        }
        for p in personas
    ]


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
        latest = await _latest_run_for_work(session, work=w)
        pending_kind = _pending_kind_from_run(latest)
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
                    w.state,
                    pending=1 if pending else 0,
                    chapter_no=int(w.latest_chapter_no),
                    pending_kind=pending_kind,
                ),
                updated_at=w.updated_at,
            )
        )
    return out


async def get_work(session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID) -> WorkDetail:
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
    latest = await _latest_run_for_work(session, work=work)
    pending_kind = _pending_kind_from_run(latest)
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
            work.state,
            pending=1 if pending else 0,
            chapter_no=int(work.latest_chapter_no),
            pending_kind=pending_kind,
        ),
        created_at=work.created_at,
        updated_at=work.updated_at,
    )
