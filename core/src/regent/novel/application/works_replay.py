"""Chapter replay services."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application import executor as executor_app
from regent.novel.application import memory as memory_app
from regent.novel.application.events import append_event
from regent.novel.application.works_access import get_owned_work as _get_owned_work
from regent.novel.application.works_run_control import (
    get_run_progress,
)
from regent.novel.domain.errors import (
    NotFound,
)
from regent.novel.domain.models import (
    ReportFactRequest,
    ReportFactResponse,
    RunProgressOut,
    StepState,
)
from regent.novel.domain.states import (
    ChapterRunState,
    ChapterStep,
    StoryWorkState,
    chapter_step_order,
)
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    ChapterStepModel,
    StoryWorkModel,
)


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
        select(func.max(ChapterRunModel.attempt)).where(
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
    # B-04：重演也是一次**新的运行**——执行器按作品分桶重新确定（桶对同一
    # 作品是确定的，因此与首跑同臂；灰度旋钮回退后的重演落回 stable 属于
    # 「切换推迟到下一次运行」的既定语义）。不再硬编码 director_v2，否则
    # legacy 臂作品的纠错重演会静默换架构，盲评无法归因。
    executor = executor_app.choose_executor(work.id)
    context: dict = {
        "architecture_version": executor,
        "executor": executor,
        "executor_version": executor_app.executor_version(executor),
    }
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
        if await _queue_replay_run(session, work=work, chapter_no=c, correction=correction)
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
