"""F4/F5/F6/F9：连续创作入口、权威 attempt 屏障、旧调用键缺省。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from regent.novel.application.directing_calls import (
    hydrate_call_key_version,
    resolve_call_key_version,
)
from regent.novel.application.works_continuation import (
    _earlier_blocks_continuation,
    ensure_next_run,
    get_continuation_policy,
    set_continuation_policy,
)
from regent.novel.domain.errors import ValidationFailed
from regent.novel.domain.states import ChapterRunState, StoryWorkState


def test_f6_legacy_missing_call_key_defaults_to_v1():
    assert resolve_call_key_version({}) == 1
    assert resolve_call_key_version({"decisions": []}) == 1
    run = SimpleNamespace(generation_context={})
    assert resolve_call_key_version({}, run) == 1
    run2 = SimpleNamespace(generation_context={"call_key_version": 2})
    assert resolve_call_key_version({}, run2) == 2
    prod: dict = {}
    hydrate_call_key_version(prod, run)
    assert prod["call_key_version"] == 1


def test_f4_set_continuation_policy_validates_scope():
    work = SimpleNamespace(story_bible={}, version=1)
    with pytest.raises(ValidationFailed):
        set_continuation_policy(work, enabled=True, volume_scope="nope")
    pol = set_continuation_policy(
        work, enabled=True, target_chapter_no=5, volume_scope="current"
    )
    assert pol.enabled is True
    assert pol.target_chapter_no == 5
    assert get_continuation_policy(work).version >= 1


@pytest.mark.asyncio
async def test_f9_old_terminal_failed_does_not_block_when_later_canonized(novel_db):
    from regent.novel.infrastructure.models import (
        ChapterRunModel,
        NovelPrincipalModel,
        StoryWorkModel,
    )

    async with novel_db() as s:
        owner = uuid.uuid4()
        s.add(NovelPrincipalModel(id=owner, subject=f"c:{owner}"))
        work = StoryWorkModel(
            id=uuid.uuid4(),
            owner_id=owner,
            state=StoryWorkState.RUNNING.value,
            genre="悬疑",
            latest_chapter_no=1,
            story_bible_locked_at=__import__("datetime").datetime.now(
                __import__("datetime").UTC
            ),
        )
        s.add(work)
        await s.flush()
        failed = ChapterRunModel(
            id=uuid.uuid4(),
            work_id=work.id,
            branch_id=work.branch_id,
            chapter_no=1,
            attempt=1,
            state=ChapterRunState.TERMINAL_FAILED.value,
            current_step="PRODUCE",
            title="失败稿",
            generation_context={},
        )
        ok = ChapterRunModel(
            id=uuid.uuid4(),
            work_id=work.id,
            branch_id=work.branch_id,
            chapter_no=1,
            attempt=2,
            state=ChapterRunState.CANONIZED.value,
            current_step="CANON",
            title="成功稿",
            generation_context={},
        )
        s.add_all([failed, ok])
        await s.commit()

        assert (
            await _earlier_blocks_continuation(s, work=work, before_chapter=2)
        ) is False

        set_continuation_policy(work, enabled=True, target_chapter_no=3)
        nxt = await ensure_next_run(s, work=work, completed_run=ok)
        assert nxt is not None
        assert int(nxt.chapter_no) == 2


@pytest.mark.asyncio
async def test_f9_latest_terminal_still_blocks(novel_db):
    from datetime import UTC, datetime

    from regent.novel.infrastructure.models import (
        ChapterRunModel,
        NovelPrincipalModel,
        StoryWorkModel,
    )

    async with novel_db() as s:
        owner = uuid.uuid4()
        s.add(NovelPrincipalModel(id=owner, subject=f"c:{owner}"))
        work = StoryWorkModel(
            id=uuid.uuid4(),
            owner_id=owner,
            state=StoryWorkState.RUNNING.value,
            genre="悬疑",
            latest_chapter_no=1,
            story_bible_locked_at=datetime.now(UTC),
        )
        s.add(work)
        await s.flush()
        failed = ChapterRunModel(
            id=uuid.uuid4(),
            work_id=work.id,
            branch_id=work.branch_id,
            chapter_no=1,
            attempt=1,
            state=ChapterRunState.TERMINAL_FAILED.value,
            current_step="PRODUCE",
            title="失败",
            generation_context={},
        )
        s.add(failed)
        await s.commit()
        assert (
            await _earlier_blocks_continuation(s, work=work, before_chapter=2)
        ) is True


@pytest.mark.asyncio
async def test_f5_ensure_next_run_idempotent_returns_same(novel_db):
    from datetime import UTC, datetime

    from regent.novel.infrastructure.models import (
        ChapterRunModel,
        NovelPrincipalModel,
        StoryWorkModel,
    )

    async with novel_db() as s:
        owner = uuid.uuid4()
        s.add(NovelPrincipalModel(id=owner, subject=f"c:{owner}"))
        work = StoryWorkModel(
            id=uuid.uuid4(),
            owner_id=owner,
            state=StoryWorkState.RUNNING.value,
            genre="悬疑",
            latest_chapter_no=1,
            story_bible_locked_at=datetime.now(UTC),
        )
        s.add(work)
        await s.flush()
        done = ChapterRunModel(
            id=uuid.uuid4(),
            work_id=work.id,
            branch_id=work.branch_id,
            chapter_no=1,
            attempt=1,
            state=ChapterRunState.CANONIZED.value,
            current_step="CANON",
            title="一",
            generation_context={},
        )
        s.add(done)
        await s.commit()
        set_continuation_policy(work, enabled=True, target_chapter_no=5)
        a = await ensure_next_run(s, work=work, completed_run=done)
        b = await ensure_next_run(s, work=work, completed_run=done)
        assert a is not None and b is not None
        assert a.id == b.id
        assert int(a.chapter_no) == 2
