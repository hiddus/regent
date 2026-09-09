"""P1-3 按卷记忆与路径终止（Plan v6.4 §10）。

验收点：

- 事实按卷切分，不再用「最近 120 条」近似；
- 末节点完成后结束或跨卷展开，不重复生成末节点。
"""

from __future__ import annotations

import uuid

import pytest
from regent.novel.application import works
from regent.novel.application.generation import _canon_facts, tag_facts
from regent.novel.domain.errors import Conflict
from regent.novel.domain.states import ChapterRunState, StoryWorkState
from regent.novel.infrastructure.models import (
    CanonCommitModel,
    ChapterRunModel,
    CriticalNodeModel,
    CriticalPathModel,
    NovelPrincipalModel,
    StoryWorkModel,
    VolumeModel,
)
from sqlalchemy import select


async def _work(session, *, state=StoryWorkState.RUNNING.value):
    owner = uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject=f"volume-test:{owner}"))
    work = StoryWorkModel(id=uuid.uuid4(), owner_id=owner, state=state, genre="悬疑")
    session.add(work)
    await session.flush()
    return owner, work


async def _volume(session, work, *, volume_no: int, state: str, start: int, end: int):
    vol = VolumeModel(
        id=uuid.uuid4(),
        work_id=work.id,
        volume_no=volume_no,
        title=f"第{volume_no}卷",
        state=state,
        start_chapter_no=start,
        end_chapter_no=end,
        summary=[],
    )
    session.add(vol)
    await session.flush()
    return vol


async def _canon(session, work, *, chapter_no: int, facts: list[dict], version: int):
    session.add(
        CanonCommitModel(
            id=uuid.uuid4(),
            work_id=work.id,
            branch_id=work.branch_id,
            chapter_no=chapter_no,
            parent_version=version - 1,
            version=version,
            facts=facts,
            source_hash=f"hash-{chapter_no}-{version}",
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# 事实打标签
# ---------------------------------------------------------------------------


def test_tag_facts_stamps_volume_and_chapter():
    tagged = tag_facts(
        [{"statement": "钥匙在桌上", "known_by": ["主角"]}],
        volume_no=2,
        chapter_no=7,
    )
    assert tagged[0]["volume_no"] == 2
    assert tagged[0]["chapter_no"] == 7
    # 已有标签不得被覆盖
    assert tag_facts([{"volume_no": 1}], volume_no=3, chapter_no=1)[0]["volume_no"] == 1


# ---------------------------------------------------------------------------
# 按卷切分
# ---------------------------------------------------------------------------


async def test_canon_facts_are_scoped_to_the_current_volume(novel_db):
    """前几卷的细节不该占当前卷的上下文名额。"""
    async with novel_db() as session:
        owner, work = await _work(session)
        first = await _volume(session, work, volume_no=1, state="COMPLETED", start=1, end=10)
        # 已完成卷通过摘要进入上下文：细节不该占当前卷的名额
        first.summary = [{"statement": "第一卷摘要"}]
        await _volume(session, work, volume_no=2, state="ACTIVE", start=11, end=20)
        await _canon(
            session,
            work,
            chapter_no=1,
            facts=tag_facts(
                [{"statement": "第一卷的旧事"}], volume_no=1, chapter_no=1
            ),
            version=1,
        )
        await _canon(
            session,
            work,
            chapter_no=11,
            facts=tag_facts(
                [{"statement": "第二卷的新事"}], volume_no=2, chapter_no=11
            ),
            version=2,
        )
        facts = await _canon_facts(session, work, None)
        statements = {f.get("statement") for f in facts}
        assert "第二卷的新事" in statements
        assert "第一卷摘要" in statements, "前卷摘要丢失"
        assert "第一卷的旧事" not in statements


async def test_unlabelled_facts_never_empty_the_context(novel_db):
    """历史事实没有卷标签时，宁可多带也不能把上下文清空到无法创作。"""
    async with novel_db() as session:
        owner, work = await _work(session)
        await _volume(session, work, volume_no=1, state="ACTIVE", start=1, end=10)
        await _canon(
            session,
            work,
            chapter_no=1,
            facts=[{"statement": "没有标签的旧事实"}],
            version=1,
        )
        facts = await _canon_facts(session, work, None)
        assert any(f.get("statement") == "没有标签的旧事实" for f in facts)


# ---------------------------------------------------------------------------
# 末节点终止
# ---------------------------------------------------------------------------


async def _path_with_nodes(session, work, node_ids=("n1", "n2", "n3")):
    path = CriticalPathModel(id=uuid.uuid4(), work_id=work.id, node_count=len(node_ids))
    session.add(path)
    await session.flush()
    for ordinal, node_id in enumerate(node_ids, start=1):
        session.add(
            CriticalNodeModel(
                id=uuid.uuid4(),
                path_id=path.id,
                node_id=node_id,
                ordinal=ordinal,
                title=f"节点{ordinal}",
            )
        )
    await session.flush()
    return path


async def test_last_node_completion_is_detected(novel_db):
    async with novel_db() as session:
        owner, work = await _work(session)
        await _path_with_nodes(session, work)
        run = ChapterRunModel(
            id=uuid.uuid4(),
            work_id=work.id,
            branch_id=work.branch_id,
            chapter_no=1,
            state=ChapterRunState.CANONIZED.value,
            generation_context={"node_completed": True, "target_node": {"id": "n3"}},
        )
        session.add(run)
        await session.flush()
        assert await works._last_node_completed(session, work, run) is True
        run.generation_context = {
            "node_completed": True,
            "target_node": {"id": "n2"},
        }
        assert await works._last_node_completed(session, work, run) is False
        run.generation_context = {"node_completed": False, "target_node": {"id": "n3"}}
        assert await works._last_node_completed(session, work, run) is False


async def test_start_run_refuses_after_the_story_is_complete(novel_db, monkeypatch):
    """整本结束后不得再开新章：末节点不能被反复重写。"""

    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)

    async with novel_db() as session:
        owner, work = await _work(session)
        session.add(
            ChapterRunModel(
                id=uuid.uuid4(),
                work_id=work.id,
                branch_id=work.branch_id,
                chapter_no=1,
                state=ChapterRunState.CANONIZED.value,
                generation_context={"story_complete": True},
            )
        )
        await session.commit()

    async with novel_db() as session:
        with pytest.raises(Conflict, match="already complete"):
            await works.start_run(session, owner_id=owner, work_id=work.id)
        await session.commit()

    async with novel_db() as session:
        stored = await session.scalar(select(StoryWorkModel))
        assert stored.state == StoryWorkState.DONE.value


async def test_volume_expansion_triggers_on_last_node(novel_db, monkeypatch):
    """末节点完成时也要跨卷展开，即便卷完成度还没到 80%。"""

    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)
    expanded: list[object] = []

    async def fake_expand(session, *, work, provider=None):
        expanded.append(work.id)
        return None

    monkeypatch.setattr(works, "expand_next_volume", fake_expand)

    async with novel_db() as session:
        owner, work = await _work(session)
        await _volume(session, work, volume_no=1, state="ACTIVE", start=1, end=100)
        await _path_with_nodes(session, work)
        work.latest_chapter_no = 5  # 完成度仅 5%，远低于 80%
        run = ChapterRunModel(
            id=uuid.uuid4(),
            work_id=work.id,
            branch_id=work.branch_id,
            chapter_no=5,
            state=ChapterRunState.CANONIZED.value,
            generation_context={"node_completed": True, "target_node": {"id": "n3"}},
        )
        session.add(run)
        await session.flush()
        await works._maybe_expand_volume(session, work=work, run=run)
        assert expanded == [work.id]
