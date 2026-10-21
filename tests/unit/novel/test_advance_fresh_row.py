"""加锁推进必须读到库里的最新进度，而不是会话里早先装进来的旧对象。

真机第 8 章：worker B 的候选查询把运行装进会话时，worker A 的 CHECK 还没提交；
A 提交 DONE 后 B 锁到这一行，手里却还是 CHECK 的 generation_context，REVIEW
判「未完成的短链章节不能审校」，再把旧进度连同错误整份写回，盖掉了 A 的 DONE。
"""

from __future__ import annotations

import uuid

import pytest
from regent.novel.application import works
from regent.novel.application.works_advance import advance_step
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    CriticalNodeModel,
    CriticalPathModel,
    NovelPrincipalModel,
    PersonaSpecModel,
    StoryGoalModel,
    StoryWorkModel,
    VolumeModel,
)
from sqlalchemy import select, update


async def _noop_event(session, **kwargs):  # noqa: ANN001, ANN201
    return None


async def _seed(factory) -> None:  # noqa: ANN001
    owner = uuid.uuid4()
    work_id = uuid.uuid4()
    async with factory() as session:
        session.add(NovelPrincipalModel(id=owner, subject=f"fresh:{owner}"))
        session.add(StoryWorkModel(id=work_id, owner_id=owner, state="READY", genre="悬疑"))
        session.add(PersonaSpecModel(id=uuid.uuid4(), work_id=work_id, name="主角"))
        session.add(StoryGoalModel(id=uuid.uuid4(), work_id=work_id, raw_intent="信任的代价"))
        session.add(
            VolumeModel(
                id=uuid.uuid4(),
                work_id=work_id,
                volume_no=1,
                title="第一卷",
                start_chapter_no=1,
                end_chapter_no=3,
                state="ACTIVE",
            )
        )
        path_id = uuid.uuid4()
        session.add(CriticalPathModel(id=path_id, work_id=work_id, node_count=3))
        for ordinal in range(1, 4):
            session.add(
                CriticalNodeModel(
                    id=uuid.uuid4(),
                    path_id=path_id,
                    node_id=f"n{ordinal}",
                    ordinal=ordinal,
                    title=f"节点{ordinal}",
                    volume_no=1,
                )
            )
        await session.commit()
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()


@pytest.mark.asyncio
async def test_locked_run_is_reloaded_not_taken_from_the_session(novel_db, monkeypatch):
    monkeypatch.setattr(works, "append_event", _noop_event)
    await _seed(novel_db)
    seen: list[str | None] = []

    async def _execute(session, *, provider, work, run, step):  # noqa: ANN001, ANN202
        seen.append((run.generation_context or {}).get("marker"))
        return True

    async with novel_db() as session:
        # 候选查询：此刻的进度装进会话
        run = (await session.scalars(select(ChapterRunModel))).one()
        assert "marker" not in (run.generation_context or {})
        # 别的 worker 随后提交了新进度：不经这个会话，内存对象看不见
        await session.execute(
            update(ChapterRunModel)
            .where(ChapterRunModel.id == run.id)
            .values(generation_context={**(run.generation_context or {}), "marker": "fresh"})
            .execution_options(synchronize_session=False)
        )
        work = await session.get(StoryWorkModel, run.work_id)
        await advance_step(
            session,
            provider=None,
            owner_id=work.owner_id,
            work_id=work.id,
            chapter_no=int(run.chapter_no),
            run_id=run.id,
            execute_step_fn=_execute,
            append_event_fn=_noop_event,
        )

    assert seen == ["fresh"]
