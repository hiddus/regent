"""B-04 双策略灰度的验收：两个真实可执行且固定版本的策略。

命题指向「实现退回旧行为就会失败」的具体断言：

- 注册：KNOWN_EXECUTORS 含 director_v2 与 legacy_v1 两个**真实**策略，各有
  固定版本号；两策略步骤链不同且都可整章执行。
- 隔离：灰度臂（legacy_v1）能端到端产出定典章节；重演按作品桶钉住执行器，
  不再硬编码 director_v2；executor_version 活过 ASSEMBLE 重建。
- 回退：灰度旋钮撤除后新运行落回 stable；在途运行不换臂，留下
  ``executor.switch_deferred`` 记录并继续跑完（不许切换 ≠ 停摆）。

Chinese fixture prose deliberately uses full-width punctuation.
ruff: noqa: RUF001
"""

from __future__ import annotations

import uuid

import pytest
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import executor as executor_app
from regent.novel.application import works
from regent.novel.application import memory as memory_app
from regent.novel.application.generation import (
    CanonExtraction,
    ChapterDraft,
    ChapterReview,
    DirectorPlan,
    CharacterAction,
    Performance,
)
from regent.novel.application.direction import ARCHITECTURE
from regent.novel.domain.states import ChapterRunState, ChapterStep, chapter_step_order
from regent.novel.domain.models import ReportFactRequest
from regent.novel.infrastructure.models import (
    CanonCommitModel,
    ChapterRunModel,
    CriticalNodeModel,
    CriticalPathModel,
    NovelPrincipalModel,
    PersonaSpecModel,
    StoryGoalModel,
    StoryWorkModel,
)
from sqlalchemy import select

TEXT = "他把钥匙放在桌上。" + "雨水沿窗棂流下，两个人仍旧没有开口。" * 65


class Provider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def generate_structured(self, *, response_model, **kwargs):
        self.requests.append({"schema": response_model, **kwargs})
        output = self.outputs.pop(0)
        assert isinstance(output, response_model), (output, response_model)
        return StructuredModelResponse(output=output, usage=ModelUsage(10, 20), model="test")


def _fact(statement: str, **extra) -> dict:
    return {"statement": statement, "quote": statement, "known_by": ["甲"], **extra}


def _legacy_outputs() -> list:
    """legacy 六步链的全部模型输出：PERFORM×N、DIRECT、WEAVE、REVIEW、CANON。"""
    return [
        Performance(
            persona="主角", immediate_goal="拿回钥匙", private_reasoning="PRIVATE_THOUGHT",
            actions=["伸手"], dialogue=["拿着吧"], emotional_shift="紧张",
        ),
        DirectorPlan(
            scene_goal="建立不对等信任",
            character_actions=[
                CharacterAction(persona="主角", scene_actions=["把钥匙放在桌上"],
                                key_dialogue=["拿着吧"], emotional_arc="试探到松手")
            ],
            beats=["进门", "对峙", "放手"],
            state_before={"place": "旧宅", "trust": "低"},
            state_after={"place": "河堤", "trust": "中"},
            ending_hook="雨还没停",
        ),
        ChapterDraft(title="交付", content=TEXT),
        ChapterReview(passed=True),
        CanonExtraction(facts=[
            {"statement": "钥匙放在桌上", "entities": ["钥匙"], "known_by": ["ALL"],
             "confidence": "high", "quote": "他把钥匙放在桌上。"},
        ]),
    ]


async def _seed_work(session, *, work_id=None, latest_chapter_no: int = 0):
    owner = uuid.uuid4()
    work_id = work_id or uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject=f"c:{owner}"))
    work = StoryWorkModel(
        id=work_id, owner_id=owner, state="READY", genre="悬疑",
        latest_chapter_no=latest_chapter_no,
    )
    session.add(work)
    session.add(StoryGoalModel(id=uuid.uuid4(), work_id=work_id, raw_intent="信任的代价"))
    return owner, work_id


@pytest.mark.asyncio
async def test_canary_arm_produces_a_full_chapter_end_to_end(novel_db, monkeypatch):
    """灰度臂 legacy_v1 是**真实可执行**的：整章六步走完并定典（B-04）。"""
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY", "legacy_v1")
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY_PERCENT", "100")

    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)
    provider = Provider(_legacy_outputs())

    async with novel_db() as s:
        owner, work_id = await _seed_work(s)
        s.add(PersonaSpecModel(id=uuid.uuid4(), work_id=work_id, name="主角"))
        await s.commit()
        await works.start_run(s, owner_id=owner, work_id=work_id)
        await s.commit()
        run = await s.scalar(select(ChapterRunModel))
        assert run is not None
        assert run.generation_context["executor"] == "legacy_v1"
        assert run.generation_context["executor_version"] == "legacy_v1@1"
        # legacy 臂走六步链：有 PERFORM/WEAVE，没有 PRODUCE
        steps = {st.value for st in chapter_step_order(run.generation_context)}
        assert {"PERFORM", "WEAVE"} <= steps and "PRODUCE" not in steps

    for _ in range(12):
        async with novel_db() as session:
            progress = await works.advance_background_run(session, provider=provider)
            await session.commit()
            if progress and progress.state.value == "CANONIZED":
                break
    else:
        pytest.fail("legacy 臂没有跑完整章")

    async with novel_db() as session:
        run = await session.scalar(select(ChapterRunModel))
        assert run.content == TEXT
        assert run.generation_context["executor"] == "legacy_v1"
        # 版本号活过了 ASSEMBLE 重建（否则盲评无法归因）
        assert run.generation_context["executor_version"] == "legacy_v1@1"
        assert run.review["passed"] is True
        commit = await session.scalar(select(CanonCommitModel))
        assert commit is not None and commit.facts


