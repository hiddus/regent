"""R3 长期创作与纠错（Plan §4 R3）。

出口条件对应的断言：
- 跨章知识获取：稳定规则在更晚的章节仍能被召回；
- 误信：低置信度事实也带来源（章节号 + 事实摘要），可举证；
- 伏笔回收：未兑现的承诺永不因限额被裁掉，兑现后状态可查；
- 关系变化：关系类条目独立成类并可召回；
- 旧版上下文不污染新版：改意只置失效，不删除也不改写事实链。
"""

from __future__ import annotations

import uuid

import pytest
from regent.novel.application import memory as memory_app
from regent.novel.domain import memory as domain
from regent.novel.infrastructure.models import (
    MemoryItemModel,
    NovelPrincipalModel,
    StoryWorkModel,
)
from sqlalchemy import select


async def _work(session) -> StoryWorkModel:
    owner = uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject=f"memory:{owner}"))
    work = StoryWorkModel(id=uuid.uuid4(), owner_id=owner, state="RUNNING", genre="悬疑")
    session.add(work)
    await session.flush()
    return work


def _rule(subject="灵力", text="灵力耗尽后会反噬"):
    return {
        "memory_kind": "rule", "subject": subject, "fact": text,
        "entities": ["灵力"], "confidence": "high",
    }


def _promise(subject="玉佩", text="玉佩来历未交代"):
    return {
        "memory_kind": "promise", "subject": subject, "fact": text,
        "entities": ["玉佩"], "confidence": "high",
    }


def test_extract_is_deterministic_and_typed():
    facts = [_rule(), _promise(), {"fact": "天气不错"}]  # 最后一条不分类 → 不记
    first = domain.extract_items(facts, chapter_no=1)
    second = domain.extract_items(list(reversed(facts)), chapter_no=1)
    assert [i.key for i in first] == [i.key for i in second]
    assert sorted(i.kind for i in first) == ["promise", "rule"]
    assert all(i.source_chapter_no == 1 for i in first)


def test_unmarked_facts_are_not_memorized():
    """宁可漏记也不误记：没有类别标记的事实不得混进「稳定规则」。 """
    assert domain.extract_items([{"fact": "他坐在窗边"}], chapter_no=1) == []


@pytest.mark.asyncio
async def test_stable_rule_survives_across_chapters(novel_db):
    async with novel_db() as s:
        work = await _work(s)
        await memory_app.record_chapter_memory(s, work=work, chapter_no=1, facts=[_rule()])
        await s.commit()
        bundle = await memory_app.recall_memory(s, work=work, chapter_no=9, limit=4)
    assert any(i.kind == "rule" and i.subject == "灵力" for i in bundle.items)
    assert bundle.source_hash  # 召回结果可复现地留痕


@pytest.mark.asyncio
async def test_open_promise_never_dropped_by_limit(novel_db):
    """伏笔回收失败不可逆：限额再小也必须带上未兑现的承诺。"""
    async with novel_db() as s:
        work = await _work(s)
        facts = [_promise(f"伏笔-{i}", f"第 {i} 处未交代") for i in range(30)]
        facts += [_rule(f"规则-{i}", f"第 {i} 条设定") for i in range(30)]
        await memory_app.record_chapter_memory(s, work=work, chapter_no=1, facts=facts)
        await s.commit()
        bundle = await memory_app.recall_memory(s, work=work, chapter_no=2, limit=3)
    open_promises = {i.subject for i in bundle.items if i.kind == "promise" and i.state == "OPEN"}
    assert open_promises == {f"伏笔-{i}" for i in range(30)}


@pytest.mark.asyncio
async def test_promise_payoff_resolves_state(novel_db):
    async with novel_db() as s:
        work = await _work(s)
        await memory_app.record_chapter_memory(s, work=work, chapter_no=1, facts=[_promise()])
        await s.commit()
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=4,
            facts=[{"fact": "玉佩是母亲遗物", "resolves": "玉佩"}],
        )
        await s.commit()
        rows = list((await s.scalars(select(MemoryItemModel))).all())
    item = next(r for r in rows if r.subject == "玉佩")
    assert item.state == "RESOLVED"
    # 种下章与兑现章分开留痕：「第几章埋的」和「第几章收的」都要能查
    assert int(item.source_chapter_no) == 1
    assert int(item.resolved_chapter_no) == 4


@pytest.mark.asyncio
async def test_misbelief_keeps_its_source(novel_db):
    """误信也是记忆：置信度为 low 的事实同样带来源，便于之后纠错。"""
    async with novel_db() as s:
        work = await _work(s)
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=2,
            facts=[{**_rule("城主", "城主是叛徒"), "confidence": "low"}],
            source_hash="deadbeef",
        )
        await s.commit()
        bundle = await memory_app.recall_memory(s, work=work, chapter_no=3)
    item = next(i for i in bundle.items if i.subject == "城主")
    assert item.confidence == "low"
    assert item.source_hash == "deadbeef"
    assert item.source_chapter_no == 2


@pytest.mark.asyncio
async def test_relation_change_is_recalled(novel_db):
    async with novel_db() as s:
        work = await _work(s)
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=3,
            facts=[{
                "memory_kind": "relation", "relation_between": ["阿岚", "沈执"],
                "fact": "同盟破裂", "entities": ["阿岚", "沈执"],
            }],
        )
        await s.commit()
        bundle = await memory_app.recall_memory(
            s, work=work, chapter_no=4, kinds=["relation"], entities=["阿岚"]
        )
    assert [i.subject for i in bundle.items] == ["阿岚↔沈执"]


