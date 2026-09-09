"""A-05（二）：灰度必须是**能跑的开关**，不是函数骨架。

可证伪命题：
- 同一作品永远落在同一桶：重新选择、或改变调用顺序，结果都不变。若分桶依赖
  调用次序或内存状态 → 用例失败。
- 灰度臂必须是已知执行器：未知名字被忽略，整章不会走进跑不通的路径。
  若实现允许任意名字 → 用例失败。
- 章 endowed 的执行器在出生时钉住：灰度比例中途调到 100% 时，**在途章继续用
  旧执行器**，下一章才用新臂。若实现每步都重新选择 → 用例失败。

为什么在测试里放开 KNOWN_EXECUTORS：这道闸门本身保护的是生产（未知执行器不
得上线），这里要验证的是闸门之后的**路由/分桶/钉住**逻辑，所以模拟出一个未来
会实现的新臂是合理的；「未知名字被忽略」另有用例专门守住闸门本身。
"""

from __future__ import annotations

import uuid

import pytest
from regent.novel.application import executor as executor_app
from regent.novel.application import works
from regent.novel.application.direction import ARCHITECTURE
from regent.novel.infrastructure.models import ChapterRunModel
from sqlalchemy import select

from test_last_node_and_volume import (  # noqa: E402
    _Provider,
    _boot,
    _chapter_outputs,
    _run_chapter,
)

CANARY = "director_v3"


def _future_arm(monkeypatch, *, percent: str) -> None:
    """模拟一个未来实现的新执行器上线，并按给定比例灰度。"""
    monkeypatch.setattr(
        executor_app, "KNOWN_EXECUTORS", frozenset({ARCHITECTURE, CANARY})
    )
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY", CANARY)
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY_PERCENT", percent)


def test_bucket_is_deterministic_and_splits_traffic(monkeypatch):
    _future_arm(monkeypatch, percent="50")
    work_ids = [str(uuid.UUID(int=i)) for i in range(1, 41)]

    first = {wid: executor_app.choose_executor(wid) for wid in work_ids}
    again = {wid: executor_app.choose_executor(wid) for wid in reversed(work_ids)}
    assert first == again, "同一作品换次序遍历就换桶，灰度期间会来回横跳"
    assert set(first.values()) == {ARCHITECTURE, CANARY}, "50% 灰度没有真的分流"


def test_unknown_executor_name_is_ignored(monkeypatch):
    """未知执行器不能当灰度臂：整章会走进跑不通的路径。"""
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY", "not_an_implemented_executor")
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY_PERCENT", "100")
    assert executor_app.canary_executor() == ""
    assert executor_app.canary_percent() == 0, "没有灰度臂时开关必须在坡底"
    assert executor_app.choose_executor("any-work") == ARCHITECTURE


async def test_in_flight_chapter_keeps_its_pinned_executor(novel_db, monkeypatch):
    """在途章不换执行器也不停摆；灰度比例调整只作用于之后的章。"""
    events: list[dict] = []

    async def _capture(session, **kwargs):
        events.append(kwargs)
        return None

    monkeypatch.setattr(works, "append_event", _capture)
    provider = _Provider(_chapter_outputs(True) + _chapter_outputs(False))
    owner, work_id = await _boot(novel_db, node_count=2, end_chapter=6)

    async with novel_db() as session:
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()
    async with novel_db() as session:
        run = await session.scalar(
            select(ChapterRunModel).where(
                ChapterRunModel.work_id == work_id, ChapterRunModel.chapter_no == 1
            )
        )
        assert (run.generation_context or {}).get("executor") == ARCHITECTURE

    # 灰度拉满：新章走灰度臂，但已经在跑的第一章继续用出生时钉住的那个
    _future_arm(monkeypatch, percent="100")
    await _run_chapter(novel_db, provider, owner, work_id, 1)

    async with novel_db() as session:
        run = await session.scalar(
            select(ChapterRunModel).where(
                ChapterRunModel.work_id == work_id, ChapterRunModel.chapter_no == 1
            )
        )
    context = run.generation_context or {}
    assert context.get("executor") == ARCHITECTURE, "在途章被中途换了执行器"
    scope = context.get(executor_app.DEFER_MARKER) or {}
    assert scope.get("requested") == CANARY, "没有留下「切换被推迟」的记录"
    deferred = [e for e in events if e["event_type"] == "executor.switch_deferred"]
    assert len(deferred) == 1, f"延期切换应只记录一次：{len(deferred)}"

    async with novel_db() as session:
        progress = await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()
        assert progress.chapter_no == 2
        run = await session.scalar(
            select(ChapterRunModel).where(
                ChapterRunModel.work_id == work_id, ChapterRunModel.chapter_no == 2
            )
        )
    context = run.generation_context or {}
    assert context.get("executor") == CANARY, "新章没有走到灰度臂"
    assert context.get("architecture_version") == CANARY
