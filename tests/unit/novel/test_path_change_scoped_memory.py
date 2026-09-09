"""A-05（一）：改意按**依赖范围**失效，而不是按类别整批失效。

可证伪命题：
- 依赖图完整时，改动一个节点只失效「它 + 它的下游」；上游、无关支线、世界规则
  必须活下来。若实现退回整批失效（旧行为），这两条会被误杀 → 用例失败。
- 依赖图不完整时必须走保守退路，并在事件里写明走了退路。若实现假装能算出
  最小子图 → 用例失败。
- 「改路径改的是承诺」：只改承诺文本、标题不动也算改动。若改动判定只看
  标题/顺序 → 用例失败。

人物 MECE 提醒：这里刻意区分了「上游」「下游」「无关支线」三类，因为整批失效
恰恰是把这三类一视同仁才错的。
"""

from __future__ import annotations

import uuid

import pytest
from regent.novel.application import memory as memory_app
from regent.novel.domain import memory as domain
from regent.novel.infrastructure.models import (
    CriticalNodeModel,
    CriticalPathModel,
    MemoryEdgeModel,
    MemoryItemModel,
)
from sqlalchemy import delete, select

from test_long_term_memory import _work  # noqa: E402

# 10 个节点（MIN_PATH_NODES），标题同时充当「节点承诺」的记忆主题——
# 与 generation.canon() 写入承诺时用节点标题作 subject 的生产行为一致。
TITLES = ["初遇", "结盟", "背叛", "失散", "重逢", "试炼", "真相", "对决", "清算", "归途"]
CHANGED_INDEX = 1  # 「结盟」被改
NEW_TITLE = "被迫结盟"
SIDE_THREAD = "旧宅密室"  # 与主线无依赖边的支线承诺
RULE_SUBJECT = "灵力"


def _nodes(titles: list[str]) -> list[tuple[str, int, str, str]]:
    """(node_id, ordinal, title, promise)。"""
    return [
        (f"node-{i}", i + 1, title, f"{title}埋下的承诺")
        for i, title in enumerate(titles)
    ]


async def _seed_path(session, work, *, titles: list[str]) -> None:
    rows = _nodes(titles)
    path = CriticalPathModel(
        id=uuid.uuid4(),
        work_id=work.id,
        version=1,
        frozen_through_chapter=0,
        node_count=len(rows),
        dependency_edges=[],
    )
    session.add(path)
    await session.flush()
    for node_id, ordinal, title, promise in rows:
        session.add(
            CriticalNodeModel(
                id=uuid.uuid4(),
                path_id=path.id,
                node_id=node_id,
                ordinal=ordinal,
                title=title,
                node_type="CUSTOM",
                promise=promise,
                preconditions=[],
                consequences=[],
            )
        )
    await session.flush()


async def _seed_memory(session, work, *, with_edges: bool = True) -> None:
    """写入与节点一一对应的承诺 + 一条无关支线 + 一条世界规则。"""
    facts = [
        {
            "memory_kind": "promise",
            "subject": title,
            "fact": f"{title}尚未兑现",
            "entities": [title],
        }
        for title in TITLES
    ]
    facts.append(
        {
            "memory_kind": "promise",
            "subject": SIDE_THREAD,
            "fact": "支线旧宅密室另有隐情",
            "entities": [SIDE_THREAD],
        }
    )
    facts.append(
        {
            "memory_kind": "rule",
            "subject": RULE_SUBJECT,
            "fact": "灵力耗尽后会反噬",
            "entities": [RULE_SUBJECT],
        }
    )
    await memory_app.record_chapter_memory(
        session, work=work, chapter_no=1, facts=facts
    )
    if with_edges:
        # 顺叙依赖：前一个节点的承诺是后一个节点得以成立的前提
        for upstream, downstream in zip(TITLES, TITLES[1:], strict=False):
            await memory_app.link_memory(
                session,
                work=work,
                upstream_key=_promise_key(upstream),
                downstream_key=_promise_key(downstream),
            )


def _promise_key(subject: str, content: str | None = None) -> str:
    """承诺类键带内容指纹（B-01）：构造键必须给出内容，否则查不到。"""
    return domain.item_key("promise", subject, content or f"{subject}尚未兑现")


def _capture_events(monkeypatch, target_module) -> list[dict]:
    captured: list[dict] = []

    async def _fake_event(session, **kwargs):
        captured.append(kwargs)
        return None

    monkeypatch.setattr(target_module, "append_event", _fake_event)
    return captured


async def _update(session, work, *, titles: list[str], promises: dict[str, str] | None = None):
    from regent.novel.application import works
    from regent.novel.domain.models import CriticalNode, CriticalPathUpdate

    promises = promises or {}
    payload = CriticalPathUpdate(
        nodes=[
            CriticalNode(
                node_id=node_id,
                ordinal=ordinal,
                title=title,
                promise=promises.get(title, promise),
            )
            for node_id, ordinal, title, promise in _nodes(titles)
        ],
        expected_version=1,
        change_note="改动中间节点",
    )
    return await works.update_critical_path(
        session, owner_id=work.owner_id, work_id=work.id, payload=payload
    )