@pytest.mark.asyncio
async def test_direction_change_invalidates_without_rewriting(novel_db):
    """改意只失效不修改：行还在（留痕），但不再进入召回。"""
    async with novel_db() as s:
        work = await _work(s)
        await memory_app.record_chapter_memory(s, work=work, chapter_no=1, facts=[_rule()])
        await s.commit()
        before = len((await s.scalars(select(MemoryItemModel))).all())
        invalidated = await memory_app.invalidate_memory(s, work=work, reason="goal_changed")
        await s.commit()
        after = len((await s.scalars(select(MemoryItemModel))).all())
        bundle = await memory_app.recall_memory(s, work=work, chapter_no=2)
    assert invalidated == 1
    assert after == before  # 不删除
    assert bundle.items == ()  # 旧设定不再污染新版


def test_replay_subgraph_is_minimal_and_deterministic():
    items = domain.extract_items(
        [
            _rule("境界", "境界不可越级"),
            {"memory_kind": "promise", "subject": "密约", "fact": "密约待兑现"},
            {"memory_kind": "promise", "subject": "远征", "fact": "远征未决"},
        ],
        chapter_no=1,
    )
    key = {i.subject: i.key for i in items}
    edges = [
        (key["境界"], key["密约"]),
        (key["密约"], key["远征"]),
    ]
    # 起点没有上游：必须显式登记为独立，否则「起点」与「漏记了它的上游」无从区分
    plan = domain.replay_subgraph(items, edges, changed=["境界"], independent=[key["境界"]])
    assert plan.complete
    assert set(plan.subjects) == {"境界", "密约", "远征"}
    again = domain.replay_subgraph(items, edges, changed=["境界"], independent=[key["境界"]])
    assert plan == again


def test_edges_alone_do_not_prove_coverage():
    """有边不等于图完整：没有覆盖记录的条目必须让计划退回保守（B-02）。"""
    items = domain.extract_items(
        [
            _rule("境界", "境界不可越级"),
            {"memory_kind": "promise", "subject": "密约", "fact": "密约待兑现"},
            {"memory_kind": "promise", "subject": "旁支", "fact": "旁支未交代"},
        ],
        chapter_no=1,
    )
    key = {i.subject: i.key for i in items}
    # 只记了 境界→密约：旁支既可能是独立，也可能是漏记了它的上游
    plan = domain.replay_subgraph(items, [(key["境界"], key["密约"])], changed=["境界"])
    assert not plan.complete
    assert key["旁支"] in plan.unknown
    assert "保守" in plan.reason


def test_incomplete_dependency_graph_refuses_precision():
    """依赖信息不完整时不能假装算出子图。"""
    items = domain.extract_items([_rule("境界", "境界不可越级")], chapter_no=1)
    plan = domain.replay_subgraph(
        items, [("rule:境界", "promise:密约")], changed=["境界"]
    )
    assert not plan.complete
    assert "保守" in plan.reason


@pytest.mark.asyncio
async def test_plan_replay_reads_edges_from_store(novel_db):
    async with novel_db() as s:
        work = await _work(s)
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1,
            facts=[_rule("境界", "境界不可越级"),
                   {"memory_kind": "promise", "subject": "密约", "fact": "密约待兑现"}],
        )
        await memory_app.link_memory(
            s, work=work,
            upstream_key=domain.item_key("rule", "境界"),
            downstream_key=domain.item_key("promise", "密约", "密约待兑现"),
        )
        await s.commit()
        plan = await memory_app.plan_replay(s, work=work, changed_subjects=["境界"])
    assert plan.complete
    assert set(plan.subjects) == {"境界", "密约"}


@pytest.mark.asyncio
async def test_critical_path_change_invalidates_path_dependent_memory(novel_db, monkeypatch):
    """改路径只失效承诺与弧线，世界规则保留；事实链不动。"""
    from regent.novel.application import works

    # SQLite 不支持裸 SQL 绑定 UUID（既有方言限制），本用例不验证事件落库
    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)
    from regent.novel.domain.models import CriticalNode, CriticalPathUpdate
    from regent.novel.infrastructure.models import CriticalNodeModel, CriticalPathModel

    def nodes(prefix: str) -> list[CriticalNode]:
        return [
            CriticalNode(node_id=f"{prefix}-{i}", ordinal=i, title=f"{prefix}{i}")
            for i in range(works.MIN_PATH_NODES)
        ]

    async with novel_db() as s:
        work = await _work(s)
        path = CriticalPathModel(
            id=uuid.uuid4(), work_id=work.id, version=1, frozen_through_chapter=0,
            node_count=works.MIN_PATH_NODES, dependency_edges=[],
        )
        s.add(path)
        await s.flush()
        for node in nodes("n"):
            s.add(CriticalNodeModel(
                id=uuid.uuid4(), path_id=path.id, node_id=node.node_id,
                ordinal=node.ordinal, title=node.title, node_type="CUSTOM",
                promise="", preconditions=[], consequences=[],
            ))
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1,
            facts=[_rule(), _promise("旧伏笔", "旧路径埋下的伏笔")],
        )
        await s.commit()

        _, impact = await works.update_critical_path(
            s,
            owner_id=work.owner_id,
            work_id=work.id,
            payload=CriticalPathUpdate(
                nodes=nodes("m"), expected_version=1, change_note="换方向"
            ),
        )
        await s.commit()
        assert impact is not None
        bundle = await memory_app.recall_memory(s, work=work, chapter_no=2)

    subjects = {i.subject for i in bundle.items}
    assert "旧伏笔" not in subjects      # 依托旧路径的承诺已失效
    assert "灵力" in subjects            # 世界规则不因改路径消失

    async with novel_db() as s:
        rows = list((await s.scalars(select(MemoryItemModel))).all())
    assert len(rows) == 2  # 失效是打标记，不是删除
