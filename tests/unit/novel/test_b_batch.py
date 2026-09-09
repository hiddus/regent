"""B-01 / B-02 的入口级验收（Plan v7.3）。

这层的命题都是**可证伪**的：每条都指向一个「如果实现退回旧行为就会失败」的
具体断言，而不是「字段存在」这种永远为真的检查。

- B-01：正式事实能形成规则、承诺与兑现；承诺不互相覆盖；六视图有可见性边界。
- B-02：有边不等于依赖完整；纠错从入口真的按范围重演，不是只打失效标记。

人物 MECE 提醒：这里的受众刻意分成 director / character / reader 三类——把
「人物知道」「读者知道」「导演知道」混为一谈，正是信息隔离失守的常见形式。
"""

# Chinese fixture prose deliberately uses full-width punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

import uuid

import pytest
from regent.novel.application import memory as memory_app
from regent.novel.application import works
from regent.novel.domain import memory as domain
from regent.novel.domain.models import (
    CriticalNode,
    CriticalPathUpdate,
    ReportFactRequest,
)
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    CriticalNodeModel,
    CriticalPathModel,
    MemoryEdgeModel,
    MemoryItemModel,
    NovelPrincipalModel,
    StoryWorkModel,
)
from sqlalchemy import delete, select


async def _work(session, *, latest_chapter_no: int = 0) -> StoryWorkModel:
    owner = uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject=f"b:{owner}"))
    work = StoryWorkModel(
        id=uuid.uuid4(), owner_id=owner, state="RUNNING", genre="悬疑",
        latest_chapter_no=latest_chapter_no,
    )
    session.add(work)
    await session.flush()
    return work


async def _chapter_runs(session, work: StoryWorkModel, count: int) -> None:
    for chapter_no in range(1, count + 1):
        session.add(
            ChapterRunModel(
                id=uuid.uuid4(),
                work_id=work.id,
                branch_id=work.branch_id,
                chapter_no=chapter_no,
                attempt=1,
                state="CANONIZED",
            )
        )
    await session.flush()


def _fact(statement: str, **extra) -> dict:
    """正式核验输出：只有 statement/quote/known_by/entities，没有分类标记。

    刻意不给 memory_kind：分类标记一给，测的就不是「正式事实能不能被正确分类」
    而是「人工标签能不能被读进去」——后者早就是通的。
    """
    return {"statement": statement, "quote": statement, "known_by": ["甲"], **extra}


# ---------------------------------------------------------------------------
# B-01 正式长期记忆
# ---------------------------------------------------------------------------


def test_promise_is_not_mistaken_for_character_arc():
    """「甲承诺明日归还钥匙」必须记成承诺，不是人物弧线（B-01）。"""
    items = domain.extract_items(
        [_fact("甲承诺明日归还钥匙")], chapter_no=1, cast=["甲"]
    )
    assert len(items) == 1
    assert items[0].kind == "promise", items[0].as_payload()
    assert items[0].subject == "甲"
    assert items[0].basis == domain.BASIS_PROMISE_SIGNAL


def test_two_promises_by_the_same_person_do_not_overwrite_each_other():
    """同一人物的两条承诺各成一条：覆盖等于把伏笔丢在写操作里（B-01）。"""
    items = domain.extract_items(
        [_fact("甲承诺明日归还钥匙"), _fact("甲立誓三年后复仇")],
        chapter_no=1,
        cast=["甲"],
    )
    assert len(items) == 2, [i.key for i in items]
    assert {i.content for i in items} == {"甲承诺明日归还钥匙", "甲立誓三年后复仇"}


def test_co_occurrence_is_not_pretended_to_be_a_relation_change():
    """两人同场不等于关系变化：来源与置信度必须能区分（B-01）。"""
    kind, basis = domain.classify_with_basis(
        {"statement": "甲与乙同在厅中", "known_by": ["甲", "乙"]}, cast=["甲", "乙"]
    )
    assert kind == "relation" and basis == domain.BASIS_CO_OCCURRENCE
    items = domain.extract_items(
        [{"statement": "甲与乙同在厅中", "known_by": ["甲", "乙"]}],
        chapter_no=1,
        cast=["甲", "乙"],
    )
    # 推断出来的关系不能冒充 high：否则「同场」和「反目」在召回里权重相同
    assert items[0].confidence == "medium"

    kind, basis = domain.classify_with_basis(
        {"statement": "甲与乙当场反目", "known_by": ["甲", "乙"]}, cast=["甲", "乙"]
    )
    assert kind == "relation" and basis == domain.BASIS_RELATION_SIGNAL