def test_two_real_strategies_are_registered_and_version_pinned():
    """两个策略都已注册、各有固定版本、步骤链真实不同（B-04 验收口径）。"""
    assert executor_app.KNOWN_EXECUTORS == {ARCHITECTURE, "legacy_v1"}
    assert executor_app.STABLE_EXECUTOR == ARCHITECTURE
    assert executor_app.executor_version(ARCHITECTURE) == "director_v2@1"
    assert executor_app.executor_version("legacy_v1") == "legacy_v1@1"
    # 两臂的步骤链必须真实不同——同一条链谈不上两个策略
    legacy_steps = chapter_step_order({"architecture_version": "legacy_v1"})
    stable_steps = chapter_step_order({"architecture_version": ARCHITECTURE})
    assert legacy_steps != stable_steps
    assert ChapterStep.PERFORM in legacy_steps and ChapterStep.PRODUCE not in legacy_steps
    assert ChapterStep.PRODUCE in stable_steps and ChapterStep.WEAVE not in stable_steps


@pytest.mark.asyncio
async def test_replay_pins_the_work_bucket_executor(novel_db, monkeypatch):
    """纠错重演按作品桶钉住执行器，不再硬编码 director_v2（B-04）。"""
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY", "legacy_v1")
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY_PERCENT", "100")

    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)

    async with novel_db() as s:
        owner = uuid.uuid4()
        s.add(NovelPrincipalModel(id=owner, subject=f"c:{owner}"))
        work = StoryWorkModel(
            id=uuid.uuid4(), owner_id=owner, state="RUNNING", genre="悬疑",
            latest_chapter_no=3,
        )
        s.add(work)
        await s.flush()  # branch_id 等服务端默认值落定后才能建章节运行
        for chapter_no in range(1, 4):
            s.add(ChapterRunModel(
                id=uuid.uuid4(), work_id=work.id, branch_id=work.branch_id,
                chapter_no=chapter_no, attempt=1,
                state=ChapterRunState.CANONIZED.value,
            ))
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1, facts=[_fact("甲承诺明日归还钥匙")], cast=["甲"],
        )
        await s.commit()
        await works.report_fact(
            s, owner_id=owner, work_id=work.id,
            payload=ReportFactRequest(statement="甲其实从未持有钥匙", chapter_no=1, subject="甲"),
        )
        await s.commit()
        replay = await s.scalar(
            select(ChapterRunModel).where(
                ChapterRunModel.work_id == work.id,
                ChapterRunModel.chapter_no == 1,
                ChapterRunModel.attempt == 2,
            )
        )

    assert replay is not None
    ctx = dict(replay.generation_context or {})
    assert ctx["executor"] == "legacy_v1", (
        f"重演被硬编码回 stable，盲评无法归因：{ctx.get('executor')}"
    )
    assert ctx["architecture_version"] == "legacy_v1"
    assert ctx["executor_version"] == "legacy_v1@1"
    assert (ctx.get("correction") or {}).get("statement") == "甲其实从未持有钥匙"


@pytest.mark.asyncio
async def test_rollback_returns_new_runs_to_stable_and_defers_in_flight(
    novel_db, monkeypatch
):
    """回退：新运行落回 stable；在途运行不换臂、留 defer 记录并继续执行（B-04）。"""

    async def _noop_event(session, **kwargs):
        return None

    deferred_events: list[dict] = []

    async def _event(session, **kwargs):
        if kwargs.get("event_type") == "executor.switch_deferred":
            deferred_events.append(kwargs)
        return None

    monkeypatch.setattr(works, "append_event", _event)
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY", "legacy_v1")
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY_PERCENT", "100")

    async with novel_db() as s:
        owner, work_id = await _seed_work(s)
        s.add(PersonaSpecModel(id=uuid.uuid4(), work_id=work_id, name="主角"))
        await s.commit()
        await works.start_run(s, owner_id=owner, work_id=work_id)
        await s.commit()
        first = await s.scalar(select(ChapterRunModel))
        assert first.generation_context["executor"] == "legacy_v1"

    # 回退：撤掉灰度旋钮
    monkeypatch.delenv("NOVEL_EXECUTOR_CANARY")
    monkeypatch.delenv("NOVEL_EXECUTOR_CANARY_PERCENT")

    assert executor_app.choose_executor(work_id) == ARCHITECTURE, "回退后新运行仍进灰度臂"

    # 在途运行：不换臂，留 defer 记录，继续推进
    async with novel_db() as s:
        progress = await works.advance_background_run(s, provider=Provider([]))
        await s.commit()
        run = await s.scalar(select(ChapterRunModel))
        ctx = dict(run.generation_context or {})

    assert ctx.get("executor") == "legacy_v1", "在途运行被换臂"
    assert ctx["architecture_version"] == "legacy_v1"
    assert ctx.get(executor_app.DEFER_MARKER) == {
        "pinned": "legacy_v1",
        "requested": ARCHITECTURE,
        "chapter_no": int(run.chapter_no),
    }
    assert len(deferred_events) == 1, "回退没有留下可查的切换推迟记录"
    assert progress is not None  # 继续执行（ASSEMBLE 已推进），不是停摆
