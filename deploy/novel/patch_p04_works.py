"""一次性补丁：works.py 接入租约复核、输入版本失效与改意递增。"""
from __future__ import annotations

import pathlib

path = pathlib.Path("core/src/regent/novel/application/works.py")
text = path.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"count={text.count(old)} for {old[:70]!r}"
    text = text.replace(old, new)


# --- 1) 导入 ---
sub(
    """    acquire_run_lease, release_run_lease,""",
    """    acquire_run_lease,
    lease_is_valid,
    release_run_lease,""",
)

# --- 2) advance_step：领取租约时记住 fencing token 与输入版本 ---
sub(
    """    # 领取运行租约：模型调用在事务外进行，期间其他 worker 不得并行推进（§4.4）
    await acquire_run_lease(session, run=run, owner=_LEASE_OWNER)
""",
    """    # 领取运行租约：模型调用在事务外进行，期间其他 worker 不得并行推进（§4.4）
    fencing_token = await acquire_run_lease(session, run=run, owner=_LEASE_OWNER)
    # 输入版本快照：调用窗口内用户改意后，本次结果不得写回（P0-4）
    input_version_before = int(run.input_version or 1)

    def _intact() -> bool:
        return lease_is_valid(
            run, owner=_LEASE_OWNER, token=fencing_token
        ) and int(run.input_version or 1) == input_version_before
""",
)

# --- 3) 失败分支写入前复核 ---
sub(
    """    except Exception as exc:
        pending.state = StepState.FAILED.value""",
    """    except Exception as exc:
        if not _intact():
            # 租约已被接管或用户已改意：这次失败属于旧方向，不得写回（P0-4）
            return await get_run_progress(session, owner_id=owner_id, work_id=work_id)
        pending.state = StepState.FAILED.value""",
)

# --- 4) 成功分支写入前复核 ---
sub(
    """    # 调用窗口已结束：释放租约，下一个 tick 重新领取
    await release_run_lease(session, run=run, owner=_LEASE_OWNER)
""",
    """    # 调用窗口已结束：先复核租约与输入版本，再写回结果（P0-4）。
    # 期间可能已有新 worker 接管，或用户改意递增了 input_version——
    # 这两种情况下本次产出属于旧方向，必须作废而不是覆盖。
    if not _intact():
        await append_event(
            session,
            work_id=work_id,
            event_type="chapter.result_discarded",
            data={
                "chapter_no": chapter_no,
                "step": pending.step,
                "reason": "lease_lost" if not lease_is_valid(
                    run, owner=_LEASE_OWNER, token=fencing_token
                ) else "input_version_changed",
            },
            branch_id=work.branch_id,
            chapter_no=chapter_no,
        )
        return await get_run_progress(session, owner_id=owner_id, work_id=work_id)

    # 调用窗口已结束：释放租约，下一个 tick 重新领取
    await release_run_lease(session, run=run, owner=_LEASE_OWNER)
""",
)

# --- 5) bump_input_version 工具 ---
sub(
    """async def submit_guidance(""",
    '''async def bump_input_version(
    session: AsyncSession,
    *,
    run: ChapterRunModel,
    reason: str,
) -> int:
    """用户改意/路径变更：递增 input_version，使旧方向的调用与产物失效（P0-4）。

    input_version 参与命令 id 与调用逻辑键，递增后旧方向的 pending 结果在写回前
    会被 ``advance_step`` 的复核丢弃，不会覆盖新方向。返回新的版本号。
    """
    run.input_version = int(run.input_version or 1) + 1
    run.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=run.work_id,
        event_type="chapter.input_version_bumped",
        data={
            "chapter_no": run.chapter_no,
            "reason": reason,
            "input_version": run.input_version,
        },
        branch_id=run.branch_id,
        chapter_no=run.chapter_no,
    )
    return int(run.input_version)


async def submit_guidance(''',
)

# --- 6) submit_guidance 递增输入版本 ---
sub(
    """    # 状态迁移：AWAITING_INPUT -> RUNNING
    assert_chapter_run_transition(run.state, ChapterRunState.RUNNING.value)
    run.state = ChapterRunState.RUNNING.value
    run.version += 1
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="chapter.guidance_submitted",""",
    """    # 状态迁移：AWAITING_INPUT -> RUNNING
    assert_chapter_run_transition(run.state, ChapterRunState.RUNNING.value)
    run.state = ChapterRunState.RUNNING.value
    # 用户改意：旧方向的调用键失效，未写回的结果不得再覆盖（P0-4）
    await bump_input_version(session, run=run, reason="guidance")
    await session.flush()
    await append_event(
        session,
        work_id=work_id,
        event_type="chapter.guidance_submitted",""",
)

# --- 7) 关键路径变更使在途章节失效 ---
sub(
    """    work.version += 1
    if work.state in (StoryWorkState.READY.value, StoryWorkState.RUNNING.value):
        assert_story_work_transition(work.state, StoryWorkState.RECOMPUTING.value)
        work.state = StoryWorkState.RECOMPUTING.value
    await session.flush()
""",
    """    work.version += 1
    if work.state in (StoryWorkState.READY.value, StoryWorkState.RUNNING.value):
        assert_story_work_transition(work.state, StoryWorkState.RECOMPUTING.value)
        work.state = StoryWorkState.RECOMPUTING.value
    await session.flush()

    # 路径变更：在途章节的输入版本递增，旧方向产出作废（P0-4）
    _IN_FLIGHT = (
        ChapterRunState.QUEUED.value,
        ChapterRunState.RUNNING.value,
        ChapterRunState.RETRYABLE_FAILED.value,
        ChapterRunState.AWAITING_INPUT.value,
        ChapterRunState.PENDING_DECISION.value,
    )
    affected_runs = (
        await session.scalars(
            select(ChapterRunModel).where(
                ChapterRunModel.work_id == work_id,
                ChapterRunModel.branch_id == work.branch_id,
                ChapterRunModel.state.in_(_IN_FLIGHT),
            )
        )
    ).all()
    for run in affected_runs:
        await bump_input_version(session, run=run, reason="critical_path_changed")
""",
)

path.write_text(text, encoding="utf-8")
print("patched", path)
