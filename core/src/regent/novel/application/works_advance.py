"""Chapter checkpoint advancement."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.application import executor as executor_app
from regent.novel.application.directing_protocol import is_directed
from regent.novel.application.events import append_event
from regent.novel.application.generation import execute_step
from regent.novel.application.production import acquire_run_lease, lease_is_valid, release_run_lease
from regent.novel.application.works_access import get_owned_work as _get_owned_work
from regent.novel.application.works_constants import (
    CHECKPOINT_STEPS,
)
from regent.novel.application.works_constants import (
    LEASE_OWNER as _LEASE_OWNER,
)
from regent.novel.application.works_constants import (
    RETRY_BACKOFF as _RETRY_BACKOFF,
)
from regent.novel.application.works_run_control import get_run_progress
from regent.novel.application.works_volumes import _after_chapter_completed
from regent.novel.domain.errors import BudgetExhausted, InvalidState, NotFound, ProductionStopped
from regent.novel.domain.models import RunProgressOut, StepState
from regent.novel.domain.states import (
    CHAPTER_STEP_ORDER,
    ChapterRunState,
    ChapterStep,
    StoryWorkState,
    assert_chapter_run_transition,
    assert_story_work_transition,
    chapter_step_order,
)
from regent.novel.infrastructure.models import ChapterRunModel, ChapterStepModel

logger = logging.getLogger(__name__)


def _rewind_review_to_weave(*, run: ChapterRunModel, by_name: dict[str, ChapterStepModel]) -> None:
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
    run_id: uuid.UUID | None = None,
    execute_step_fn: Any = None,
    append_event_fn: Any = None,
) -> RunProgressOut:
    """推进一个可恢复的 Agent-loop 步骤。

    幂等键 ``work:branch:chapter:step:input_version``——重复调用不产生副作用。
    后台领取应传入 ``run_id``，避免历史 attempt 与领取对象不一致。
    """
    execute = execute_step_fn or execute_step
    emit = append_event_fn or append_event
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if run_id is not None:
        run = await session.scalar(
            select(ChapterRunModel)
            .where(
                ChapterRunModel.id == run_id,
                ChapterRunModel.work_id == work_id,
                ChapterRunModel.branch_id == work.branch_id,
            )
            .with_for_update()
        )
    else:
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
    if run is None:
        raise NotFound("chapter run not found")
    if int(run.chapter_no) != int(chapter_no):
        raise InvalidState(
            "run_id chapter mismatch",
            current=f"run.chapter={run.chapter_no} requested={chapter_no}",
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
            await emit(
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
    if work.state != StoryWorkState.RUNNING.value or run.state in {
        ChapterRunState.CANONIZED.value,
        ChapterRunState.CANCELLED.value,
        ChapterRunState.SUPERSEDED.value,
        ChapterRunState.PENDING_DECISION.value,
        ChapterRunState.TERMINAL_FAILED.value,
        ChapterRunState.AWAITING_INPUT.value,
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
        await session.scalars(select(ChapterStepModel).where(ChapterStepModel.run_id == run.id))
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
        await emit(
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
    await emit(
        session,
        work_id=work_id,
        event_type="chapter.step_started",
        data={"chapter_no": chapter_no, "step": pending.step},
        branch_id=work.branch_id,
        chapter_no=chapter_no,
    )
    try:
        complete = await execute(session, provider=provider, work=work, run=run, step=step)
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
        # 失败要出声：只写表不写日志，等于让故障自己安静地过去。
        logger.warning(
            "chapter step failed: work=%s chapter=%s step=%s attempt=%s code=%s: %s",
            work_id,
            chapter_no,
            pending.step,
            pending.attempt,
            pending.error_code,
            str(exc)[:500],
        )
        # BQ-1：额度耗尽 → 作品可恢复暂停；章 run 保持 RUNNING，step 可重入 PENDING。
        if isinstance(exc, BudgetExhausted):
            pending.state = StepState.PENDING.value
            pending.attempt = max(0, int(pending.attempt or 0) - 1)
            pending.error_code = ""
            pause_state = (
                StoryWorkState.PAUSED_QUOTA.value
                if exc.kind == "calls"
                else StoryWorkState.PAUSED_COST.value
            )
            if work.state == StoryWorkState.RUNNING.value:
                assert_story_work_transition(work.state, pause_state)
                work.state = pause_state
                work.version += 1
            context = dict(run.generation_context or {})
            production = context.get("production") or {}
            context["budget_pause"] = {
                "kind": exc.kind,
                "call_count": int(production.get("call_count", 0) or 0),
                "committed_minor": int(production.get("committed_minor", 0) or 0),
                "phase": production.get("phase"),
                "scene_index": production.get("scene_index"),
                "message": str(exc),
            }
            run.generation_context = context
            # 明确保持 RUNNING：勿 TERMINAL_FAILED / RETRYABLE_FAILED（避免空转撞墙）。
            if run.state != ChapterRunState.RUNNING.value:
                assert_chapter_run_transition(run.state, ChapterRunState.RUNNING.value)
                run.state = ChapterRunState.RUNNING.value
            run.version += 1
            await session.flush()
            await emit(
                session,
                work_id=work_id,
                event_type="work.budget_paused",
                data={
                    "kind": exc.kind,
                    "work_state": work.state,
                    "chapter_no": chapter_no,
                    "step": pending.step,
                    "message": str(exc),
                    "call_count": context["budget_pause"]["call_count"],
                    "committed_minor": context["budget_pause"]["committed_minor"],
                    "phase": context["budget_pause"]["phase"],
                    "scene_index": context["budget_pause"]["scene_index"],
                },
                branch_id=work.branch_id,
                chapter_no=chapter_no,
            )
            await release_run_lease(session, run=run, owner=_LEASE_OWNER)
            return await get_run_progress(session, owner_id=owner_id, work_id=work_id)
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
        await emit(
            session,
            work_id=work_id,
            event_type="chapter.step_failed",
            data={"chapter_no": chapter_no, "step": pending.step, "error": pending.error_code},
            branch_id=work.branch_id,
            chapter_no=chapter_no,
        )
        if run.state == ChapterRunState.TERMINAL_FAILED.value:
            await release_run_lease(session, run=run, owner=_LEASE_OWNER)
        else:
            # 可重试的失败按住一会儿再放回场上；终态才彻底释放。
            await acquire_run_lease(session, run=run, owner=_LEASE_OWNER, ttl=_RETRY_BACKOFF)
        return await get_run_progress(session, owner_id=owner_id, work_id=work_id)

    # 调用窗口已结束：先复核租约与输入版本，再写回结果（P0-4）。
    # 期间可能已有新 worker 接管，或用户改意递增了 input_version——
    # 这两种情况下本次产出属于旧方向，必须作废而不是覆盖。
    stale = await _stale_reason()
    if stale is not None:
        if stale == "input_version_changed":
            await release_run_lease(session, run=run, owner=_LEASE_OWNER)
        await emit(
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
        await emit(
            session,
            work_id=work_id,
            event_type="chapter.scene_progress",
            data={
                "chapter_no": chapter_no,
                "scene_no": production.get("scene_index", 0) + 1,
                "completed_scenes": len(production.get("accepted", [])),
                "summary": "导演正在指导场景创作",
            },
            branch_id=work.branch_id,
            chapter_no=chapter_no,
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
        _step_output_data["summary"] = f"目标节点：{tn.get('title', '无')}" + (
            f" | 卷：{vol.get('title', '')}" if vol.get("title") else ""
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
            {
                "persona": ca.get("persona", ""),
                "scene_actions": ca.get("scene_actions", [])[:2],
                "emotional_arc": ca.get("emotional_arc", ""),
            }
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
    await emit(
        session,
        work_id=work_id,
        event_type="chapter.step_output",
        data=_step_output_data,
        branch_id=work.branch_id,
        chapter_no=chapter_no,
    )
    await emit(
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
        await emit(
            session,
            work_id=work_id,
            event_type="chapter.done",
            data={"chapter_no": chapter_no},
            branch_id=work.branch_id,
            chapter_no=chapter_no,
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
        await emit(
            session,
            work_id=work_id,
            event_type="chapter.checkpoint_reached",
            data={"chapter_no": chapter_no, "step": step.value, "auto_advance": False},
            branch_id=work.branch_id,
            chapter_no=chapter_no,
        )
    return await get_run_progress(session, owner_id=owner_id, work_id=work_id)
