# Chinese fixtures deliberately use full-width punctuation.
# ruff: noqa: RUF001

"""审核、投诉与申诉的行为测试（FR-25 / G-23）。

核心不变量：任何投诉都必须落 ModerationCase；**无结论不得视为通过**。
本地规则扫描只提名，不判定、不删除、不阻断。
"""

import uuid

import pytest
from regent.novel.application import works
from regent.novel.domain.errors import NotFound, ValidationFailed
from regent.novel.domain.moderation import ScanResult, scan_text
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    ModerationCaseModel,
    NovelPrincipalModel,
    StoryWorkModel,
)
from sqlalchemy import select

async def _noop_event(session, **kwargs):
    # 事件序列分配器是 Postgres 专有实现，SQLite 下不参与本测试。
    return None


@pytest.fixture(autouse=True)
def _no_events(monkeypatch):
    monkeypatch.setattr(works, "append_event", _noop_event)


async def _seed(session, chapter_no: int = 1, content: str = "他把钥匙放在桌上。"):
    owner, work_id = uuid.uuid4(), uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject="moderation-test"))
    session.add(
        StoryWorkModel(id=work_id, owner_id=owner, state="READY", genre="悬疑", title="交付")
    )
    session.add(
        ChapterRunModel(
            id=uuid.uuid4(),
            work_id=work_id,
            branch_id=uuid.uuid4(),
            chapter_no=chapter_no,
            attempt=1,
            state="CANONIZED",
            content=content,
        )
    )
    await session.flush()
    return owner, work_id


async def test_report_creates_pending_case_and_never_implies_resolution(novel_db):
    sessions = novel_db
    engine = sessions.kw["bind"]
    try:
        async with sessions() as session:
            owner, work_id = await _seed(session)
            out = await works.report_moderation(
                session,
                owner_id=owner,
                work_id=work_id,
                chapter_no=1,
                reason_code="infringement",
                detail="第 1 章疑似侵权",
            )
            assert out.decision.value == "PENDING"
            assert out.resolved_at is None
            assert out.target_type == "CHAPTER"
            row = (await session.scalars(select(ModerationCaseModel))).one()
            assert row.decision == "PENDING"
            # 投诉不得静默删除或改写正文
            run = await session.scalar(select(ChapterRunModel))
            assert run.content == "他把钥匙放在桌上。"
            await session.commit()
    finally:
        await engine.dispose()


async def test_report_rejects_unknown_reason_code(novel_db):
    sessions = novel_db
    engine = sessions.kw["bind"]
    try:
        async with sessions() as session:
            owner, work_id = await _seed(session)
            with pytest.raises(ValidationFailed):
                await works.report_moderation(
                    session, owner_id=owner, work_id=work_id, reason_code="because"
                )
    finally:
        await engine.dispose()


async def test_report_on_missing_chapter_is_not_found(novel_db):
    sessions = novel_db
    engine = sessions.kw["bind"]
    try:
        async with sessions() as session:
            owner, work_id = await _seed(session)
            with pytest.raises(NotFound):
                await works.report_moderation(
                    session, owner_id=owner, work_id=work_id, chapter_no=99
                )
    finally:
        await engine.dispose()


async def test_scan_without_terms_reports_it_cannot_scan(novel_db):
    """未配置词表 = 无法扫描，不是“扫描通过”（G-23）。"""
    sessions = novel_db
    engine = sessions.kw["bind"]
    try:
        async with sessions() as session:
            owner, work_id = await _seed(session)
            result = await works.scan_chapter_rules(
                session, owner_id=owner, work_id=work_id, chapter_no=1
            )
            assert result["configured"] is False
            assert result["scanned"] is False
            assert (await session.scalars(select(ModerationCaseModel))).all() == []
    finally:
        await engine.dispose()


async def test_scan_with_terms_only_nominates_and_keeps_the_text(novel_db, monkeypatch):
    monkeypatch.setenv("NOVEL_MODERATION_TERMS", "钥匙")
    sessions = novel_db
    engine = sessions.kw["bind"]
    try:
        async with sessions() as session:
            owner, work_id = await _seed(session, content="他把钥匙放在桌上，钥匙转了半圈。")
            result = await works.scan_chapter_rules(
                session, owner_id=owner, work_id=work_id, chapter_no=1
            )
            assert result["configured"] is True and result["hits"] == 2
            cases = (await session.scalars(select(ModerationCaseModel))).all()
            assert len(cases) == 2
            # 只提名：案件待处理，正文不动，作品不进入任何“已处理/已通过”状态
            assert {c.decision for c in cases} == {"PENDING"}
            run = await session.scalar(select(ChapterRunModel))
            assert run.content.startswith("他把钥匙")
            work = await session.get(StoryWorkModel, work_id)
            assert work.state == "READY"
            await session.commit()
    finally:
        await engine.dispose()


def test_scan_result_distinguishes_clean_from_unscanned():
    unscanned = ScanResult(configured=False, hits=())
    assert unscanned.clean is False
    assert unscanned.scanned is False
    clean = ScanResult(configured=True, hits=())
    assert clean.clean is True
    assert scan_text("任何正文", terms=()).configured is False
