"""BQ-1 / BQ-2：预算可恢复暂停与无效修订止损。

命题：
1. 调用/金额耗尽抛 BudgetExhausted，不清零账本；grant 提高有效上限后可再调。
2. advance_step 遇预算耗尽 → 作品 PAUSED_*、章 run 非 TERMINAL_FAILED、step 可重入。
3. 非预算 ProductionStopped 仍 TERMINAL_FAILED。
4. 硬问题无进展（hash 不变的 repeat，或换措辞仍同题的 open）→ 禁止再 REWRITE。
5. REWRITE/RENDER 前须预留足够后续调用（写作/复看/核验）。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from regent.novel.application import direction as d
from regent.novel.application import works
from regent.novel.domain.errors import BudgetExhausted, CommandRejected, ProductionStopped
from regent.novel.domain.states import ChapterRunState, StoryWorkState, StepState
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    ChapterStepModel,
    NovelPrincipalModel,
    PersonaSpecModel,
    StoryGoalModel,
    StoryWorkModel,
)


def test_validate_reserve_constant_is_pinned():
    assert d.VALIDATE_RESERVE_REWRITE == 4
    assert d.VALIDATE_RESERVE_RENDER == 3
    assert d.VALIDATE_RESERVE_CALLS == d.VALIDATE_RESERVE_REWRITE


@pytest.mark.asyncio
async def test_call_budget_raises_budget_exhausted_and_preserves_count(monkeypatch):
    from regent.novel.application import production as prod

    production = {"call_count": d.MAX_CALLS, "committed_minor": 0, "calls": []}
    run = SimpleNamespace(id=uuid.uuid4(), chapter_no=1, current_step="PRODUCE", generation_context={})
    work = SimpleNamespace(id=uuid.uuid4())

    class FakeBroker:
        def __init__(self, **_kwargs):
            pass

        async def run(self, *args, **kwargs):
            raise AssertionError("provider must not be called")

    monkeypatch.setattr(prod, "CallBroker", FakeBroker)

    with pytest.raises(BudgetExhausted) as info:
        await d._call(
            AsyncMock(),
            SimpleNamespace(model_name="test"),
            work,
            run,
            production,
            d.SceneBrief,
            "sys",
            {"x": 1},
            "test",
            "cmd-1",
        )
    assert info.value.kind == "calls"
    assert production["call_count"] == d.MAX_CALLS


@pytest.mark.asyncio
async def test_grant_raises_call_cap_without_clearing_count(monkeypatch):
    from regent.novel.application import production as prod

    production = {
        "call_count": d.MAX_CALLS,
        "committed_minor": 0,
        "budget_grant_calls": 1,
        "calls": [],
    }
    run = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_no=1,
        current_step="PRODUCE",
        generation_context={},
        input_version=1,
    )
    work = SimpleNamespace(id=uuid.uuid4())

    class FakeBroker:
        def __init__(self, **_kwargs):
            pass

        async def run(self, *args, **kwargs):
            return SimpleNamespace(
                output=d.SceneBrief(
                    purpose="p",
                    setting="s",
                    conflict="c",
                    exit_condition="e",
                    actors=[
                        d.ActorDirection(
                            persona="主角", objective="o", instruction="i"
                        )
                    ],
                    narrative=d.NarrativeSpec(
                        viewpoint="主角",
                        distance="近",
                        style="克制",
                        reader_effect="担忧",
                        disclosure_rule="不揭露",
                    ),
                ),
                actual_minor=1,
                reserved_minor=1,
                reused=False,
                avoided_minor=0,
            )

    monkeypatch.setattr(prod, "CallBroker", FakeBroker)
    from regent.novel.application import directing_calls

    monkeypatch.setattr(directing_calls, "_save", lambda *_a, **_k: None)

    out = await d._call(
        AsyncMock(),
        SimpleNamespace(model_name="test"),
        work,
        run,
        production,
        d.SceneBrief,
        "sys",
        {"x": 1},
        "test",
        "cmd-1",
    )
    assert out.purpose == "p"
    assert production["call_count"] == d.MAX_CALLS + 1


def test_issue_ledger_marks_repeat_and_blocks_rewrite():
    take: dict = {}
    validation = {
        "requirements": [
            {"requirement_id": "req.a", "status": "missing"},
        ]
    }
    d._record_issue_ledger(take, content_hash="h1", validation=validation)
    d._record_issue_ledger(take, content_hash="h1", validation=validation)
    assert any(e["outcome"] == "repeat" for e in take["issue_ledger"] if e["round"] == 2)
    blocked = d._rewrite_blocked_by_ledger(take)
    assert blocked is not None
    assert "无进展" in blocked


def test_issue_ledger_blocks_rewrite_when_same_issues_persist_after_prose_change():
    """正文换了措辞但硬问题 id 集合不变 → 仍无进展，禁止再 REWRITE。"""
    take: dict = {}
    validation = {
        "requirements": [
            {"requirement_id": "req.a", "status": "missing"},
        ]
    }
    d._record_issue_ledger(take, content_hash="h1", validation=validation)
    d._record_issue_ledger(take, content_hash="h2", validation=validation)
    assert any(e["outcome"] == "open" for e in take["issue_ledger"] if e["round"] == 2)
    blocked = d._rewrite_blocked_by_ledger(take)
    assert blocked is not None
    assert "同一组问题" in blocked


def test_issue_ledger_allows_rewrite_when_issue_set_changes():
    """解决旧题并出现新题：集合变了，允许再修订。"""
    take: dict = {}
    d._record_issue_ledger(
        take,
        content_hash="h1",
        validation={"requirements": [{"requirement_id": "req.a", "status": "missing"}]},
    )
    d._record_issue_ledger(
        take,
        content_hash="h2",
        validation={"requirements": [{"requirement_id": "req.b", "status": "missing"}]},
    )
    assert d._rewrite_blocked_by_ledger(take) is None


def test_validate_reserve_rejects_rewrite_when_calls_low():
    production = {"call_count": d.MAX_CALLS - 1, "budget_grant_calls": 0}
    with pytest.raises(CommandRejected) as info:
        d._ensure_validate_call_reserve(production, action="REWRITE_PROSE")
    assert "预留后续额度" in str(info.value)


def test_validate_reserve_render_needs_less_than_rewrite():
    production = {
        "call_count": d.MAX_CALLS - d.VALIDATE_RESERVE_RENDER,
        "budget_grant_calls": 0,
    }
    d._ensure_validate_call_reserve(production, action="RENDER_SCENE")
    with pytest.raises(CommandRejected):
        d._ensure_validate_call_reserve(production, action="REWRITE_PROSE")


def test_validate_reserve_passes_with_enough_remaining():
    production = {
        "call_count": d.MAX_CALLS - d.VALIDATE_RESERVE_REWRITE,
        "budget_grant_calls": 0,
    }
    d._ensure_validate_call_reserve(production, action="REWRITE_PROSE")


def test_runtime_state_exposes_grant_aware_caps():
    production = {
        "call_count": d.MAX_CALLS,
        "budget_grant_calls": 15,
        "budget_grant_cost_minor": 500,
        "reserved_minor": 0,
        "cast": {"主角": {}},
        "scene_index": 0,
        "takes": [],
    }
    take = {
        "turn": 0,
        "revisions": 0,
        "take_no": 1,
        "events": [],
        "content": "x",
    }
    state = d._runtime_state(production, SimpleNamespace(input_version=1), take)
    assert state.call_cap == d.MAX_CALLS + 15
    assert state.cost_cap == d.MAX_COST_MINOR + 500
    assert state.calls_used == d.MAX_CALLS


@pytest.mark.asyncio
async def test_advance_step_budget_pause_keeps_run_running(novel_db, monkeypatch):
    from sqlalchemy import select

    monkeypatch.setattr(works, "append_event", AsyncMock())
    owner, work_id, run_id, branch_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    async with novel_db() as session:
        session.add(NovelPrincipalModel(id=owner, subject="bq-owner"))
        session.add(
            StoryWorkModel(
                id=work_id,
                owner_id=owner,
                state="RUNNING",
                genre="悬疑",
                branch_id=branch_id,
            )
        )
        session.add(StoryGoalModel(id=uuid.uuid4(), work_id=work_id, raw_intent="代价"))
        session.add(PersonaSpecModel(id=uuid.uuid4(), work_id=work_id, name="主角"))
        session.add(
            ChapterRunModel(
                id=run_id,
                work_id=work_id,
                branch_id=branch_id,
                chapter_no=1,
                attempt=1,
                state=ChapterRunState.RUNNING.value,
                current_step="PRODUCE",
                generation_context={
                    "architecture_version": d.ARCHITECTURE,
                    "production": {
                        "call_count": 90,
                        "committed_minor": 100,
                        "phase": "WATCH_PROSE",
                        "scene_index": 0,
                        "accepted": [],
                        "plan": {"scenes": [{}]},
                    },
                },
            )
        )
        for step in ("ASSEMBLE", "DIRECT", "PRODUCE", "REVIEW", "CANON"):
            session.add(
                ChapterStepModel(
                    id=uuid.uuid4(),
                    run_id=run_id,
                    step=step,
                    state=(
                        StepState.SUCCEEDED.value
                        if step in {"ASSEMBLE", "DIRECT"}
                        else StepState.PENDING.value
                    ),
                    attempt=0,
                )
            )
        await session.commit()

    async def raise_budget(*_a, **_k):
        raise BudgetExhausted("本章导演调用预算已耗尽，草稿已保留", kind="calls")

    monkeypatch.setattr(works, "execute_step", raise_budget)

    async with novel_db() as session:
        progress = await works.advance_step(
            session,
            provider=SimpleNamespace(),
            owner_id=owner,
            work_id=work_id,
            chapter_no=1,
        )
        await session.commit()

    assert progress.state == ChapterRunState.RUNNING
    assert progress.work_state == StoryWorkState.PAUSED_QUOTA.value
    assert progress.budget_pause is not None
    assert progress.budget_pause["kind"] == "calls"
    assert progress.budget_pause["call_count"] == 90

    async with novel_db() as session:
        work = await session.get(StoryWorkModel, work_id)
        run = await session.get(ChapterRunModel, run_id)
        step = (
            await session.scalars(
                select(ChapterStepModel).where(
                    ChapterStepModel.run_id == run_id,
                    ChapterStepModel.step == "PRODUCE",
                )
            )
        ).one()
        assert work.state == StoryWorkState.PAUSED_QUOTA.value
        assert run.state == ChapterRunState.RUNNING.value
        assert step.state == StepState.PENDING.value
        assert int(run.generation_context["production"]["call_count"]) == 90


@pytest.mark.asyncio
async def test_authorize_budget_raises_cap_and_resumes(novel_db, monkeypatch):
    monkeypatch.setattr(works, "append_event", AsyncMock())
    owner, work_id, run_id, branch_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    async with novel_db() as session:
        session.add(NovelPrincipalModel(id=owner, subject="bq-auth"))
        session.add(
            StoryWorkModel(
                id=work_id,
                owner_id=owner,
                state=StoryWorkState.PAUSED_QUOTA.value,
                genre="悬疑",
                branch_id=branch_id,
            )
        )
        session.add(
            ChapterRunModel(
                id=run_id,
                work_id=work_id,
                branch_id=branch_id,
                chapter_no=1,
                attempt=1,
                state=ChapterRunState.RUNNING.value,
                generation_context={
                    "budget_pause": {"kind": "calls", "call_count": d.MAX_CALLS},
                    "production": {
                        "call_count": d.MAX_CALLS,
                        "committed_minor": 10,
                        "budget_grant_calls": 0,
                    },
                },
            )
        )
        await session.commit()

    async with novel_db() as session:
        state = await works.authorize_budget(
            session,
            owner_id=owner,
            work_id=work_id,
            grant_calls=20,
            grant_cost_minor=0,
            client_nonce="n1",
        )
        await session.commit()
        state_val = getattr(state, "state", state)
        assert getattr(state_val, "value", state_val) == StoryWorkState.RUNNING.value
        run = await session.get(ChapterRunModel, run_id)
        production = run.generation_context["production"]
        assert production["budget_grant_calls"] == 20
        assert production["call_count"] == d.MAX_CALLS
        assert "budget_pause" not in run.generation_context


@pytest.mark.asyncio
async def test_non_budget_production_stopped_still_terminal(novel_db, monkeypatch):
    monkeypatch.setattr(works, "append_event", AsyncMock())
    owner, work_id, run_id, branch_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    async with novel_db() as session:
        session.add(NovelPrincipalModel(id=owner, subject="bq-term"))
        session.add(
            StoryWorkModel(
                id=work_id,
                owner_id=owner,
                state="RUNNING",
                genre="悬疑",
                branch_id=branch_id,
            )
        )
        session.add(
            ChapterRunModel(
                id=run_id,
                work_id=work_id,
                branch_id=branch_id,
                chapter_no=1,
                attempt=1,
                state=ChapterRunState.RUNNING.value,
                current_step="PRODUCE",
                generation_context={
                    "architecture_version": d.ARCHITECTURE,
                    "production": {},
                },
            )
        )
        for step in ("ASSEMBLE", "DIRECT", "PRODUCE", "REVIEW", "CANON"):
            session.add(
                ChapterStepModel(
                    id=uuid.uuid4(),
                    run_id=run_id,
                    step=step,
                    state=(
                        StepState.SUCCEEDED.value
                        if step in {"ASSEMBLE", "DIRECT"}
                        else StepState.PENDING.value
                    ),
                    attempt=0,
                )
            )
        await session.commit()

    async def raise_other(*_a, **_k):
        raise ProductionStopped("呈现修订重复了无效指令")

    monkeypatch.setattr(works, "execute_step", raise_other)

    async with novel_db() as session:
        progress = await works.advance_step(
            session,
            provider=SimpleNamespace(),
            owner_id=owner,
            work_id=work_id,
            chapter_no=1,
        )
        await session.commit()
    assert progress.state == ChapterRunState.TERMINAL_FAILED
    assert progress.work_state == StoryWorkState.RUNNING.value
