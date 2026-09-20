"""一次性补丁：卷扩展触发末节点完成 + 整本结束（P1-3）。"""
from __future__ import annotations

import pathlib

path = pathlib.Path("core/src/regent/novel/application/works.py")
text = path.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"count={text.count(old)} for {old[:70]!r}"
    text = text.replace(old, new)


# 1) _maybe_expand_volume：末节点完成也触发展开
sub(
    """async def _maybe_expand_volume(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    provider: ModelProvider | None = None,
) -> None:
    \"\"\"检查当前卷完成度，>= 80% 时自动触发下一卷扩展。\"\"\"""",
    """async def _last_node_completed(session: AsyncSession, work: StoryWorkModel, run) -> bool:
    \"\"\"本章完成的是不是关键路径上的最后一个节点（P1-3）。\"\"\"
    context = run.generation_context or {}
    if not context.get("node_completed"):
        return False
    target_id = (context.get("target_node") or {}).get("id")
    if not target_id:
        return False
    path = await session.scalar(
        select(CriticalPathModel)
        .where(CriticalPathModel.work_id == work.id)
        .order_by(CriticalPathModel.version.desc())
        .limit(1)
    )
    if path is None:
        return False
    node_ids = list(
        (
            await session.scalars(
                select(CriticalNodeModel.node_id)
                .where(CriticalNodeModel.path_id == path.id)
                .order_by(CriticalNodeModel.ordinal)
            )
        ).all()
    )
    return bool(node_ids) and target_id == node_ids[-1]


async def _maybe_expand_volume(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    provider: ModelProvider | None = None,
    run: ChapterRunModel | None = None,
) -> None:
    \"\"\"检查当前卷完成度，>= 80% 或末节点已完成时自动触发下一卷扩展。

    末节点完成也必须触发：否则后续章节会一直重写同一个末节点（P1-3）。
    \"\"\"""",
)

sub(
    """    completed = int(work.latest_chapter_no) - int(current_vol.start_chapter_no) + 1
    if completed / total_chapters < 0.8:
        return""",
    """    completed = int(work.latest_chapter_no) - int(current_vol.start_chapter_no) + 1
    last_node_done = (
        await _last_node_completed(session, work, run) if run is not None else False
    )
    if completed / total_chapters < 0.8 and not last_node_done:
        return""",
)

# 2) 调用点传入 run
sub(
    """        await _maybe_expand_volume(session, work=work, provider=provider)""",
    """        await _maybe_expand_volume(session, work=work, provider=provider, run=run)""",
)

# 3) start_run：整本结束后不再开新章
sub(
    """    # READY/DONE 需要状态迁移；RUNNING 表示上一章完成后继续下一章，不做自迁移。
    if work.state != StoryWorkState.RUNNING.value:""",
    """    # 末节点已完成且没有可扩展的新卷：整本结束，不再开新章（P1-3）
    if latest_run is not None and (latest_run.generation_context or {}).get(
        "story_complete"
    ):
        if work.state != StoryWorkState.DONE.value:
            assert_story_work_transition(work.state, StoryWorkState.DONE.value)
            work.state = StoryWorkState.DONE.value
            work.version += 1
            await session.flush()
        raise Conflict(
            "story already complete",
            current_version=int(work.version),
            conflict_summary={"reason": "story_complete", "latest_chapter_no": int(
                work.latest_chapter_no
            )},
        )
    # READY/DONE 需要状态迁移；RUNNING 表示上一章完成后继续下一章，不做自迁移。
    if work.state != StoryWorkState.RUNNING.value:""",
)

path.write_text(text, encoding="utf-8")
print("patched", path)
