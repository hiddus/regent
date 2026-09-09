"""D-01～D-04 闭环缺口的验收（复核报告 v7.7）。

命题全部指向「实现退回旧行为就会失败」的具体断言：

- D-01：纠错内容必须穿过 ASSEMBLE 重建；不同报错不得因已有排队任务而静默丢失。
- D-02：后台领取有依赖屏障——更早章节在途时，QUEUED 的新章不得起跑。
- D-03：六视角记忆投影接入 director_v2 场景链（plan/ACT/WATCH/RENDER）。
- D-04：终局裁决受章级货币上限约束，预算耗尽在调用 provider 前拒绝。

Chinese fixture prose deliberately uses full-width punctuation.
ruff: noqa: RUF001
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import direction as d
from regent.novel.application import generation, production, works
from regent.novel.application import memory as memory_app
from regent.novel.domain import memory as memory_domain
from regent.novel.domain.context import (
    compile_actor_context,
    compile_director_performance_context,
    compile_writer_context,
)
from regent.novel.domain.errors import ProductionStopped
from regent.novel.domain.models import ReportFactRequest
from regent.novel.domain.states import ChapterRunState
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    CriticalNodeModel,
    CriticalPathModel,
    NovelPrincipalModel,
    StoryGoalModel,
    StoryWorkModel,
)
from sqlalchemy import select

MAX_COST_MINOR = 20_000
_OLD = datetime(2000, 1, 1, tzinfo=UTC)
_NEW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 公共小夹具
# ---------------------------------------------------------------------------


def _fact(statement: str, **extra) -> dict:
    return {"statement": statement, "quote": statement, "known_by": ["甲"], **extra}


async def _work(session, *, latest_chapter_no: int = 0, state: str = "RUNNING"):
    owner = uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject=f"c:{owner}"))
    work = StoryWorkModel(
        id=uuid.uuid4(),
        owner_id=owner,
        state=state,
        genre="悬疑",
        latest_chapter_no=latest_chapter_no,
    )
    session.add(work)
    await session.flush()
    return work


async def _goal(session, work) -> None:
    session.add(
        StoryGoalModel(
            id=uuid.uuid4(),
            work_id=work.id,
            raw_intent="一个关于钥匙的秘密",
            normalized_goal="钥匙的秘密要讲完",
            version=1,
        )
    )


async def _path_with_nodes(session, work, node_ids=("n1", "n2", "n3")) -> None:
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


def _run(work, *, chapter_no: int, state: str, context: dict | None = None,
         attempt: int = 1) -> ChapterRunModel:
    return ChapterRunModel(
        id=uuid.uuid4(),
        work_id=work.id,
        branch_id=work.branch_id,
        chapter_no=chapter_no,
        attempt=attempt,
        state=state,
        updated_at=_NEW,
        generation_context=context or {},
    )


async def _chapter_runs(session, work: StoryWorkModel, count: int) -> None:
    for chapter_no in range(1, count + 1):
        session.add(_run(work, chapter_no=chapter_no, state=ChapterRunState.CANONIZED.value))
    await session.flush()


# ---------------------------------------------------------------------------
# D-01：纠错内容穿过 ASSEMBLE 重建
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assemble_preserves_correction_through_rebuild(novel_db, monkeypatch):
    """排队时保存的 correction，ASSEMBLE 重建上下文后必须还在（D-01）。"""

    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)

    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=3)
        await _goal(s, work)
        await _path_with_nodes(s, work)
        s.add(_run(work, chapter_no=1, state=ChapterRunState.CANONIZED.value))
        replay = _run(
            work,
            chapter_no=1,
            state=ChapterRunState.QUEUED.value,
            attempt=2,
            context={
                "architecture_version": "director_v2",
                "correction": {
                    "ticket_id": "t-1",
                    "statement": "甲其实从未持有钥匙",
                    "subject": "甲",
                    "reported_chapter_no": 1,
                },
                "replay_reason": "fact_reported",
            },
        )
        s.add(replay)
        await s.commit()

        await generation.assemble(s, work=work, run=replay)
        await s.commit()

    ctx = dict(replay.generation_context or {})
    assert (ctx.get("correction") or {}).get("statement") == "甲其实从未持有钥匙", (
        f"ASSEMBLE 重建把纠错内容清掉了，重演变成「不知道要改什么」的普通重跑：{sorted(ctx)}"
    )
    assert ctx.get("replay_reason") == "fact_reported"


@pytest.mark.asyncio
async def test_different_correction_merges_into_queued_replay(novel_db, monkeypatch):
    """已有排队重演时，**不同**的新报错必须合并进队列，不得静默丢弃（D-01）。"""

    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)

    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=3)
        await _chapter_runs(s, work, 3)
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        await works.report_fact(
            s, owner_id=work.owner_id, work_id=work.id,
            payload=ReportFactRequest(statement="甲其实从未持有钥匙", chapter_no=1, subject="甲"),
        )
        await s.commit()
        # 同一主体、**不同**的报错：第二条必须合并进已排队的那次重演
        await works.report_fact(
            s, owner_id=work.owner_id, work_id=work.id,
            payload=ReportFactRequest(statement="甲当晚根本不在场", chapter_no=1, subject="甲"),
        )
        await s.commit()

        replays = list(
            (
                await s.scalars(
                    select(ChapterRunModel).where(
                        ChapterRunModel.work_id == work.id,
                        ChapterRunModel.chapter_no == 1,
                        ChapterRunModel.attempt > 1,
                        ChapterRunModel.state == ChapterRunState.QUEUED.value,
                    )
                )
            ).all()
        )
        attempts = sorted(
            int(r.attempt)
            for r in (
                await s.scalars(
                    select(ChapterRunModel).where(
                        ChapterRunModel.work_id == work.id,
                        ChapterRunModel.chapter_no == 1,
                    )
                )
            ).all()
        )

    assert attempts == [1, 2], f"不同报错叠出了多个任务：{attempts}"
    assert len(replays) == 1
    ctx = dict(replays[0].generation_context or {})
    statements = {str(c.get("statement", "")) for c in (ctx.get("corrections") or [])}
    assert statements == {"甲其实从未持有钥匙", "甲当晚根本不在场"}, (
        f"新报错被静默丢弃，重演只知道第一次的错：{ctx}"
    )
    # 当前纠错语义指向最新一次报错
    assert (ctx.get("correction") or {}).get("statement") == "甲当晚根本不在场"


# ---------------------------------------------------------------------------
# D-02：后台领取的依赖屏障
# ---------------------------------------------------------------------------


def _install_advance_step_spy(monkeypatch) -> list[int]:
    claimed: list[int] = []

    async def fake_advance_step(session, *, provider, owner_id, work_id, chapter_no):
        claimed.append(int(chapter_no))
        return None

    monkeypatch.setattr(works, "advance_step", fake_advance_step)
    return claimed


@pytest.mark.asyncio
async def test_barrier_skips_later_chapter_and_resumes_the_earlier_one(
    novel_db, monkeypatch
):
    """第一章在途（无租约）时，第二章即使 updated_at 更旧也不得越过；worker 转而续跑第一章。"""
    claimed = _install_advance_step_spy(monkeypatch)

    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=2)
        ch1 = _run(work, chapter_no=1, state=ChapterRunState.RUNNING.value)
        ch1.lease_expires_at = None  # 无租约：可续跑，但第二章仍被屏障挡住
        ch2 = _run(
            work,
            chapter_no=2,
            state=ChapterRunState.QUEUED.value,
            context={"architecture_version": "director_v2"},
            attempt=1,
        )
        ch2.updated_at = _OLD  # 旧行为按 updated_at 排序：退回旧实现必然先领它
        s.add_all([ch1, ch2])
        await s.commit()

        await works.advance_background_run(s, provider=object())
        await s.commit()

    assert claimed == [1], f"必须跳过第二章、续跑第一章，实际推进了 {claimed}"


@pytest.mark.asyncio
async def test_barrier_holds_while_earlier_chapter_lease_is_live(novel_db, monkeypatch):
    """第一章租约在期（另一个 worker 在跑）时，本 worker 什么都不领（D-02 / 双 worker）。"""
    claimed = _install_advance_step_spy(monkeypatch)

    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=2)
        ch1 = _run(work, chapter_no=1, state=ChapterRunState.RUNNING.value)
        ch1.lease_expires_at = datetime(2099, 1, 1, tzinfo=UTC)  # 租约在期
        ch2 = _run(
            work, chapter_no=2, state=ChapterRunState.QUEUED.value,
            context={"architecture_version": "director_v2"},
        )
        ch2.updated_at = _OLD
        s.add_all([ch1, ch2])
        await s.commit()

        out = await works.advance_background_run(s, provider=object())
        await s.commit()

    assert out is None
    assert claimed == [], f"租约在期的第一章应挡住一切，实际推进了 {claimed}"


@pytest.mark.asyncio
async def test_background_claims_earlier_queued_chapter_first(novel_db, monkeypatch):
    """两章都 QUEUED 时必须先领第一章，即使第二章 updated_at 更旧（D-02）。"""
    claimed = _install_advance_step_spy(monkeypatch)

    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=2)
        ch1 = _run(
            work, chapter_no=1, state=ChapterRunState.QUEUED.value,
            context={"architecture_version": "director_v2"},
        )
        ch2 = _run(
            work, chapter_no=2, state=ChapterRunState.QUEUED.value,
            context={"architecture_version": "director_v2"},
        )
        ch2.updated_at = _OLD
        s.add_all([ch1, ch2])
        await s.commit()

        await works.advance_background_run(s, provider=object())
        await s.commit()

    assert claimed == [1], f"必须先推进第一章，实际推进了 {claimed}"


@pytest.mark.asyncio
async def test_barrier_releases_when_earlier_chapter_is_final(novel_db, monkeypatch):
    """第一章已终态（CANONIZED）后，屏障放行第二章（D-02）。"""
    claimed = _install_advance_step_spy(monkeypatch)

    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=2)
        ch1 = _run(work, chapter_no=1, state=ChapterRunState.CANONIZED.value)
        ch2 = _run(
            work, chapter_no=2, state=ChapterRunState.QUEUED.value,
            context={"architecture_version": "director_v2"},
        )
        s.add_all([ch1, ch2])
        await s.commit()

        await works.advance_background_run(s, provider=object())
        await s.commit()

    assert claimed == [2], f"第一章已定典，第二章应被领取，实际推进了 {claimed}"


@pytest.mark.asyncio
async def test_barrier_blocks_on_pending_decision_of_earlier_chapter(novel_db, monkeypatch):
    """第一章停在 PENDING_DECISION（人在回路）时，第二章同样不得起跑（D-02）。"""
    claimed = _install_advance_step_spy(monkeypatch)

    async with novel_db() as s:
        work = await _work(s, latest_chapter_no=2)
        ch1 = _run(work, chapter_no=1, state=ChapterRunState.PENDING_DECISION.value)
        ch2 = _run(
            work, chapter_no=2, state=ChapterRunState.QUEUED.value,
            context={"architecture_version": "director_v2"},
        )
        ch2.updated_at = _OLD
        s.add_all([ch1, ch2])
        await s.commit()

        out = await works.advance_background_run(s, provider=object())
        await s.commit()

    assert out is None
    assert claimed == [], "等待用户裁决的第一章必须挡住第二章起跑"


# ---------------------------------------------------------------------------
# D-03：记忆投影接入 director_v2 场景链
# ---------------------------------------------------------------------------


def _memory_payloads() -> list[dict]:
    return [
        {"kind": "rule", "content": "世界规则：满月时门会打开", "entities": []},
        {"kind": "promise", "content": "甲承诺归还钥匙", "entities": ["甲"], "subject": "甲"},
        {"kind": "belief", "content": "甲误信乙已离开", "entities": ["甲"], "subject": "甲"},
        {"kind": "reader_knowledge", "content": "读者已知道钥匙在抽屉", "entities": []},
        {"kind": "director_note", "content": "导演笔记：下章铺垫丙", "entities": []},
    ]


def test_projection_views_stay_enforced():
    """六视角边界在 domain 实现里保持：导演笔记不进角色/叙述者。"""
    payloads = _memory_payloads()
    character_view = memory_domain.project_payloads(payloads, "character", "甲")
    kinds = {p["kind"] for p in character_view}
    assert kinds == {"rule", "promise", "belief"}, f"角色视图越界：{kinds}"
    narrator_view = memory_domain.project_payloads(payloads, "narrator")
    assert "director_note" not in {p["kind"] for p in narrator_view}
    director_view = memory_domain.project_payloads(payloads, "director")
    assert {p["kind"] for p in director_view} == {
        "rule", "promise", "belief", "reader_knowledge", "director_note",
    }


def test_actor_context_carries_only_its_own_memory():
    """ACT 装配：角色 payload 只含本人视角记忆，且 manifest 留痕（D-03）。"""
    compiled = compile_actor_context(
        work_id=uuid.uuid4(), branch_id=uuid.uuid4(), chapter_no=1,
        scene_index=0, take_no=1, beat=0, persona="甲",
        cast={"甲": {"identity": {}, "drives": {}, "voice": {}}},
        direction={"persona": "甲"}, setting="旧宅",
        canon=[], observations=[], turn=0,
        memory=memory_domain.project_payloads(_memory_payloads(), "character", "甲"),
    )
    kinds = {p["kind"] for p in compiled.payload["character_memory"]}
    assert kinds == {"rule", "promise", "belief"}, (
        f"角色 payload 混入了他人/读者/导演的记忆：{kinds}"
    )
    assert any(src.kind == "memory" for src in compiled.manifest.sources)


def test_writer_context_carries_narrator_memory_only():
    """RENDER 装配：正文材料拿叙述者视图，导演笔记不进（D-03）。"""
    compiled = compile_writer_context(
        work_id=uuid.uuid4(), branch_id=uuid.uuid4(), chapter_no=1,
        scene_index=0, take_no=1, narrative={}, events=[], voices={},
        previous_ending="", target_characters=800,
        director_instruction="", memory=memory_domain.project_payloads(
            _memory_payloads(), "narrator"
        ),
    )
    kinds = {p["kind"] for p in compiled.payload["narrator_memory"]}
    assert "director_note" not in kinds, f"导演笔记进了正文材料：{kinds}"
    assert any(src.kind == "memory" for src in compiled.manifest.sources)


def test_director_context_carries_director_memory():
    """WATCH 装配：导演观看表演时带导演视图（含未兑现承诺与导演笔记）（D-03）。"""
    compiled = compile_director_performance_context(
        work_id=uuid.uuid4(), branch_id=uuid.uuid4(), chapter_no=1,
        scene_index=0, take_no=1, brief={}, events=[], performances=[],
        rule_issues=[], remaining_turns=3,
        memory=memory_domain.project_payloads(_memory_payloads(), "director"),
    )
    kinds = {p["kind"] for p in compiled.payload["director_memory"]}
    assert "director_note" in kinds and "promise" in kinds


class _Provider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def generate_structured(self, *, response_model, **kwargs):
        self.requests.append({"schema": response_model, **kwargs})
        output = self.outputs.pop(0)
        assert isinstance(output, response_model), (output, response_model)
        return StructuredModelResponse(output=output, usage=ModelUsage(10, 20), model="test")


class _Session:
    async def scalar(self, query):
        return None

    async def scalars(self, query):
        return SimpleNamespace(
            all=lambda: [
                SimpleNamespace(
                    name="主角", identity={}, drives={}, voice={"style": "克制"},
                )
            ]
        )

    def add(self, row):
        pass

    async def flush(self):
        pass

    async def commit(self):
        pass


def _directed_run(memory_payloads: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        chapter_no=1,
        generation_context={
            "architecture_version": d.ARCHITECTURE,
            "canon": [],
            "memory": memory_payloads,
            "actual_state": {},
            "target_node": {},
            "recent_chapters": [],
            "production": {},
        },
        performances=[],
        review={},
        user_guidance={},
        current_step="DIRECT",
        content="",
        title="",
        word_count=0,
    )


def _plan_output() -> d.ChapterDirection:
    return d.ChapterDirection(
        title="交付", reader_intent="为信任担心", ending_reason="交付已成立",
        scenes=[d.SceneBrief(
            purpose="建立不对等信任", setting="旧宅", conflict="信任与试探",
            exit_condition="主角交出钥匙",
            actors=[d.ActorDirection(persona="主角", objective="取得信任", instruction="等")],
            narrative=d.NarrativeSpec(
                viewpoint="主角", distance="近", style="克制",
                reader_effect="担忧", disclosure_rule="不揭露同伴秘密",
            ),
        )],
    )


def test_plan_request_gets_director_view_not_raw_memory():
    """plan_chapter 的导演请求：拿导演视图记忆，不再全量倾倒 raw memory（D-03）。"""
    payloads = _memory_payloads()
    provider = _Provider([_plan_output()])
    session, run = _Session(), _directed_run(payloads)
    work = SimpleNamespace(id=uuid.uuid4())

    asyncio.run(d.plan_chapter(session, provider=provider, work=work, run=run))

    plan_requests = [r for r in provider.requests if r["schema"] is d.ChapterDirection]
    assert plan_requests, "导演计划请求没有发生"
    payload = json.loads(plan_requests[0]["user_prompt"])
    assert "director_memory" in payload, "导演计划请求没有带导演视图记忆"
    assert "memory" not in (payload.get("context") or {}), (
        "导演计划请求仍在全量倾倒未投影的召回记忆"
    )
    sent = {p["kind"] for p in payload["director_memory"]}
    assert sent == {"rule", "promise", "belief", "reader_knowledge", "director_note"}


@pytest.mark.asyncio
async def test_director_v2_requests_carry_projected_memory(novel_db):
    """真实请求级证明（D-03 验收）：plan/ACT/WATCH/RENDER 各拿到自己的投影。"""
    from test_direction import (
        Provider,
        Session,
        run_object,
        successful_outputs,
        tick_to_done,
    )

    payloads = _memory_payloads() + [
        {"kind": "promise", "content": "主角承诺护送丙", "entities": ["主角"], "subject": "主角"},
    ]
    provider = Provider(successful_outputs())
    session, run = Session(), run_object()
    run.generation_context["memory"] = payloads
    work = SimpleNamespace(id=uuid.uuid4())

    await d.plan_chapter(session, provider=provider, work=work, run=run)
    await tick_to_done(session, provider, work, run)

    def payload_of(schema):
        return json.loads(
            next(r["user_prompt"] for r in provider.requests if r["schema"] is schema)
        )

    # 导演计划：导演视图全量六类；raw memory 不再随 context 倾倒
    plan = payload_of(d.ChapterDirection)
    assert "memory" not in (plan.get("context") or {})
    assert {p["kind"] for p in plan["director_memory"]} == {
        "rule", "promise", "belief", "reader_knowledge", "director_note",
    }
    # ACT：主角只拿他自己的投影（rule + 与他相关的 promise），没有读者认知、
    # 导演笔记、他人误信
    actor = payload_of(d.ActorTurn)
    actor_kinds = {p["kind"] for p in actor["character_memory"]}
    assert actor_kinds == {"rule", "promise"}, f"角色视图越界：{actor_kinds}"
    # WATCH_TAKE：导演观看表演带导演视图
    watch = payload_of(d.TakeDirection)
    assert {p["kind"] for p in watch["director_memory"]} == {
        "rule", "promise", "belief", "reader_knowledge", "director_note",
    }
    # RENDER：正文材料只拿叙述者视图——导演笔记与人物误信不进正文
    prose = payload_of(d.SceneText)
    prose_kinds = {p["kind"] for p in prose["narrator_memory"]}
    assert "director_note" not in prose_kinds and "belief" not in prose_kinds, prose_kinds
    # WATCH_PROSE：导演审阅正文同样带导演视图
    prose_watch = payload_of(d.ProseDirection)
    assert "director_note" in {p["kind"] for p in prose_watch["director_memory"]}


# ---------------------------------------------------------------------------
# D-04：终局裁决的预算上限
# ---------------------------------------------------------------------------


def test_ending_verdict_rejected_before_provider_call_when_budget_exhausted():
    """本章预算耗尽时，终局判断在调用 provider 前拒绝（D-04）。"""
    provider = _Provider([])
    run = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_no=3,
        generation_context={"production": {"committed_minor": MAX_COST_MINOR}},
    )
    work = SimpleNamespace(id=uuid.uuid4())

    with pytest.raises(ProductionStopped):
        asyncio.run(
            generation.generate_ending_verdict(
                provider,
                session=_Session(),
                work=work,
                run=run,
                raw_intent="一个关于钥匙的秘密",
                genre="悬疑",
                ending_statement="",
                volume_no=1,
                latest_chapter_no=3,
                completed_nodes=[],
            )
        )
    assert provider.requests == [], "预算耗尽仍发起了模型调用"


def test_ending_verdict_passes_remaining_budget_to_broker(monkeypatch):
    """终局判断把「章级上限 − 已结算」作为预算上限传给 CallBroker（D-04）。"""
    captured: dict = {}
    real_broker = production.CallBroker

    class _SpyBroker(real_broker):
        def __init__(self, *args, **kwargs):
            captured["budget_limit_minor"] = kwargs.get("budget_limit_minor")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(production, "CallBroker", _SpyBroker)

    committed = 15_000
    provider = _Provider([])  # 无输出：调用会失败，但我们只关心预算在调用前已传递
    run = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_no=3,
        generation_context={"production": {"committed_minor": committed}},
    )
    work = SimpleNamespace(id=uuid.uuid4())

    with pytest.raises(Exception):
        asyncio.run(
            generation.generate_ending_verdict(
                provider,
                session=_Session(),
                work=work,
                run=run,
                raw_intent="x",
                genre="悬疑",
                ending_statement="",
                volume_no=1,
                latest_chapter_no=3,
                completed_nodes=[],
            )
        )

    assert captured["budget_limit_minor"] == MAX_COST_MINOR - committed, (
        f"预算上限没有按剩余额度传递：{captured}"
    )
