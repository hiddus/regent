"""A-04：正式事实必须真的写进长期记忆（Plan v6.9 第二批）。

审计反例：`VerifiedFact` 只有 statement/quote/known_by/entities，分类器不认；
即使手工补上 memory_kind，`_content_of` 也不读 statement，结果仍是 0 条记忆。
现有记忆测试用的是另一套人工字段，不能证明正常章节会写入记忆。

验收要求：用正式核验输出生成至少一条记忆，下一章实际召回并验证来源。
"""

# Chinese fixture prose deliberately uses full-width punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

import uuid

import pytest
from regent.novel.application import direction as d
from regent.novel.application import memory as memory_app
from regent.novel.application import works
from regent.novel.domain import memory as domain
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    CriticalNodeModel,
    CriticalPathModel,
    MemoryItemModel,
    NovelPrincipalModel,
    PersonaSpecModel,
    StoryGoalModel,
    StoryWorkModel,
)
from sqlalchemy import select

from test_direction import Provider, TEXT, brief, prose_decision, resolution, take_decision  # noqa: E402
from test_last_node_and_volume import _Provider, _run_chapter  # noqa: E402

QUOTE = "他把钥匙放在桌上。"


def _validation() -> d.SceneValidation:
    """正式核验输出：只有 statement / quote / known_by / entities，外加可选标注。"""
    return d.SceneValidation(
        passed=True,
        facts=[
            d.VerifiedFact(
                statement="钥匙只能交给掌灯人",
                quote=QUOTE,
                known_by=["ALL"],
                entities=["钥匙"],
                memory_kind="rule",
                subject="旧宅规矩",
            ),
            d.VerifiedFact(
                statement="他第一次主动把钥匙交给别人",
                quote=QUOTE,
                known_by=["主角"],
                entities=["钥匙"],
            ),
            d.VerifiedFact(
                statement="两个人第一次互不设防",
                quote=QUOTE,
                known_by=["主角", "同伴"],
                entities=[],
            ),
        ],
        state_changes=[d.VerifiedStateChange(key="key", value="桌上", quote=QUOTE)],
    )


def _chapter_outputs(node_completed: bool) -> list:
    return [
        d.ChapterDirection(
            title="交付",
            reader_intent="为信任担心",
            ending_reason="交付已成立",
            scenes=[brief()],
        ),
        d.ActorTurn(intention="信任", actions=["伸手"], private_reasoning="PRIVATE"),
        resolution(),
        take_decision(),
        d.SceneText(content=TEXT),
        prose_decision(),
        _validation(),
        d.ChapterValidation(
            passed=True, node_completed=node_completed, completion_quote=QUOTE
        ),
    ]


async def _noop_event(session, **kwargs):
    return None


async def _boot(sessions):
    owner, work_id = uuid.uuid4(), uuid.uuid4()
    async with sessions() as session:
        session.add(NovelPrincipalModel(id=owner, subject=f"a04:{owner}"))
        session.add(
            StoryWorkModel(id=work_id, owner_id=owner, state="READY", genre="悬疑")
        )
        session.add(
            StoryGoalModel(id=uuid.uuid4(), work_id=work_id, raw_intent="信任的代价")
        )
        for name in ("主角", "同伴"):
            session.add(
                PersonaSpecModel(
                    id=uuid.uuid4(),
                    work_id=work_id,
                    name=name,
                    voice={"style": "克制"},
                )
            )
        path_id = uuid.uuid4()
        session.add(CriticalPathModel(id=path_id, work_id=work_id, node_count=2))
        for ordinal in (1, 2):
            session.add(
                CriticalNodeModel(
                    id=uuid.uuid4(),
                    path_id=path_id,
                    node_id=f"n{ordinal}",
                    ordinal=ordinal,
                    title=f"节点{ordinal}",
                    promise=f"节点{ordinal}承诺",
                )
            )
        await session.commit()
    return owner, work_id


async def _rows(sessions) -> dict[str, MemoryItemModel]:
    async with sessions() as session:
        found = (await session.scalars(select(MemoryItemModel))).all()
        return {item.item_key: item for item in found}


async def test_formal_facts_produce_all_four_memory_kinds(novel_db, monkeypatch):
    """正式核验输出必须写出规则/弧线/关系/承诺，而不是 0 条。"""
    monkeypatch.setattr(works, "append_event", _noop_event)
    provider = _Provider(_chapter_outputs(True) + _chapter_outputs(False))
    owner, work_id = await _boot(novel_db)

    async with novel_db() as session:
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()
    await _run_chapter(novel_db, provider, owner, work_id, 1)

    rows = await _rows(novel_db)
    assert rows, "正常章节一条记忆都没有写入"
    by_kind = {row.kind for row in rows.values()}
    assert by_kind == {"rule", "character_arc", "promise", "relation"}, (
        f"记忆种类不全：{sorted((r.kind, r.item_key, r.content) for r in rows.values())}"
    )

    rule = rows["rule:旧宅规矩"]
    assert rule.content == "钥匙只能交给掌灯人", "规则内容不是正式事实的 statement"
    assert int(rule.source_chapter_no) == 1
    assert rule.source_hash, "记忆没有来源凭证"

    arc = rows["character_arc:主角"]
    assert arc.content == "他第一次主动把钥匙交给别人"
    assert arc.subject == "主角"

    relation = rows["relation:主角↔同伴"]
    assert relation.content == "两个人第一次互不设防"

    promise = rows[domain.item_key("promise", "节点1", "节点1承诺")]
    assert promise.content == "节点1承诺"
    assert promise.state == "RESOLVED", "节点已完成，承诺应已兑现"


async def test_next_chapter_recalls_memory_with_source(novel_db, monkeypatch):
    """下一章必须真的召回上一章的记忆，并带可核对的来源摘要。"""
    monkeypatch.setattr(works, "append_event", _noop_event)
    provider = _Provider(_chapter_outputs(True) + _chapter_outputs(False))
    owner, work_id = await _boot(novel_db)

    async with novel_db() as session:
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()
    await _run_chapter(novel_db, provider, owner, work_id, 1)
    async with novel_db() as session:
        await works.start_run(session, owner_id=owner, work_id=work_id)
        await session.commit()
    # 装配阶段就把记忆读进上下文；跑完整章只是为了让这一步真实发生
    for _ in range(6):
        async with novel_db() as session:
            await works.advance_step(
                session,
                provider=provider,
                owner_id=owner,
                work_id=work_id,
                chapter_no=2,
            )
            await session.commit()
        async with novel_db() as session:
            run = await session.scalar(
                select(ChapterRunModel).where(
                    ChapterRunModel.work_id == work_id, ChapterRunModel.chapter_no == 2
                )
            )
            if (run.generation_context or {}).get("memory"):
                break

    context = run.generation_context or {}
    assert context.get("memory_source_hash"), "召回没有留下来源摘要"
    recalled = {row["key"]: row for row in context["memory"]}
    for key in ("rule:旧宅规矩", "character_arc:主角", "relation:主角↔同伴"):
        assert key in recalled, f"下一章没有召回 {key}：{sorted(recalled)}"
        assert int(recalled[key]["source_chapter_no"]) == 1, "召回条目的来源章不对"

    async with novel_db() as session:
        work = await session.get(StoryWorkModel, work_id)
        bundle = await memory_app.recall_memory(session, work=work, chapter_no=2)
    assert {item.key for item in bundle.items} >= {
        "rule:旧宅规矩",
        "character_arc:主角",
        "relation:主角↔同伴",
    }
    assert bundle.source_hash == context["memory_source_hash"], "召回结果不可复现"