def _titles_with_change() -> list[str]:
    titles = list(TITLES)
    titles[CHANGED_INDEX] = NEW_TITLE
    return titles


@pytest.mark.asyncio
async def test_complete_graph_invalidates_only_downstream(novel_db, monkeypatch):
    """图完整 → 只失效「被改节点 + 下游」；上游、支线、规则不受牵连。"""
    from regent.novel.application import works

    events = _capture_events(monkeypatch, works)

    async with novel_db() as s:
        work = await _work(s)
        await _seed_path(s, work, titles=TITLES)
        await _seed_memory(s, work, with_edges=True)
        await s.commit()

        _path_out, impact = await _update(s, work, titles=_titles_with_change())
        await s.commit()
        plan = await memory_app.plan_replay(
            s, work=work, changed_subjects=[TITLES[CHANGED_INDEX]]
        )

    assert impact.affected_chapters, "改动必须算出受影响章节"
    assert plan.complete, "依赖边齐备时应给出最小子图"

    async with novel_db() as s:
        rows = list((await s.scalars(select(MemoryItemModel))).all())
    invalidated = {r.item_key for r in rows if r.invalidated_at is not None}
    alive = {r.item_key for r in rows if r.invalidated_at is None}

    # 被改节点自身 + 它的全部下游
    expected_downstream = {
        _promise_key(t) for t in TITLES[CHANGED_INDEX:]
    }
    assert invalidated == expected_downstream
    # 上游（改它不需要推翻上游）、无关支线、世界规则全部存活
    assert _promise_key(TITLES[0]) in alive
    assert _promise_key(SIDE_THREAD, "支线旧宅密室另有隐情") in alive
    assert domain.item_key("rule", RULE_SUBJECT) in alive
    assert len(rows) == len(TITLES) + 2  # 失效是打标记，不是删除

    data = events[-1]["data"]
    assert data["invalidated_scope"] == "dependency_subgraph"
    assert data["invalidated_memory_items"] == len(expected_downstream)


@pytest.mark.asyncio
async def test_incomplete_graph_falls_back_conservatively(novel_db, monkeypatch):
    """依赖覆盖记录缺失 = 图不完整，必须保守失效同类整批，并如实记录走了退路。"""
    from regent.novel.application import works

    events = _capture_events(monkeypatch, works)

    async with novel_db() as s:
        work = await _work(s)
        await _seed_path(s, work, titles=TITLES)
        await _seed_memory(s, work, with_edges=False)
        # 建图入口会为「没有上游」的条目登记 independent；这里把覆盖记录全部抹掉，
        # 模拟依赖信息缺失。此时「这条是独立的」与「这条的依赖漏记了」无从区分，
        # 唯一诚实的处置就是保守重做。
        await s.execute(delete(MemoryEdgeModel))
        await s.commit()

        await _update(s, work, titles=_titles_with_change())
        await s.commit()

    async with novel_db() as s:
        rows = list((await s.scalars(select(MemoryItemModel))).all())
    invalidated = {r.item_key for r in rows if r.invalidated_at is not None}
    alive = {r.item_key for r in rows if r.invalidated_at is None}

    # 保守退路：所有承诺（含无关支线）失效，世界规则仍不因改路径消失
    assert _promise_key(SIDE_THREAD, "支线旧宅密室另有隐情") in invalidated
    assert _promise_key(TITLES[0]) in invalidated
    assert domain.item_key("rule", RULE_SUBJECT) in alive

    data = events[-1]["data"]
    assert data["invalidated_scope"] == "conservative_batch"
    assert data["invalidated_memory_items"] == len(TITLES) + 1


@pytest.mark.asyncio
async def test_promise_only_edit_counts_as_path_change(novel_db, monkeypatch):
    """「改路径改的是承诺」：标题与顺序不动、只改承诺文本也必须判定为改动。"""
    from regent.novel.application import works

    events = _capture_events(monkeypatch, works)

    async with novel_db() as s:
        work = await _work(s)
        await _seed_path(s, work, titles=TITLES)
        await _seed_memory(s, work, with_edges=True)
        await s.commit()

        _path_out, impact = await _update(
            s,
            work,
            titles=TITLES,  # 标题一个都没动
            promises={TITLES[CHANGED_INDEX]: "结盟另有隐情"},
        )
        await s.commit()

    assert impact.affected_chapters, "只改承诺也必须计入受影响范围"

    async with novel_db() as s:
        rows = list((await s.scalars(select(MemoryItemModel))).all())
    invalidated = {r.item_key for r in rows if r.invalidated_at is not None}
    assert invalidated == {_promise_key(t) for t in TITLES[CHANGED_INDEX:]}
    assert _promise_key(TITLES[0]) in {
        r.item_key for r in rows if r.invalidated_at is None
    }
    assert events[-1]["data"]["changed_subjects"] == [TITLES[CHANGED_INDEX]]
