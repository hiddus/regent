"""Chapter run control services."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application import executor as executor_app
from regent.novel.application.events import append_event
from regent.novel.application.works_access import get_owned_work
from regent.novel.application.works_input import bump_input_version
from regent.novel.application.works_projection import projection_for as _projection_for
from regent.novel.application.works_volumes import _last_node_completed
from regent.novel.domain import ending
from regent.novel.domain.errors import Conflict, InvalidState, NotFound, ValidationFailed
from regent.novel.domain.models import (
    AutoAdvanceRequest,
    GuidanceRequest,
    RunProgressOut,
    StepState,
    WorkStateOut,
)
from regent.novel.domain.states import (
    ChapterRunState,
    ChapterStep,
    StoryWorkState,
    assert_chapter_run_transition,
    assert_story_work_transition,
    chapter_step_order,
)
from regent.novel.infrastructure.models import ChapterRunModel, ChapterStepModel, StoryWorkModel

_get_owned_work = get_owned_work


async def _latest_run(session: AsyncSession, *, work: StoryWorkModel) -> ChapterRunModel | None:
    return await session.scalar(
        select(ChapterRunModel)
        .where(
            ChapterRunModel.work_id == work.id,
            ChapterRunModel.branch_id == work.branch_id,
        )
        .order_by(ChapterRunModel.chapter_no.desc(), ChapterRunModel.attempt.desc())
        .limit(1)
    )


async def _progress_for_run(
    session: AsyncSession, *, run: ChapterRunModel, work: StoryWorkModel | None = None
) -> RunProgressOut:
    steps = await session.scalars(select(ChapterStepModel).where(ChapterStepModel.run_id == run.id))
    if work is None:
        work = await session.get(StoryWorkModel, run.work_id)
    context = run.generation_context or {}
    production = context.get("production", {})
    scene_count = len(production.get("plan", {}).get("scenes", []))
    work_state = str(work.state) if work is not None else ""
    budget_pause = context.get("budget_pause")
    if not isinstance(budget_pause, dict):
        budget_pause = None
    projection = _projection_for(work_state, chapter_no=int(run.chapter_no)) if work_state else None
    return RunProgressOut(
        work_id=str(run.work_id),
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
        work_state=work_state,
        budget_pause=budget_pause,
        public_stage=projection.public_stage if projection else None,
        available_actions=list(projection.available_actions) if projection else [],
    )


async def get_active_run_progress(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> RunProgressOut | None:
    """GET 入口专用：**没有章运行时返回 None**。

    旧实现在这种情形下编一个 ``state=QUEUED`` 的进度对象返回，于是「一章都没开」
    和「第 N 章正在生成」在前端长得一模一样：作品停在 READY、前端永远显示
    「实时生成中」，而唯一能开工的按钮（挂在 ``!progress`` 上）永远不会出现。
    真实故障（如 POST /runs 报错）被这个假进度吞掉，用户既看不到错也走不出去。
    """
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    run = await _latest_run(session, work=work)
    if run is None:
        return None
    return await _progress_for_run(session, run=run, work=work)


async def get_run_progress(
    session: AsyncSession, *, owner_id: uuid.UUID, work_id: uuid.UUID
) -> RunProgressOut:
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    run = await _latest_run(session, work=work)
    if run is None:
        return RunProgressOut(
            work_id=str(work_id),
            chapter_no=int(work.latest_chapter_no),
            state=ChapterRunState.QUEUED,
            work_state=str(work.state),
        )
    return await _progress_for_run(session, run=run, work=work)


from regent.novel.application.works_world_bible import ensure_legacy_story_bible


async def start_run(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    provider: Any | None = None,
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
        ChapterRunState.QUEUED.value,
        ChapterRunState.RUNNING.value,
        ChapterRunState.RETRYABLE_FAILED.value,
        ChapterRunState.AWAITING_INPUT.value,
        ChapterRunState.PENDING_DECISION.value,
        ChapterRunState.TERMINAL_FAILED.value,
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
            conflict_summary={
                "reason": "story_complete",
                "latest_chapter_no": int(work.latest_chapter_no),
            },
        )
    if work.state == StoryWorkState.ONBOARDING.value:
        raise InvalidState(
            "请先确认故事世界设定后再开始创作",
            current=work.state,
        )
    if not work.story_bible_locked_at:
        # 遗留作品：允许补齐并自动锁定
        await ensure_legacy_story_bible(session, work=work, provider=provider)
        if not work.story_bible_locked_at:
            raise InvalidState(
                "请先确认故事世界设定后再开始创作",
                current=work.state,
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
            "executor_version": executor_app.executor_version(executor),
            "call_key_version": 2,
            "production": {"call_key_version": 2},
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
) -> WorkResumeOut:
    """按失败原因路由恢复；禁止只切 work.state 假装成功。"""
    from regent.novel.application.works_continuation import (
        ensure_next_run,
        get_continuation_policy,
    )
    from regent.novel.domain.models import WorkResumeOut

    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if work.state not in (
        StoryWorkState.PAUSED_COST.value,
        StoryWorkState.PAUSED_QUOTA.value,
        StoryWorkState.FAILED.value,
        StoryWorkState.READY.value,
        StoryWorkState.RUNNING.value,
    ):
        raise InvalidState("work cannot be resumed from current state", current=work.state)
    # BQ-1：仍挂着 budget_pause 时禁止裸 resume，必须走 authorize_budget。
    if work.state in (
        StoryWorkState.PAUSED_QUOTA.value,
        StoryWorkState.PAUSED_COST.value,
    ):
        run = await _latest_run(session, work=work)
        pause = (run.generation_context or {}).get("budget_pause") if run is not None else None
        if isinstance(pause, dict):
            raise InvalidState(
                "budget pause requires authorize_budget before resume",
                current=work.state,
            )

    run = await _latest_run(session, work=work)

    def _out(
        state: str,
        *,
        blocker_code: str = "",
        recoverability: str = "",
        failed_phase: str = "",
        actions: list[str] | None = None,
        detail: str = "",
        run_state: str = "",
        chapter_no: int | None = None,
    ) -> WorkResumeOut:
        return WorkResumeOut(
            state=WorkStateOut(state),
            blocker_code=blocker_code,
            recoverability=recoverability,
            failed_phase=failed_phase,
            recommended_actions=list(actions or []),
            detail=detail,
            run_state=run_state,
            chapter_no=chapter_no,
        )

    if run is not None:
        rstate = str(run.state or "")
        rch = int(run.chapter_no)
        prod = (run.generation_context or {}).get("production") or {}
        phase = str(prod.get("phase") or run.current_step or "")

        if rstate == ChapterRunState.TERMINAL_FAILED.value:
            # 不自动重开 attempt；不把 work 伪置 RUNNING
            code = "content_hard_fail" if phase in ("VALIDATE", "REVIEW", "ACCEPT_CHAPTER") else "terminal_failed"
            actions = ["regenerate_chapter", "inspect_evidence", "repair_from_checkpoint"]
            await append_event(
                session,
                work_id=work_id,
                event_type="work.resume_blocked",
                data={
                    "blocker_code": code,
                    "chapter_no": rch,
                    "run_state": rstate,
                    "failed_phase": phase,
                },
                branch_id=work.branch_id,
                chapter_no=rch,
            )
            return _out(
                work.state,
                blocker_code=code,
                recoverability="user_action_required",
                failed_phase=phase,
                actions=actions,
                detail=f"第 {rch} 章终止失败，须显式 regenerate 或修复后恢复",
                run_state=rstate,
                chapter_no=rch,
            )

        if rstate == ChapterRunState.PENDING_DECISION.value:
            return _out(
                work.state,
                blocker_code="pending_decision",
                recoverability="user_decision_required",
                failed_phase=phase,
                actions=["open_decision", "resolve_ending"],
                detail="章任务等待裁决，不能空转恢复",
                run_state=rstate,
                chapter_no=rch,
            )

        if rstate == ChapterRunState.AWAITING_INPUT.value:
            return _out(
                work.state,
                blocker_code="awaiting_input",
                recoverability="user_input_required",
                failed_phase=phase,
                actions=["provide_input"],
                detail="章任务等待用户输入",
                run_state=rstate,
                chapter_no=rch,
            )

        if rstate == ChapterRunState.RETRYABLE_FAILED.value:
            # 可恢复：清租约后 worker 可再领
            run.lease_expires_at = None
            if run.state == ChapterRunState.RETRYABLE_FAILED.value:
                # 保持 RETRYABLE_FAILED，worker 领取范围已包含
                pass
            session.add(run)
            if work.state != StoryWorkState.RUNNING.value:
                assert_story_work_transition(work.state, StoryWorkState.RUNNING.value)
                work.state = StoryWorkState.RUNNING.value
            work.version += 1
            await session.flush()
            await append_event(
                session,
                work_id=work_id,
                event_type="work.resumed",
                data={"state": work.state, "reason": "retryable_failed", "chapter_no": rch},
                branch_id=work.branch_id,
            )
            return _out(
                work.state,
                blocker_code="",
                recoverability="auto",
                actions=["wait_worker"],
                detail="暂时故障已重新入队",
                run_state=rstate,
                chapter_no=rch,
            )

        if rstate == ChapterRunState.CANONIZED.value:
            policy = get_continuation_policy(work)
            next_run = await ensure_next_run(session, work=work, completed_run=run)
            if next_run is not None:
                if work.state != StoryWorkState.RUNNING.value:
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
                        "reason": "continuation",
                        "chapter_no": int(next_run.chapter_no),
                    },
                    branch_id=work.branch_id,
                )
                return _out(
                    work.state,
                    actions=["wait_worker"],
                    detail="已授权连续创作，后继章已入队",
                    run_state=str(next_run.state),
                    chapter_no=int(next_run.chapter_no),
                )
            if policy.enabled:
                return _out(
                    work.state,
                    blocker_code="continuation_blocked",
                    recoverability="system_or_user",
                    actions=["inspect_prior_chapter", "start_run"],
                    detail="连续创作已授权但无法开后继（前章在途/未锁定/目标已到）",
                    run_state=rstate,
                    chapter_no=rch,
                )
            # 无连续授权：保持 FAILED/READY，不伪置 RUNNING；用户 start_run 开下一章
            return _out(
                work.state,
                blocker_code="continuation_not_authorized",
                recoverability="user_action_required",
                actions=["start_run"],
                detail="上一章已接受；未授权自动连续，请手动开始下一章",
                run_state=rstate,
                chapter_no=rch,
            )

        if rstate in (
            ChapterRunState.QUEUED.value,
            ChapterRunState.RUNNING.value,
        ):
            if work.state != StoryWorkState.RUNNING.value:
                assert_story_work_transition(work.state, StoryWorkState.RUNNING.value)
                work.state = StoryWorkState.RUNNING.value
            work.version += 1
            await session.flush()
            await append_event(
                session,
                work_id=work_id,
                event_type="work.resumed",
                data={"state": work.state, "reason": "in_flight", "chapter_no": rch},
                branch_id=work.branch_id,
            )
            return _out(
                work.state,
                actions=["wait_worker"],
                detail="存在在途章任务，后台继续推进",
                run_state=rstate,
                chapter_no=rch,
            )

    # 无章运行：READY/FAILED 空作品
    if work.state != StoryWorkState.RUNNING.value:
        assert_story_work_transition(work.state, StoryWorkState.RUNNING.value)
        work.state = StoryWorkState.RUNNING.value
    work.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="work.resumed",
        data={"state": work.state, "reason": "no_active_run"},
        branch_id=work.branch_id,
    )
    return _out(
        work.state,
        blocker_code="no_active_run",
        recoverability="user_action_required",
        actions=["start_run"],
        detail="恢复完成但尚无章任务，请开始第一章",
        run_state="",
    )


async def authorize_budget(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    work_id: uuid.UUID,
    grant_calls: int = 0,
    grant_cost_minor: int = 0,
    client_nonce: str = "",
) -> WorkStateOut:
    """预算暂停后授权追加额度并恢复 RUNNING（BQ-1）。

    只累加 ``budget_grant_*``，不清零 ``call_count`` / ``committed_minor``。
    """
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if work.state not in (
        StoryWorkState.PAUSED_QUOTA.value,
        StoryWorkState.PAUSED_COST.value,
    ):
        raise InvalidState(
            "budget authorize requires PAUSED_QUOTA or PAUSED_COST",
            current=work.state,
        )
    calls = max(0, int(grant_calls or 0))
    cost = max(0, int(grant_cost_minor or 0))
    if calls <= 0 and cost <= 0:
        raise ValidationFailed("grant_calls or grant_cost_minor must be positive")
    run = await _latest_run(session, work=work)
    if run is None:
        raise NotFound("chapter run not found")
    context = dict(run.generation_context or {})
    production = dict(context.get("production") or {})
    production["budget_grant_calls"] = int(production.get("budget_grant_calls", 0) or 0) + calls
    production["budget_grant_cost_minor"] = (
        int(production.get("budget_grant_cost_minor", 0) or 0) + cost
    )
    context["production"] = production
    context.pop("budget_pause", None)
    run.generation_context = context
    run.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="work.budget_authorized",
        data={
            "grant_calls": calls,
            "grant_cost_minor": cost,
            "budget_grant_calls": production["budget_grant_calls"],
            "budget_grant_cost_minor": production["budget_grant_cost_minor"],
            "client_nonce": client_nonce or "",
            "call_count": int(production.get("call_count", 0) or 0),
            "committed_minor": int(production.get("committed_minor", 0) or 0),
        },
        branch_id=work.branch_id,
        chapter_no=int(run.chapter_no),
    )
    return await resume_work(session, owner_id=owner_id, work_id=work_id)


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
