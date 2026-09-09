"""P1-2 导演裁决闭环（Plan v6.4 §10）。

验收点：

- 导演主动创建持久 DecisionRequest，章节进入等待；
- 用户选项与到期默认竞争，只有一个能落定；
- 结果写回章节、递增 input_version 并恢复推进——不是只改一个通知状态。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from regent.novel.application import works
from regent.novel.domain.errors import Conflict, ValidationFailed
from regent.novel.domain.states import ChapterRunState, DecisionState, StoryWorkState
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    DecisionRequestModel,
    NovelPrincipalModel,
    StoryWorkModel,
)
from sqlalchemy import select


async def _work_with_run(session, *, state=StoryWorkState.RUNNING.value, run_state="RUNNING"):
    owner = uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject=f"decision-test:{owner}"))
    work = StoryWorkModel(id=uuid.uuid4(), owner_id=owner, state=state, genre="悬疑")
    session.add(work)
    await session.flush()
    run = ChapterRunModel(
        id=uuid.uuid4(),
        work_id=work.id,
        branch_id=work.branch_id,
        chapter_no=1,
        state=run_state,
        input_version=1,
    )
    session.add(run)
    await session.flush()
    return owner, work, run


OPTIONS = [
    {
        "option_id": "trust",
        "label": "交出钥匙",
        "near_term_consequence": "信任建立，风险上升",
        "reversibility": "COSTLY",
    },
    {
        "option_id": "refuse",
        "label": "拒绝",
        "near_term_consequence": "关系僵持，风险推迟",
        "reversibility": "REVERSIBLE",
    },
]


async def test_create_decision_persists_and_pauses_the_chapter(novel_db, monkeypatch):
    """导演发起裁决：请求落库，作品与章节进入等待。"""
    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)
    async with novel_db() as session:
        owner, work, run = await _work_with_run(session)
        view = await works.create_decision(
            session,
            owner_id=owner,
            work_id=work.id,
            chapter_no=1,
            run_id=run.id,
            trigger_summary="这场戏要不要交出钥匙",
            why_human="影响后续三章的信任线",
            options=OPTIONS,
            default_option_id="refuse",
            impact_horizon_chapters=3,
        )
        assert view.state == DecisionState.PENDING
        assert view.confirm_nonce, "没有 nonce 的裁决无法防止重复提交"
        await session.commit()

    async with novel_db() as session:
        row = await session.scalar(select(DecisionRequestModel))
        assert row.state == DecisionState.PENDING.value
        assert row.default_option_id == "refuse"
        work = await session.scalar(select(StoryWorkModel))
        run = await session.scalar(select(ChapterRunModel))
        assert work.state == StoryWorkState.PENDING_DECISION.value
        assert run.state == ChapterRunState.PENDING_DECISION.value


async def test_default_option_must_exist(novel_db, monkeypatch):
    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)
    async with novel_db() as session:
        owner, work, run = await _work_with_run(session)
        with pytest.raises(ValidationFailed, match="default option"):
            await works.create_decision(
                session,
                owner_id=owner,
                work_id=work.id,
                chapter_no=1,
                run_id=run.id,
                trigger_summary="无默认项的裁决",
                why_human="到期无人选择就会落空",
                options=OPTIONS,
                default_option_id="missing",
            )


async def test_resolution_changes_input_version_and_resumes(novel_db, monkeypatch):
    """用户选择必须真的改变后续生成：input_version 递增且章节恢复推进。"""
    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)
    async with novel_db() as session:
        owner, work, run = await _work_with_run(session)
        view = await works.create_decision(
            session,
            owner_id=owner,
            work_id=work.id,
            chapter_no=1,
            run_id=run.id,
            trigger_summary="要不要交出钥匙",
            why_human="影响后续走向",
            options=OPTIONS,
            default_option_id="refuse",
        )
        await session.commit()
        decision_id = uuid.UUID(view.decision_id)

    async with novel_db() as session:
        row = await session.scalar(select(DecisionRequestModel))
        await works.resolve_decision(
            session,
            owner_id=owner,
            work_id=work.id,
            decision_id=decision_id,
            option_id="trust",
            accept_default=False,
            confirm_nonce=row.confirm_nonce,
        )
        await session.commit()

    async with novel_db() as session:
        row = await session.scalar(select(DecisionRequestModel))
        assert row.state == DecisionState.RESOLVED.value
        assert row.resolved_option_id == "trust"
        assert row.resolved_by == "user"
        run = await session.scalar(select(ChapterRunModel))
        # 选择进入生成上下文：导演下一轮看得到，而不只是通知里出现
        assert run.generation_context["decision_resolutions"][0]["option_id"] == "trust"
        assert run.input_version == 2, "用户选择没有让旧方向失效"
        assert run.state == ChapterRunState.RUNNING.value


async def test_only_one_side_wins_the_race(novel_db, monkeypatch):
    """用户提交与到期默认竞争：第二次落定必须失败。"""
    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)
    async with novel_db() as session:
        owner, work, run = await _work_with_run(session)
        view = await works.create_decision(
            session,
            owner_id=owner,
            work_id=work.id,
            chapter_no=1,
            run_id=run.id,
            trigger_summary="要不要交出钥匙",
            why_human="影响后续走向",
            options=OPTIONS,
            default_option_id="refuse",
        )
        await session.commit()
        decision_id = uuid.UUID(view.decision_id)

    async with novel_db() as session:
        row = await session.scalar(select(DecisionRequestModel))
        await works.resolve_decision(
            session,
            owner_id=owner,
            work_id=work.id,
            decision_id=decision_id,
            option_id="trust",
            accept_default=False,
            confirm_nonce=row.confirm_nonce,
        )
        await session.commit()

    async with novel_db() as session:
        row = await session.scalar(select(DecisionRequestModel))
        with pytest.raises(Conflict):
            await works.resolve_decision(
                session,
                owner_id=owner,
                work_id=work.id,
                decision_id=decision_id,
                option_id="refuse",
                accept_default=True,
                confirm_nonce=row.confirm_nonce,
            )
        assert row.resolved_option_id == "trust", "后到的一方覆盖了先落定的结果"


async def test_expired_decision_falls_back_to_the_default(novel_db, monkeypatch):
    """到期无人选择：按默认项落定，且同样递增输入版本。"""
    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)
    async with novel_db() as session:
        owner, work, run = await _work_with_run(session)
        await works.create_decision(
            session,
            owner_id=owner,
            work_id=work.id,
            chapter_no=1,
            run_id=run.id,
            trigger_summary="要不要交出钥匙",
            why_human="影响后续走向",
            options=OPTIONS,
            default_option_id="refuse",
            deadline=datetime.now(UTC) - timedelta(seconds=1),
        )
        await session.commit()

    async with novel_db() as session:
        resolved = await works.sweep_expired_decisions(session)
        await session.commit()
        assert len(resolved) == 1

    async with novel_db() as session:
        row = await session.scalar(select(DecisionRequestModel))
        assert row.resolved_option_id == "refuse"
        assert row.resolved_by == "timer"
        run = await session.scalar(select(ChapterRunModel))
        assert run.input_version == 2
        assert run.state == ChapterRunState.RUNNING.value

    # 再扫一次不应重复落定
    async with novel_db() as session:
        assert await works.sweep_expired_decisions(session) == []


async def test_pending_deadline_is_not_swept_early(novel_db, monkeypatch):
    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)
    async with novel_db() as session:
        owner, work, run = await _work_with_run(session)
        await works.create_decision(
            session,
            owner_id=owner,
            work_id=work.id,
            chapter_no=1,
            run_id=run.id,
            trigger_summary="还没到期",
            why_human="等待用户",
            options=OPTIONS,
            default_option_id="refuse",
            deadline=datetime.now(UTC) + timedelta(hours=1),
        )
        await session.commit()

    async with novel_db() as session:
        assert await works.sweep_expired_decisions(session) == []
        row = await session.scalar(select(DecisionRequestModel))
        assert row.state == DecisionState.PENDING.value
