"""一次性补丁：works 层裁决创建、落定回写与到期默认（P1-2）。"""
from __future__ import annotations

import pathlib

path = pathlib.Path("core/src/regent/novel/application/works.py")
text = path.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"count={text.count(old)} for {old[:70]!r}"
    text = text.replace(old, new)


# 1) create_decision：导演创建持久裁决请求
sub(
    """async def resolve_decision(""",
    '''async def create_decision(
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
    """导演发起裁决：落持久化请求，章节与作品进入等待（P1-2 / G-13）。

    裁决不是聊天消息：它必须能被收件箱列出、能被深链打开、能在无人选择时
    按默认项到期落定。选项为空或默认项不在选项里，一律拒绝创建。
    """
    work = await _get_owned_work(session, work_id=work_id, owner_id=owner_id)
    if not options:
        raise ValidationFailed("decision requires at least one option")
    ids = {str(option.get("option_id", "")) for option in options}
    if default_option_id not in ids:
        raise ValidationFailed("default option must be one of the options")

    row = DecisionRequestModel(
        id=uuid.uuid4(),
        work_id=work_id,
        run_id=run_id,
        node_id=node_id or "",
        chapter_no=chapter_no,
        state=DecisionState.PENDING.value,
        trigger_summary=trigger_summary,
        why_human=why_human,
        options=[dict(option) for option in options],
        default_option_id=default_option_id,
        deadline=deadline,
        impact_level=impact_level,
        impact_horizon_chapters=impact_horizon_chapters,
        # 提交必须带回 nonce：预览不消费，重复提交不生效
        confirm_nonce=hashlib.sha256(
            f"{work_id}:{chapter_no}:{uuid.uuid4()}".encode()
        ).hexdigest()[:32],
    )
    session.add(row)
    await session.flush()

    if work.state == StoryWorkState.RUNNING.value:
        assert_story_work_transition(work.state, StoryWorkState.PENDING_DECISION.value)
        work.state = StoryWorkState.PENDING_DECISION.value
        work.version += 1
    if run_id is not None:
        run = await session.get(ChapterRunModel, run_id)
        if run is not None and run.state in (
            ChapterRunState.RUNNING.value,
            ChapterRunState.AWAITING_INPUT.value,
        ):
            assert_chapter_run_transition(
                run.state, ChapterRunState.PENDING_DECISION.value
            )
            run.state = ChapterRunState.PENDING_DECISION.value
            run.version += 1
    await session.flush()

    await append_event(
        session,
        work_id=work_id,
        event_type="decision.created",
        data={
            "decision_id": str(row.id),
            "chapter_no": chapter_no,
            "trigger_summary": trigger_summary,
            "default_option_id": default_option_id,
            "deadline": deadline.isoformat() if deadline else None,
        },
        branch_id=work.branch_id,
        chapter_no=chapter_no,
        decision_id=str(row.id),
    )
    return _decision_view(row)


async def _apply_decision(
    session: AsyncSession,
    *,
    row: DecisionRequestModel,
    chosen: str,
    resolved_by: str,
) -> None:
    """裁决生效：结果回写到章节，递增输入版本并恢复推进（P1-2）。

    用户的选择必须真的改变后续生成——只改作品状态等于没选：新的
    input_version 让旧方向的调用键失效，导演下一轮才看得到这次选择。
    """
    from sqlalchemy import text as sa_text

    result = await session.execute(
        sa_text(
            "UPDATE novel_decision_requests "
            "SET state = 'RESOLVED', resolved_by = :by, resolved_option_id = :opt, "
            "    resolved_at = NOW(), version = version + 1 "
            "WHERE id = :id AND state = 'PENDING'"
        ),
        {"by": resolved_by[:32], "opt": chosen, "id": row.id},
    )
    if result.rowcount != 1:
        raise Conflict("decision was resolved concurrently", current_version=int(row.version))
    await session.refresh(row)

    work = await session.get(StoryWorkModel, row.work_id)
    if work is not None and work.state == StoryWorkState.PENDING_DECISION.value:
        assert_story_work_transition(work.state, StoryWorkState.RUNNING.value)
        work.state = StoryWorkState.RUNNING.value
        work.version += 1

    run: ChapterRunModel | None = None
    if row.run_id is not None:
        run = await session.get(ChapterRunModel, row.run_id)
    if run is None and row.chapter_no is not None and work is not None:
        run = await session.scalar(
            select(ChapterRunModel)
            .where(
                ChapterRunModel.work_id == row.work_id,
                ChapterRunModel.branch_id == work.branch_id,
                ChapterRunModel.chapter_no == row.chapter_no,
            )
            .order_by(ChapterRunModel.attempt.desc())
            .limit(1)
        )
    if run is not None:
        if run.state == ChapterRunState.PENDING_DECISION.value:
            assert_chapter_run_transition(run.state, ChapterRunState.RUNNING.value)
            run.state = ChapterRunState.RUNNING.value
        # 选择进入生成上下文：导演下一轮必须看得到，而不是只在通知里出现
        context = dict(run.generation_context or {})
        resolutions = list(context.get("decision_resolutions") or [])
        resolutions.append(
            {
                "decision_id": str(row.id),
                "option_id": chosen,
                "resolved_by": resolved_by,
                "chapter_no": row.chapter_no,
            }
        )
        context["decision_resolutions"] = resolutions
        run.generation_context = context
        await bump_input_version(session, run=run, reason="decision_resolved")
    await session.flush()

    if work is not None:
        await append_event(
            session,
            work_id=row.work_id,
            event_type="decision.resolved",
            data={
                "decision_id": str(row.id),
                "option_id": chosen,
                "resolved_by": resolved_by,
            },
            branch_id=work.branch_id,
            chapter_no=row.chapter_no,
            decision_id=str(row.id),
        )


async def sweep_expired_decisions(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 20
) -> list[str]:
    """到期未选择的裁决按默认项落定：与用户提交竞争，只有一个能赢（P1-2）。"""
    moment = now or datetime.now(UTC)
    rows = (
        await session.scalars(
            select(DecisionRequestModel)
            .where(
                DecisionRequestModel.state == DecisionState.PENDING.value,
                DecisionRequestModel.deadline.is_not(None),
                DecisionRequestModel.deadline <= moment,
                DecisionRequestModel.default_option_id != "",
            )
            .limit(limit)
        )
    ).all()
    resolved: list[str] = []
    for row in rows:
        try:
            await _apply_decision(
                session, row=row, chosen=row.default_option_id, resolved_by="timer"
            )
        except Conflict:  # 已被用户抢先落定
            continue
        resolved.append(str(row.id))
    return resolved


async def resolve_decision(''',
)

