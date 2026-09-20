"""B-04 双策略灰度：director_v2@2（场景协议）与 director_v2_beat@1（逐节拍对照）。

legacy_v1 已退役。两臂共享导演五步链，差异在 PRODUCE 协议（scene vs beat）。

命题：
- 注册：KNOWN_EXECUTORS 含两真实策略，版本钉死为 @2 / @1。
- 隔离：灰度臂能被钉进 run；重演按作品桶钉住；executor_version 活过 ASSEMBLE。
- 回退：撤灰度后新运行落 stable；在途不换臂并留下 defer 记录。
"""

from __future__ import annotations

import uuid

import pytest
from regent.novel.application import executor as executor_app
from regent.novel.application import works
from regent.novel.application import memory as memory_app
from regent.novel.application.direction import (
    ARCHITECTURE,
    BEAT_ARCHITECTURE,
    PROTOCOL_BEAT,
    PROTOCOL_SCENE,
    SCRIPT_ARCHITECTURE,
    SCRIPT_SCENE_ARCHITECTURE,
    production_protocol,
)
from regent.novel.domain.states import ChapterRunState, ChapterStep, chapter_step_order
from regent.novel.domain.models import ReportFactRequest
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    NovelPrincipalModel,
    PersonaSpecModel,
    StoryGoalModel,
    StoryWorkModel,
)
from sqlalchemy import select


def _fact(statement: str, **extra) -> dict:
    return {"statement": statement, "quote": statement, "known_by": ["甲"], **extra}


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
async def test_canary_arm_pins_beat_protocol(novel_db, monkeypatch):
    """灰度臂 director_v2_beat 被钉进 run，步骤链含 PRODUCE（B-04）。"""
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY", BEAT_ARCHITECTURE)
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY_PERCENT", "100")

    async def _noop_event(session, **kwargs):
        return None

    monkeypatch.setattr(works, "append_event", _noop_event)

    async with novel_db() as s:
        owner, work_id = await _seed_work(s)
        s.add(PersonaSpecModel(id=uuid.uuid4(), work_id=work_id, name="主角"))
        await s.commit()
        await works.start_run(s, owner_id=owner, work_id=work_id)
        await s.commit()
        run = await s.scalar(select(ChapterRunModel))
        assert run is not None
        assert run.generation_context["executor"] == BEAT_ARCHITECTURE
        assert run.generation_context["executor_version"] == "director_v2@1"
        steps = {st.value for st in chapter_step_order(run.generation_context)}
        assert "PRODUCE" in steps and "PERFORM" not in steps
        assert production_protocol(run) == PROTOCOL_BEAT


def test_two_real_strategies_are_registered_and_version_pinned():
    """策略已注册、版本钉死；协议不同（B-04 + script/script_scene 对照臂）。"""
    assert executor_app.KNOWN_EXECUTORS == {
        ARCHITECTURE,
        BEAT_ARCHITECTURE,
        SCRIPT_ARCHITECTURE,
        SCRIPT_SCENE_ARCHITECTURE,
    }
    assert executor_app.STABLE_EXECUTOR == SCRIPT_SCENE_ARCHITECTURE
    assert executor_app.executor_version(ARCHITECTURE) == "director_v2@2"
    assert executor_app.executor_version(BEAT_ARCHITECTURE) == "director_v2@1"
    assert executor_app.executor_version(SCRIPT_ARCHITECTURE) == "director_script@1"
    assert executor_app.executor_version(SCRIPT_SCENE_ARCHITECTURE) == "director_script_scene@1"
    assert BEAT_ARCHITECTURE not in {"legacy_v1"}
    # 步骤链相同（同为导演五步）；差异在 protocol / executor_version
    beat_steps = chapter_step_order({"architecture_version": BEAT_ARCHITECTURE})
    stable_steps = chapter_step_order({"architecture_version": ARCHITECTURE})
    assert beat_steps == stable_steps
    assert ChapterStep.PRODUCE in stable_steps
    assert production_protocol({"architecture_version": ARCHITECTURE}) == PROTOCOL_SCENE
    assert production_protocol(
        type("R", (), {"generation_context": {"architecture_version": BEAT_ARCHITECTURE}})()
    ) == PROTOCOL_BEAT
    assert production_protocol(
        type(
            "R",
            (),
            {"generation_context": {"architecture_version": SCRIPT_ARCHITECTURE}},
        )()
    ) == "script"


@pytest.mark.asyncio
async def test_replay_pins_the_work_bucket_executor(novel_db, monkeypatch):
    """纠错重演按作品桶钉住执行器（B-04）。"""
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY", BEAT_ARCHITECTURE)
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
        await s.flush()
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
    assert ctx["executor"] == BEAT_ARCHITECTURE, (
        f"重演被硬编码回 stable，盲评无法归因：{ctx.get('executor')}"
    )
    assert ctx["architecture_version"] == BEAT_ARCHITECTURE
    assert ctx["executor_version"] == "director_v2@1"
    assert (ctx.get("correction") or {}).get("statement") == "甲其实从未持有钥匙"


@pytest.mark.asyncio
async def test_rollback_returns_new_runs_to_stable_and_defers_in_flight(
    novel_db, monkeypatch
):
    """回退：新运行落回 stable；在途运行不换臂、留 defer 记录并继续执行（B-04）。"""

    deferred_events: list[dict] = []

    async def _event(session, **kwargs):
        if kwargs.get("event_type") == "executor.switch_deferred":
            deferred_events.append(kwargs)
        return None

    from regent.novel.application import works_advance

    monkeypatch.setattr(works_advance, "append_event", _event)
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY", BEAT_ARCHITECTURE)
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY_PERCENT", "100")

    async with novel_db() as s:
        owner, work_id = await _seed_work(s)
        s.add(PersonaSpecModel(id=uuid.uuid4(), work_id=work_id, name="主角"))
        await s.commit()
        await works.start_run(s, owner_id=owner, work_id=work_id)
        await s.commit()
        first = await s.scalar(select(ChapterRunModel))
        assert first.generation_context["executor"] == BEAT_ARCHITECTURE

    monkeypatch.delenv("NOVEL_EXECUTOR_CANARY")
    monkeypatch.delenv("NOVEL_EXECUTOR_CANARY_PERCENT")

    assert executor_app.choose_executor(work_id) == SCRIPT_SCENE_ARCHITECTURE, (
        "回退后新运行仍进灰度臂"
    )

    from regent.model import ModelUsage, StructuredModelResponse

    class EmptyProvider:
        async def generate_structured(self, **kwargs):
            raise AssertionError("ASSEMBLE 不应调模型")

    async with novel_db() as s:
        progress = await works.advance_background_run(s, provider=EmptyProvider())
        await s.commit()
        run = await s.scalar(select(ChapterRunModel))
        ctx = dict(run.generation_context or {})

    assert ctx.get("executor") == BEAT_ARCHITECTURE, "在途运行被换臂"
    assert ctx["architecture_version"] == BEAT_ARCHITECTURE
    assert ctx.get(executor_app.DEFER_MARKER) == {
        "pinned": BEAT_ARCHITECTURE,
        "requested": SCRIPT_SCENE_ARCHITECTURE,
        "chapter_no": int(run.chapter_no),
    }
    assert len(deferred_events) == 1, "回退没有留下可查的切换推迟记录"
    assert progress is not None