def test_misbelief_and_reader_knowledge_get_their_own_kinds():
    """误信与读者认知是六视图里的独立两类，不能并进人物弧线（B-01）。"""
    items = domain.extract_items(
        [
            _fact("甲误以为乙已经死了"),
            _fact("读者尚不知道钥匙的来处"),
            _fact("雨景写得太满，记录失败原因", **{"known_by": []}),
        ],
        chapter_no=2,
        cast=["甲", "乙"],
    )
    kinds = {i.kind for i in items}
    assert "belief" in kinds, [i.as_payload() for i in items]
    assert "reader_knowledge" in kinds
    assert "director_note" in kinds


def test_six_views_have_visibility_boundaries():
    """导演记忆永不进人物与读者视角；读者认知不给人物（B-01）。"""
    items = domain.extract_items(
        [
            {"memory_kind": "rule", "subject": "灯", "fact": "灯灭则鬼现"},
            {"memory_kind": "promise", "subject": "甲", "fact": "甲承诺归还钥匙"},
            {"memory_kind": "belief", "subject": "甲", "fact": "甲以为乙死了"},
            {"memory_kind": "reader_knowledge", "subject": "钥匙", "fact": "读者未见来处"},
            {"memory_kind": "director_note", "subject": "雨景", "fact": "雨景写得太满"},
        ],
        chapter_no=1,
    )
    views = {i.view for i in items}
    assert set(domain.VIEWS) - views <= {"character_state"}
    # 导演看得到全部
    assert len(domain.project_for(items, "director")) == len(items)
    # 人物不知道读者知道了什么
    assert not any(
        i.kind == "reader_knowledge" for i in domain.project_for(items, "character")
    )
    # 导演记忆不进正文，也不进人物上下文
    for audience in ("character", "narrator", "reader"):
        assert not any(
            i.kind == "director_note" for i in domain.project_for(items, audience)
        ), audience
    # 误信对人物自己可见（它驱动行为），对读者不可见
    assert any(i.kind == "belief" for i in domain.project_for(items, "character"))
    assert not any(i.kind == "belief" for i in domain.project_for(items, "reader"))
    # 未知受众按最严格处理
    assert all(i.kind == "rule" for i in domain.project_for(items, "someone"))


@pytest.mark.asyncio
async def test_payoff_keeps_plant_chapter_and_records_payoff_chapter(novel_db):
    """兑现后仍要能查到「第几章埋的」和「第几章收的」（B-01）。"""
    async with novel_db() as s:
        work = await _work(s)
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=2, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        await memory_app.record_chapter_memory(
            s,
            work=work,
            chapter_no=7,
            facts=[{"statement": "甲把钥匙放在桌上", "resolves": "甲"}],
            cast=["甲"],
        )
        await s.commit()
        rows = list((await s.scalars(select(MemoryItemModel))).all())
    item = next(r for r in rows if r.subject == "甲")
    assert item.state == "RESOLVED"
    assert int(item.source_chapter_no) == 2, "种下章被兑现覆盖了"
    assert int(item.resolved_chapter_no) == 7


@pytest.mark.asyncio
async def test_restatements_do_not_reopen_a_resolved_promise(novel_db):
    """已兑现的承诺被重述，不得又变回未兑现（B-01）。"""
    async with novel_db() as s:
        work = await _work(s)
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=2, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        await memory_app.record_chapter_memory(
            s,
            work=work,
            chapter_no=7,
            facts=[{"statement": "甲把钥匙放在桌上", "resolves": "甲"}],
            cast=["甲"],
        )
        await s.commit()
        # 第 9 章又把这句承诺写了一遍
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=9, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        rows = list((await s.scalars(select(MemoryItemModel))).all())
    item = next(r for r in rows if r.subject == "甲")
    assert item.state == "RESOLVED"
    assert int(item.resolved_chapter_no) == 7