# 2) resolve_decision 改为复用 _apply_decision
sub(
    '''    # 条件更新：只有仍为 PENDING 的一方能胜出
    result = await session.execute(
        sa_text(
            "UPDATE novel_decision_requests "
            "SET state = 'RESOLVED', resolved_by = :by, resolved_option_id = :opt, "
            "    resolved_at = NOW(), version = version + 1 "
            "WHERE id = :id AND state = 'PENDING'"
        ),
        {"by": resolved_by, "opt": chosen, "id": decision_id},
    )
    if result.rowcount != 1:
        raise Conflict("decision was resolved concurrently", current_version=int(row.version))
    await session.refresh(row)

    if work.state == StoryWorkState.PENDING_DECISION.value:
        assert_story_work_transition(work.state, StoryWorkState.RUNNING.value)
        work.state = StoryWorkState.RUNNING.value
        work.version += 1
    await session.flush()

    await append_event(
        session,
        work_id=work_id,
        event_type="decision.resolved",
        data={"decision_id": str(decision_id), "option_id": chosen, "resolved_by": resolved_by},
        branch_id=work.branch_id,
        chapter_no=row.chapter_no,
        decision_id=str(decision_id),
    )
    return _decision_view(row)''',
    '''    # 条件更新：只有仍为 PENDING 的一方能胜出；结果回写章节并递增输入版本
    await _apply_decision(session, row=row, chosen=chosen, resolved_by=resolved_by)
    return _decision_view(row)''',
)

# 3) 导出
sub(
    '    "create_share",\n',
    '    "create_decision",\n    "create_share",\n',
)
sub(
    '    "soft_delete_work",\n',
    '    "soft_delete_work",\n    "sweep_expired_decisions",\n',
)

path.write_text(text, encoding="utf-8")
print("patched", path)
