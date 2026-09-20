"""M2 浏览器旅程发现的断链修复（真实浏览器 + 真实 PG 上复现）。

三条命题，各自都能被退回旧行为打红：
1. 刷新恢复 onboarding（GET /works/{id}/onboarding）；
2. 「就写这个方向」重复提交幂等（双击不是冲突）；
3. 一章都没开时不得编造进度——旧实现返回 state=QUEUED，前端把
   「有没有 progress」当成「能不能开工」，于是永远显示「实时生成中」、
   「开始第一章」按钮永不出现，真实故障被假进度吞掉。

ruff: noqa: RUF001
"""

from __future__ import annotations

import uuid

import pytest
from regent.novel.application import works
from regent.novel.domain.errors import InvalidState
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    ChapterStepModel,
    CriticalPathModel,
    NovelPrincipalModel,
    OnboardingSessionModel,
    StoryWorkModel,
)
from sqlalchemy import select


async def _onboarding_work(session, *, state: str = "ONBOARDING"):
    owner = uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject=f"m2:{owner}"))
    work = StoryWorkModel(
        id=uuid.uuid4(), owner_id=owner, state=state, genre="悬疑",
    )
    session.add(work)
    await session.flush()
    return work


def _session_row(work_id: uuid.UUID, *, with_directions: bool) -> OnboardingSessionModel:
    return OnboardingSessionModel(
        id=uuid.uuid4(),
        work_id=work_id,
        user_id=uuid.uuid4(),
        clarify_round=1,
        question_count=2,
        questions=[
            {"question_id": "q1", "prompt": "钥匙从哪来？",
             "options": ["祖传", "捡来"], "default_assumption": "祖传"},
        ],
        assumptions=[],
        directions=(
            [{"card_id": "c1", "title": "三人各执一把", "differentiator": "d",
              "protagonist_desire": "查明真相", "core_conflict": "互相猜忌",
              "pacing": "快", "genre_promise": "悬念"}] if with_directions else []
        ),
    )


@pytest.mark.asyncio
async def test_restore_onboarding_returns_clarifying_session(novel_db):
    async with novel_db() as s:
        work = await _onboarding_work(s)
        s.add(_session_row(work.id, with_directions=False))
        await s.commit()

        out = await works.get_onboarding(s, owner_id=work.owner_id, work_id=work.id)

    assert out is not None, "引导中的作品必须能恢复 onboarding 会话"
    assert out.questions, "未收口时应返回澄清问题"
    assert out.directions == []
    assert out.status == "CLARIFYING"


@pytest.mark.asyncio
async def test_restore_onboarding_returns_direction_cards_once_ready(novel_db):
    async with novel_db() as s:
        work = await _onboarding_work(s)
        s.add(_session_row(work.id, with_directions=True))
        await s.commit()

        out = await works.get_onboarding(s, owner_id=work.owner_id, work_id=work.id)

    assert out is not None
    assert out.questions == [], "澄清已收口后恢复不得重复追问（G-21 只确认一次）"
    assert [d.card_id for d in out.directions] == ["c1"]
    assert out.status == "DIRECTIONS"


@pytest.mark.asyncio
async def test_restore_onboarding_none_after_direction_confirmed(novel_db):
    async with novel_db() as s:
        work = await _onboarding_work(s, state="READY")
        s.add(_session_row(work.id, with_directions=True))
        await s.commit()

        out = await works.get_onboarding(s, owner_id=work.owner_id, work_id=work.id)

    assert out is None, "方向已确认（离开 ONBOARDING）后不得再返回引导会话"


# ---------------------------------------------------------------------------
# 方向确认必须幂等（M2 旅程实测：双击「就写这个方向」→ 第二次 POST 409，
# 前端 catch 直接中断，连 /runs 都没发出去 → 作品停在 READY 且没有章运行）
# ---------------------------------------------------------------------------


async def _confirmable(session, *, state: str = "ONBOARDING"):
    work = await _onboarding_work(session, state=state)
    session.add(_session_row(work.id, with_directions=True))
    await session.flush()
    return work


@pytest.mark.asyncio
async def test_reconfirming_same_direction_is_idempotent(novel_db):
    """同一张卡的重复确认必须回放已锁定的路径，而不是报错或重建一份。"""
    async with novel_db() as s:
        work = await _confirmable(s)
        first = await works.confirm_direction(
            s, owner_id=work.owner_id, work_id=work.id, card_id="c1"
        )
        await s.commit()
        first_ids = [n.node_id for n in first.path.nodes]

        second = await works.confirm_direction(
            s, owner_id=work.owner_id, work_id=work.id, card_id="c1"
        )
        await s.commit()

        paths = (
            await s.scalars(
                select(CriticalPathModel).where(CriticalPathModel.work_id == work.id)
            )
        ).all()

    assert second.onboarding.status == "WORLD_REVIEW"
    assert [n.node_id for n in second.path.nodes] == first_ids
    assert len(paths) == 1, "重放不得再建一份关键路径（会静默损坏首卷与弧段）"


@pytest.mark.asyncio
async def test_confirming_a_different_card_after_lock_is_a_real_conflict(novel_db):
    """锁定后再换一张卡是真冲突：必须报错，不得静默改向。"""
    async with novel_db() as s:
        work = await _confirmable(s)
        await works.confirm_direction(
            s, owner_id=work.owner_id, work_id=work.id, card_id="c1"
        )
        work.state = "READY"
        await s.commit()

        with pytest.raises(InvalidState, match="already confirmed"):
            await works.confirm_direction(
                s, owner_id=work.owner_id, work_id=work.id, card_id="c-not-chosen"
            )


# ---------------------------------------------------------------------------
# 「没有章运行」必须说实话（M2 旅程实测：POST /runs 报 500，GET /runs 却回 200
# QUEUED → 前端显示「第 1 章 实时生成中」，用户既看不到错也开不了工）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_run_is_reported_as_absent_not_as_queued(novel_db):
    """没有章运行时，GET 入口用的函数必须返回 None，不得编造 QUEUED。"""
    async with novel_db() as s:
        work = await _onboarding_work(s, state="READY")
        await s.commit()

        out = await works.get_active_run_progress(
            s, owner_id=work.owner_id, work_id=work.id
        )

    assert out is None, "一章都没开时报 QUEUED 会让前端把故障显示成「实时生成中」"


@pytest.mark.asyncio
async def test_active_run_progress_reports_the_real_run(novel_db):
    """有运行时就照实回报——别把上面那条修成「永远返回 None」。"""
    async with novel_db() as s:
        work = await _onboarding_work(s, state="RUNNING")
        run = ChapterRunModel(
            id=uuid.uuid4(),
            work_id=work.id,
            branch_id=work.branch_id,
            chapter_no=1,
            attempt=1,
            state="RUNNING",
            current_step="ASSEMBLE",
            generation_context={"architecture_version": "director_v2"},
        )
        s.add(run)
        await s.flush()
        for step in ("ASSEMBLE", "DIRECT", "PRODUCE", "REVIEW", "CANON"):
            s.add(
                ChapterStepModel(
                    id=uuid.uuid4(), run_id=run.id, step=step, state="PENDING", input_version=1
                )
            )
        await s.commit()

        out = await works.get_active_run_progress(
            s, owner_id=work.owner_id, work_id=work.id
        )

    assert out is not None
    assert out.chapter_no == 1
    assert out.state.value == "RUNNING"
    assert set(out.steps) == {"ASSEMBLE", "DIRECT", "PRODUCE", "REVIEW", "CANON"}