# ---------------------------------------------------------------------------
# B-02 依赖完整性与真实重演
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_isolated_node_without_coverage_record_is_not_complete(novel_db):
    """探测三条记忆、只记一条边：不得宣称图完整（B-02）。"""
    async with novel_db() as s:
        work = await _work(s)
        await memory_app.record_chapter_memory(
            s,
            work=work,
            chapter_no=1,
            facts=[
                {"memory_kind": "promise", "subject": "a", "fact": "a 待兑现"},
                {"memory_kind": "promise", "subject": "b", "fact": "b 待兑现"},
                {"memory_kind": "promise", "subject": "c", "fact": "c 待兑现"},
            ],
        )
        await s.commit()
        rows = {r.subject: r.item_key for r in
                (await s.scalars(select(MemoryItemModel))).all()}
        await memory_app.link_memory(
            s, work=work, upstream_key=rows["a"], downstream_key=rows["b"]
        )
        await s.commit()
        # 建图入口为三条都登记了 independent；抹掉 c 的那条，模拟「漏记了依赖」。
        await s.execute(
            delete(MemoryEdgeModel).where(
                MemoryEdgeModel.downstream_key == rows["c"],
                MemoryEdgeModel.edge_kind == "independent",
            )
        )
        await s.commit()
        # 只剩 a→b：c 既可能独立，也可能是漏记了 b→c
        plan = await memory_app.plan_replay(s, work=work, changed_subjects=["a"])
    assert not plan.complete
    assert rows["c"] in plan.unknown


@pytest.mark.asyncio
async def test_reported_fact_actually_queues_replay_of_the_right_chapters(novel_db, monkeypatch):
    """纠错必须真的把受影响的章排进重跑队列，不只打失效标记（B-02）。"""
    events: list[dict] = []

    async def _fake_event(session, **kwargs):
        events.append(kwargs)
        return None

    monkeypatch.setattr(works, "append_event", _fake_event)

    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=3)
        await _chapter_runs(s, work, 3)
        # 第 1 章埋下关于「甲」的承诺，第 3 章又写了一次——依赖覆盖完整
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=3, facts=[_fact("甲再度许诺")], cast=["甲"],
        )
        await s.commit()
        response = await works.report_fact(
            s,
            owner_id=work.owner_id,
            work_id=work.id,
            payload=ReportFactRequest(
                statement="甲其实没有钥匙", chapter_no=1, subject="甲"
            ),
        )
        await s.commit()

    assert response.accepted
    assert response.replay_scope in ("dependency_subgraph", "conservative_batch")
    assert 1 in response.affected_chapters, f"第 1 章没有被排进重演：{response.affected_chapters}"

    async with novel_db() as s:
        runs = list(
            (
                await s.scalars(
                    select(ChapterRunModel).where(
                        ChapterRunModel.work_id == work.id,
                        ChapterRunModel.chapter_no == 1,
                    )
                )
            ).all()
        )
    assert sorted(int(r.attempt) for r in runs) == [1, 2], "重演没有真的创建新 attempt"


@pytest.mark.asyncio
async def test_reported_fact_without_coverage_replays_conservatively(novel_db, monkeypatch):
    """依赖覆盖缺失时，纠错必须保守重做后续章，并如实说明（B-02）。"""
    events: list[dict] = []

    async def _fake_event(session, **kwargs):
        events.append(kwargs)
        return None

    monkeypatch.setattr(works, "append_event", _fake_event)

    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=3)
        await _chapter_runs(s, work, 3)
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        # 抹掉全部覆盖记录：此时无法区分「独立」与「漏记」
        await s.execute(delete(MemoryEdgeModel))
        await s.commit()
        response = await works.report_fact(
            s,
            owner_id=work.owner_id,
            work_id=work.id,
            payload=ReportFactRequest(
                statement="甲其实没有钥匙", chapter_no=2, subject="甲"
            ),
        )
        await s.commit()

    assert response.replay_scope == "conservative_batch"
    assert response.affected_chapters, "保守重做也没有排任何章"


@pytest.mark.asyncio
async def test_path_update_still_uses_the_same_entry(novel_db, monkeypatch):
    """改路径入口仍走按依赖范围失效，不被本次改动打断（回归保护）。"""
    async def _noop(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop)

    async with novel_db() as s:
        work = await _work(s)
        path = CriticalPathModel(id=uuid.uuid4(), work_id=work.id, version=1)
        s.add(path)
        await s.flush()
        for ordinal in range(1, works.MIN_PATH_NODES + 1):
            s.add(
                CriticalNodeModel(
                    id=uuid.uuid4(), path_id=path.id, node_id=f"n{ordinal}",
                    ordinal=ordinal, title=f"节点{ordinal}",
                )
            )
        await s.commit()
        _out, impact = await works.update_critical_path(
            s,
            owner_id=work.owner_id,
            work_id=work.id,
            payload=CriticalPathUpdate(
                nodes=[
                    CriticalNode(
                        node_id=f"m{ordinal}", ordinal=ordinal, title=f"改后{ordinal}"
                    )
                    for ordinal in range(1, works.MIN_PATH_NODES + 1)
                ],
                expected_version=1,
                change_note="换方向",
            ),
        )
        await s.commit()
    assert impact is not None
