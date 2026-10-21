"""Harness² 递归臂：走生产 tick 的测试。

覆盖：路由、CallBroker 记账（一 tick 一次调用、命令 id 不撞）、前置门提示保留、
组合器采纳与交付规则、后继正文、追读自评触发、两轮上限、本书覆盖层的钉住、
试用采纳与回退、软检查/钩子/技能、改进库范围检索与剪枝、回放哈希。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from regent.novel.application import directing_chapter as chapter
from regent.novel.application import executor as executor_app
from regent.novel.application import works
from regent.novel.application.directing_budget import effective_call_cap
from regent.novel.application.directing_calls import _save
from regent.novel.application.directing_protocol import is_directed, production_protocol
from regent.novel.application.directing_scene_loop import produce_tick
from regent.novel.domain.states import ChapterStep, chapter_step_order
from regent.novel.harnessing import library as lib
from regent.novel.harnessing.overlay import first_pass_baseline, pin_harness, record_verified_round
from regent.novel.harnessing.recursion import (
    ComposeDecision,
    ConfigEdit,
    GenericScreen,
    HarnessCandidate,
    HarnessProposal,
    back_edits,
    settle_compose,
)
from regent.novel.harnessing.replay import check_write_replay, compute_payload_hash
from regent.novel.harnessing.surface import (
    apply_overrides,
    book_terms,
    flatten,
    load_base_pack,
    load_improver_prompt,
    resolve_hooks,
    resolve_skills,
    run_soft_checks,
    safe_pattern,
    validate_knob_value,
    validate_overrides,
)
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    HarnessLibraryModel,
    NovelPrincipalModel,
    PersonaSpecModel,
    StoryGoalModel,
    StoryWorkModel,
)
from sqlalchemy import select

H2 = "director_chapter_h2"


def _prose(seed: str, floor: int) -> str:
    text = f"{seed}林晚把铜钥匙放进抽屉。"
    i = 1
    while len(text) < floor:
        text += f"{seed}林晚把第{i}枚铜钥匙放进第{i}只抽屉，窗外第{i}阵雨还在下，走廊里没有第{i}个人经过。"
        i += 1
    return text


GOOD = _prose("", 2000)
GOOD_B = _prose("又", 2000)
SHORT = _prose("短", 900)
PRIOR = "上一章写的是码头的雾和一艘没有名字的船。"


def _run(*, phase: str = "WRITE", content: str = "", executor: str = H2) -> SimpleNamespace:
    base = load_base_pack("v1")
    return SimpleNamespace(
        id=uuid.uuid4(),
        chapter_no=2,
        input_version=1,
        title="第二章",
        content=content,
        word_count=len(content),
        review={},
        generation_context={
            "architecture_version": executor,
            "executor": executor,
            "harness_id": "base/v1",
            "harness_hash": base.content_hash,
            "harness_overlay": {},
            "harness_overlay_id": "",
            "chapter_no": 2,
            "story_complete": False,
            "target_node": {"id": "n1", "title": "夺钥", "promise": "夺回铜钥匙"},
            "story_bible_block": "雨城",
            "prose_style": {},
            "reader_contract": {},
            "dramatic_engine": {},
            "canon": [{"statement": "林晚住在雨城"}],
            "recent_chapters": [{"content": PRIOR}],
            "memory": [
                {
                    "kind": "promise",
                    "state": "OPEN",
                    "subject": "林晚",
                    "content": "还回铜钥匙",
                    "source_chapter_no": 1,
                },
            ],
            "production": {
                "protocol": "chapter",
                "executor": executor,
                "phase": phase,
                "repairs": 0,
                "call_count": 0,
                "cast": {"林晚": {"identity": {"role": "主角"}}},
                "takes": [],
                "accepted": [],
                "check_fails": [],
                "working_state": {},
            },
        },
    )


class FakeBroker:
    """与 ``_call`` 同样记账：预算上限、call_count、input_hash、写回 run。"""

    def __init__(self, outputs: dict):
        self.outputs = outputs
        self.calls: list[dict] = []

    async def __call__(
        self,
        session,
        provider,
        work,
        run,
        production,
        schema,
        system,
        payload,
        purpose,
        command_id,
        temperature=None,
    ):
        assert production["call_count"] < effective_call_cap(production), "超出本章调用上限"
        assert command_id not in {c["command"] for c in self.calls}, f"命令 id 重复：{command_id}"
        self.calls.append(
            {
                "schema": schema.__name__,
                "purpose": purpose,
                "command": command_id,
                "system": system,
                "payload": payload,
                "temperature": temperature,
            }
        )
        production["call_count"] = production.get("call_count", 0) + 1
        production.setdefault("calls", []).append(
            {
                "purpose": purpose,
                "input_hash": hashlib.sha256(
                    json.dumps(payload, ensure_ascii=False).encode()
                ).hexdigest(),
            }
        )
        _save(run, production)
        make = self.outputs[schema.__name__]
        return make(payload, system, command_id) if callable(make) else make.pop(0)


async def _tick(session, run, work, broker: FakeBroker) -> bool:
    before = len(broker.calls)
    done = await chapter.produce_chapter_tick(
        session, provider=None, work=work, run=run, production=run.generation_context["production"]
    )
    assert len(broker.calls) - before <= 1, "一 tick 至多一次模型调用"
    return done


def _edits(overrides: dict, what: str, where: str) -> list[ConfigEdit]:
    """每处配置修改配一条认账的 edits：path 指向键，where 指向参照稿。"""
    return [ConfigEdit(what=what, path=".".join(path), where=where) for path in flatten(overrides)]


def _proposal(generic: dict, book: dict) -> HarnessProposal:
    return HarnessProposal(
        generic=HarnessCandidate(
            kind="generic",
            edits=_edits(generic, "前章尾段 500 → 300", "开头重演了前章结尾"),
            overrides=generic,
        ),
        book=HarnessCandidate(
            kind="book",
            edits=_edits(book, "加章首承接技巧", "第一段"),
            overrides=book,
        ),
    )


def _work() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), genre="悬疑")


# ---------------------------------------------------------------- 路由与预算


def test_h2_routes_through_chapter_protocol():
    run = _run()
    assert is_directed(run)
    assert production_protocol(run) == "chapter"
    assert production_protocol({"architecture_version": H2}) == "chapter"
    assert chapter_step_order({"architecture_version": H2}) == (
        ChapterStep.ASSEMBLE,
        ChapterStep.PRODUCE,
        ChapterStep.REVIEW,
        ChapterStep.CANON,
    )
    assert effective_call_cap({"protocol": "chapter", "executor": H2}) == 30
    assert effective_call_cap({"protocol": "chapter", "executor": "director_chapter"}) == 16
    assert {"harness_overlay", "harness_overlay_id"} <= set(executor_app.PINNED_CONTEXT_KEYS)


@pytest.mark.asyncio
async def test_scene_loop_dispatches_h2_to_chapter_tick(monkeypatch):
    seen = {}

    async def _fake(*_args, **_kwargs):
        seen["chapter"] = True
        return False

    monkeypatch.setattr("regent.novel.application.directing_scene_loop.produce_chapter_tick", _fake)
    run = SimpleNamespace(generation_context={"architecture_version": H2, "production": {}})
    assert await produce_tick(None, provider=None, work=None, run=run) is False
    assert seen["chapter"] is True


# ---------------------------------------------------------------- 首稿


@pytest.mark.asyncio
async def test_first_write_keeps_front_gate_hint_and_default_payload(monkeypatch):
    run = _run()
    run.generation_context["prose_style"] = {"person": "第三人称", "pov": "林晚"}
    broker = FakeBroker({"ChapterProse": lambda *_: chapter.ChapterProse(content=GOOD)})
    monkeypatch.setattr("regent.novel.application.directing_chapter._call", broker)
    await _tick(None, run, _work(), broker)
    call = broker.calls[0]
    ctx = run.generation_context
    cast = ctx["production"]["cast"]
    assert call["command"] == "v1:chapter_write"
    assert "【前置硬约束】" in call["system"]
    assert call["system"].startswith(chapter._writer_system(ctx, cast))
    assert call["payload"] == chapter.build_write_payload(ctx, cast)
    assert ctx["production"]["h2_effective_hash"] == load_base_pack("v1").content_hash


@pytest.mark.asyncio
async def test_passing_first_draft_finishes_without_recursion(monkeypatch):
    run = _run()
    broker = FakeBroker({"ChapterProse": lambda *_: chapter.ChapterProse(content=GOOD)})
    monkeypatch.setattr("regent.novel.application.directing_chapter._call", broker)
    work = _work()
    assert await _tick(None, run, work, broker) is False
    assert await _tick(None, run, work, broker) is True
    production = run.generation_context["production"]
    assert production["phase"] == "DONE"
    assert production["first_pass"] is True
    assert "h2_round" not in production
    assert len(broker.calls) == 1


# ---------------------------------------------------------------- 一轮递归


@pytest.mark.asyncio
async def test_failed_check_runs_one_round_and_delivers_passing_probe(novel_db, monkeypatch):
    generic = {"knobs": {"previous_chapter_tail": 300}}
    book = {"skills": {"chapter_opening": True}}

    def _prose_for(payload, system, command):
        if command == "v1:chapter_write":
            return chapter.ChapterProse(content=SHORT)
        if command.endswith(":generic"):
            return chapter.ChapterProse(content=GOOD)
        return chapter.ChapterProse(content=SHORT)

    broker = FakeBroker(
        {
            "ChapterProse": _prose_for,
            "HarnessProposal": [_proposal(generic, book)],
            "ComposeDecision": [
                ComposeDecision(
                    decision="adopt_generic",
                    deliver="generic",
                    observation="通用探针没有重演前章结尾",
                    experience="前章尾段太长时容易重演，缩到 300 字",
                )
            ],
            "GenericScreen": [GenericScreen(book_specific=False)],
        }
    )
    monkeypatch.setattr("regent.novel.application.directing_chapter._call", broker)
    run = _run()
    work = _work()
    async with novel_db() as session:
        for _ in range(9):
            if await _tick(session, run, work, broker):
                break
        await session.commit()
        production = run.generation_context["production"]
        assert production["phase"] == "DONE"
        assert [c["command"] for c in broker.calls] == [
            "v1:chapter_write",
            "v1:h2:propose:r1",
            "v1:h2:probe:r1:generic",
            "v1:h2:probe:r1:book",
            "v1:h2:compose:r1",
            "v1:h2:screen:r1",
        ]
        assert [c["purpose"] for c in broker.calls][1:] == [
            "h2_propose",
            "h2_probe",
            "h2_probe",
            "h2_compose",
            "h2_screen",
        ]
        assert production["call_count"] == 6
        assert production["first_pass"] is False
        assert production["h2_delivered_from"] == "probe"
        assert production["h2_overrides"] == generic
        assert run.content == GOOD
        assert (
            production["h2_effective_hash"]
            == apply_overrides(load_base_pack("v1"), generic).content_hash
        )
        # 组合器看到了两个候选的原样修改与探针结果
        compose_request = broker.calls[4]["payload"]
        assert [c["overrides"] for c in compose_request["candidates"]] == [generic, book]
        assert compose_request["candidates"][0]["edits"][0]["what"] == "前章尾段 500 → 300"
        assert compose_request["deliver_options"] == ["generic"]
        # 采纳的这条经验连同它的 edits 一起留在运行记录里
        assert production["h2_lesson"]["edits"] == {
            "generic": compose_request["candidates"][0]["edits"]
        }
        assert production["h2_lesson"]["screen"] == {"book_specific": False, "reason": ""}
        # 筛查看到的是这条经验与随之采纳的修改
        screen_request = broker.calls[5]["payload"]
        assert screen_request["experience"] == "前章尾段太长时容易重演，缩到 300 字"
        assert screen_request["overrides"] == generic
        assert "林晚" in screen_request["book_terms"]
        # 探针 book 的执笔提示带上了章首承接技巧，且仍保留前置门提示的基底
        book_system = broker.calls[3]["system"]
        assert "第一句直接接上那个动作" in book_system
        assert book_system.startswith(chapter.CHAPTER_WRITE_SYSTEM)
        assert "【前置硬约束】" in book_system
        rows = (await session.scalars(select(HarnessLibraryModel))).all()
        assert len(rows) == 1
        assert rows[0].status == "provisional"
        assert rows[0].scope == "global"
        assert rows[0].overlay_after == generic
        assert rows[0].since_chapter == 3
        assert production["h2_recorded"] == str(rows[0].id)


@pytest.mark.asyncio
async def test_screen_demotes_book_specific_lesson_to_work_scope(novel_db, monkeypatch):
    """通用条目其实只在这本书成立时降级：改进库里它对别的书不可见。"""
    generic = {"memory": "同字不同齿距：重复的字要换一种间距。"}

    def _prose_for(payload, system, command):
        return chapter.ChapterProse(content=GOOD if command.endswith(":generic") else SHORT)

    broker = FakeBroker(
        {
            "ChapterProse": _prose_for,
            "HarnessProposal": [_proposal(generic, {})],
            "ComposeDecision": [
                ComposeDecision(
                    decision="adopt_generic",
                    deliver="generic",
                    observation="通用探针的重字不再连排",
                    experience="同字不同齿距：重复的字要换一种间距。",
                )
            ],
            "GenericScreen": [
                GenericScreen(book_specific=True, reason="齿距说的是本书那把铜钥匙的齿")
            ],
        }
    )
    monkeypatch.setattr("regent.novel.application.directing_chapter._call", broker)
    run = _run()
    work = _work()
    async with novel_db() as session:
        for _ in range(9):
            if await _tick(session, run, work, broker):
                break
        await session.commit()
        production = run.generation_context["production"]
        assert production["phase"] == "DONE"
        assert production["h2_lesson"]["screen"] == {
            "book_specific": True,
            "reason": "齿距说的是本书那把铜钥匙的齿",
        }
        row = (await session.scalars(select(HarnessLibraryModel))).one()
        assert row.scope == "work"
        assert row.scope_key == str(work.id)
        # 修改本身仍然生效，只是不共享
        assert production["h2_overrides"] == generic
        assert row.overlay_after == generic


@pytest.mark.asyncio
async def test_screen_never_kills_a_chapter_that_already_passed(novel_db, monkeypatch):
    """筛查是可选的：没预算时按最坏情况降级，不能把已经过硬门的一章拖成失败。"""
    from regent.novel.domain.errors import BudgetExhausted

    generic = {"knobs": {"previous_chapter_tail": 300}}

    class _broker(FakeBroker):
        async def __call__(self, *args, **kwargs):
            if args[8] == "h2_screen":
                raise BudgetExhausted("本章货币预算已耗尽，草稿已保留", kind="cost")
            return await super().__call__(*args, **kwargs)

    broker = _broker(
        {
            "ChapterProse": lambda payload, system, command: chapter.ChapterProse(
                content=GOOD if command.endswith(":generic") else SHORT
            ),
            "HarnessProposal": [_proposal(generic, {})],
            "ComposeDecision": [
                ComposeDecision(
                    decision="adopt_generic", deliver="generic", observation="好", experience="经验"
                )
            ],
        }
    )
    monkeypatch.setattr("regent.novel.application.directing_chapter._call", broker)
    run = _run()
    work = _work()
    async with novel_db() as session:
        for _ in range(9):
            if await _tick(session, run, work, broker):
                break
        await session.commit()
        production = run.generation_context["production"]
        assert production["phase"] == "DONE"
        assert run.content == GOOD
        assert production["h2_screen"]["book_specific"] is True
        assert "预算耗尽" in production["h2_screen"]["reason"]
        row = (await session.scalars(select(HarnessLibraryModel))).one()
        assert row.scope == "work"


@pytest.mark.asyncio
async def test_screen_survives_an_unknown_call(novel_db, monkeypatch):
    """筛查调用结果不明也降级放行：第 4 章 PROPOSE 就是 CALL_UNKNOWN 那种失败。"""
    from regent.novel.application.production import CallUnknown

    generic = {"knobs": {"previous_chapter_tail": 300}}

    class _broker(FakeBroker):
        async def __call__(self, *args, **kwargs):
            if args[8] == "h2_screen":
                raise CallUnknown("unreconciled call: h2_screen")
            return await super().__call__(*args, **kwargs)

    broker = _broker(
        {
            "ChapterProse": lambda payload, system, command: chapter.ChapterProse(
                content=GOOD if command.endswith(":generic") else SHORT
            ),
            "HarnessProposal": [_proposal(generic, {})],
            "ComposeDecision": [
                ComposeDecision(
                    decision="adopt_generic", deliver="generic", observation="好", experience="经验"
                )
            ],
        }
    )
    monkeypatch.setattr("regent.novel.application.directing_chapter._call", broker)
    run = _run()
    work = _work()
    async with novel_db() as session:
        for _ in range(9):
            if await _tick(session, run, work, broker):
                break
        await session.commit()
        production = run.generation_context["production"]
        assert production["phase"] == "DONE"
        assert run.content == GOOD
        assert production["h2_screen"]["book_specific"] is True
        assert "CallUnknown" in production["h2_screen"]["reason"]
        assert (await session.scalars(select(HarnessLibraryModel))).one().scope == "work"


@pytest.mark.asyncio
@pytest.mark.parametrize("error_name", ["CallConflict", "CallInFlight", "StaleLease"])
async def test_screen_does_not_swallow_conflicts_or_lost_leases(novel_db, monkeypatch, error_name):
    """幂等冲突是缺陷；租约被占说明另一个 worker 持有这一章。都不是「筛查没跑成」。"""
    from regent.novel.application import production as production_module

    error = getattr(production_module, error_name)
    generic = {"knobs": {"previous_chapter_tail": 300}}

    class _broker(FakeBroker):
        async def __call__(self, *args, **kwargs):
            if args[8] == "h2_screen":
                raise error(f"h2_screen: {error_name}")
            return await super().__call__(*args, **kwargs)

    broker = _broker(
        {
            "ChapterProse": lambda payload, system, command: chapter.ChapterProse(
                content=GOOD if command.endswith(":generic") else SHORT
            ),
            "HarnessProposal": [_proposal(generic, {})],
            "ComposeDecision": [
                ComposeDecision(
                    decision="adopt_generic", deliver="generic", observation="好", experience="经验"
                )
            ],
        }
    )
    monkeypatch.setattr("regent.novel.application.directing_chapter._call", broker)
    run = _run()
    work = _work()
    async with novel_db() as session:
        with pytest.raises(error):
            for _ in range(9):
                if await _tick(session, run, work, broker):
                    break


def test_back_edits_drops_overrides_no_edit_claims():
    """没人认账的配置修改不采纳：第 4 章那次调参就是三条 edits 都没提到它。"""
    edits = [
        {"what": "前章尾段 500 → 300", "path": "knobs.previous_chapter_tail", "where": "开头一段"},
    ]
    overrides = {
        "knobs": {"previous_chapter_tail": 300, "soft_check_repetition_ngram": 60},
        "memory": "换一种间距。",
    }
    kept, reasons = back_edits(edits, overrides)
    assert kept == {"knobs": {"previous_chapter_tail": 300}}
    assert len(reasons) == 1
    assert "knobs.soft_check_repetition_ngram" in reasons[0]
    assert "memory" in reasons[0]


def test_back_edits_needs_a_place_in_the_reference_draft():
    """指不出参照稿位置的修改不算认账，哪怕 path 写对了。"""
    overrides = {"knobs": {"previous_chapter_tail": 300}}
    kept, reasons = back_edits(
        [{"what": "前章尾段 500 → 300", "path": "knobs.previous_chapter_tail", "where": ""}],
        overrides,
    )
    assert kept == {}
    assert reasons
    # 没写 path 时退回键名匹配
    kept, reasons = back_edits(
        [{"what": "knobs.previous_chapter_tail 500 → 300", "where": "开头一段"}], overrides
    )
    assert kept == overrides
    assert not reasons
    # path 认下整棵子树
    kept, _ = back_edits(
        [{"what": "加章首承接技巧", "path": "skills.chapter_opening", "where": "第一段"}],
        {"skills": {"chapter_opening": {"mode": "always", "instruction": "第一句接上那个动作"}}},
    )
    assert kept["skills"]["chapter_opening"]["mode"] == "always"


def test_back_edits_checks_the_quoted_reference_text():
    """where 里引了原文就得真在参照稿里：「指向具体位置」不能靠随便写几个字蒙过去。"""
    overrides = {"knobs": {"previous_chapter_tail": 300}}
    reference = "齿轮在灯下转了半圈。林晚把包裹推到问题件区，没有填名字。"
    edit = {
        "what": "前章尾段 500 → 300",
        "path": "knobs.previous_chapter_tail",
        "where": "「林晚把包裹推到问题件区」那一段太长",
    }
    kept, reasons = back_edits([edit], overrides, reference)
    assert kept == overrides
    assert not reasons

    forged = dict(edit, where="「他在雨里站了一整夜」那一段太长")
    kept, reasons = back_edits([forged], overrides, reference)
    assert kept == {}
    assert reasons

    # 没引原文就无从核对，维持原样放行；参照稿缺失时也不拿引文当拒收理由
    plain = dict(edit, where="结尾那一段")
    assert back_edits([plain], overrides, reference)[0] == overrides
    assert back_edits([edit], overrides)[0] == overrides


def test_back_edits_checks_single_curly_quotes_like_the_live_proposal():
    """第 4 章真机提案的 where 全用‘’引原文，不认这种引号就等于对真数据不设防。"""
    overrides = {"prompts": {"write": "同一手段在同一章内不得重复使用。"}}
    reference = "陈默把照片放下。他重新蹲到门槛边，用拨针把门槛石彻底撬开。坑不大。"
    live = {
        "what": "prompts.write 增加‘同一手段不得在同一章内重复使用’约束",
        "path": "prompts.write",
        "where": "参照稿中‘他重新蹲到门槛边，用拨针把门槛石彻底撬开’——与前面是同一招的重复。",
    }
    assert back_edits([live], overrides, reference)[0] == overrides

    forged = dict(live, where="参照稿中‘他翻墙进了隔壁院子’——与前面是同一招的重复。")
    kept, reasons = back_edits([forged], overrides, reference)
    assert kept == {}
    assert reasons


@pytest.mark.asyncio
async def test_successor_write_uses_adopted_overrides_and_its_own_command(novel_db, monkeypatch):
    generic = {"knobs": {"previous_chapter_tail": 300}}
    book = {"skills": {"chapter_opening": True}}

    def _prose_for(payload, system, command):
        return chapter.ChapterProse(content=GOOD if command == "v1:chapter_write:h1" else SHORT)

    broker = FakeBroker(
        {
            "ChapterProse": _prose_for,
            "HarnessProposal": [_proposal(generic, book)],
            "ComposeDecision": [
                ComposeDecision(decision="adopt_book", observation="两个都没过，本书候选少一处失败")
            ],
        }
    )
    monkeypatch.setattr("regent.novel.application.directing_chapter._call", broker)
    run = _run()
    work = _work()
    async with novel_db() as session:
        for _ in range(9):
            if await _tick(session, run, work, broker):
                break
        production = run.generation_context["production"]
        assert production["phase"] == "DONE"
        assert broker.calls[-1]["command"] == "v1:chapter_write:h1"
        assert "第一句直接接上那个动作" in broker.calls[-1]["system"]
        assert production["h2_delivered_from"] == "successor"
        row = (await session.scalars(select(HarnessLibraryModel))).one()
        assert row.scope == "work"
        assert row.scope_key == str(work.id)


@pytest.mark.asyncio
async def test_arc_review_problems_trigger_round_on_passing_draft(novel_db, monkeypatch):
    broker = FakeBroker(
        {
            "ChapterProse": lambda *_: chapter.ChapterProse(content=GOOD),
            "HarnessProposal": [_proposal({"knobs": {"previous_chapter_tail": 300}}, {})],
            "ComposeDecision": [
                ComposeDecision(decision="keep", deliver="reference", observation="参照稿更好")
            ],
        }
    )
    monkeypatch.setattr("regent.novel.application.directing_chapter._call", broker)
    run = _run()
    run.generation_context["production"]["arc_review"] = {
        "keeps_reading": False,
        "problems": [{"chapters": [1], "problem": "主角一直被动", "fix": "让他先动手"}],
    }
    work = _work()
    async with novel_db() as session:
        for _ in range(8):
            if await _tick(session, run, work, broker):
                break
    production = run.generation_context["production"]
    assert production["phase"] == "DONE"
    assert production["h2_reference"]["trigger"] == "arc"
    assert "主角一直被动" in broker.calls[1]["payload"]["arc_issues"]
    # book 候选没有修改：探针跳过，不花调用
    assert [c["command"] for c in broker.calls] == [
        "v1:chapter_write",
        "v1:h2:propose:r1",
        "v1:h2:probe:r1:generic",
        "v1:h2:compose:r1",
    ]
    assert production["h2_probes"][1]["skipped"] is True
    assert run.content == GOOD
    assert "h2_delivered_from" not in production


@pytest.mark.asyncio
async def test_two_rounds_then_falls_back_to_repair(novel_db, monkeypatch):
    proposals = [
        _proposal(
            {"knobs": {"previous_chapter_tail": 300}}, {"knobs": {"previous_chapter_tail": 800}}
        ),
        _proposal(
            {"knobs": {"previous_chapter_tail": 400}}, {"knobs": {"previous_chapter_tail": 700}}
        ),
    ]
    broker = FakeBroker(
        {
            "ChapterProse": lambda *_: chapter.ChapterProse(content=SHORT),
            "HarnessProposal": proposals,
            "ComposeDecision": [
                ComposeDecision(decision="adopt_generic", observation="少一处失败"),
                ComposeDecision(decision="keep", observation="两个都更差"),
            ],
            # 第一轮采纳的是通用候选：交付前要先过专有性筛查
            "GenericScreen": [GenericScreen(book_specific=True, reason="靠本书的铜钥匙才成立")],
        }
    )
    monkeypatch.setattr("regent.novel.application.directing_chapter._call", broker)
    run = _run()
    work = _work()
    async with novel_db() as session:
        for _ in range(18):
            await _tick(session, run, work, broker)
            if run.generation_context["production"]["phase"] == "REPAIR":
                break
    production = run.generation_context["production"]
    assert production["phase"] == "REPAIR"
    assert production["h2_round"] == 2
    assert production["h2_stop"] is True
    assert [h["decision"] for h in production["h2_history"]] == ["adopt_generic", "keep"]
    assert "v1:h2:propose:r2" in {c["command"] for c in broker.calls}
    assert "v1:h2:screen:r1" in {c["command"] for c in broker.calls}
    assert production["call_count"] <= effective_call_cap(production)


@pytest.mark.asyncio
async def test_default_arm_never_enters_recursion(monkeypatch):
    broker = FakeBroker({"ChapterProse": lambda *_: chapter.ChapterProse(content=SHORT)})
    monkeypatch.setattr("regent.novel.application.directing_chapter._call", broker)
    run = _run(executor="director_chapter")
    work = _work()
    await _tick(None, run, work, broker)
    await _tick(None, run, work, broker)
    production = run.generation_context["production"]
    assert production["phase"] == "REPAIR"
    # 首过口径与 H2 臂同一个键：基线不能因为换了臂就认不出这几章
    assert production["first_pass"] is False
    assert "h2_first_pass" not in production
    run.generation_context["production"]["phase"] = "PROPOSE"
    from regent.novel.domain.errors import ProductionStopped

    with pytest.raises(ProductionStopped, match="非 H2 臂"):
        await _tick(None, run, work, broker)


# ---------------------------------------------------------------- 组合器规则


def _cands():
    return [
        {"kind": "generic", "overrides": {"knobs": {"previous_chapter_tail": 300}}},
        {"kind": "book", "overrides": {"knobs": {"earlier_results_count": 3}}},
    ]


def test_compose_cannot_adopt_failed_probe_when_other_passed():
    probes = [
        {"kind": "generic", "passed": False},
        {"kind": "book", "passed": True, "soft_issues": []},
    ]
    out = settle_compose(
        ComposeDecision(decision="adopt_generic", deliver="generic"),
        _cands(),
        probes,
        reference_passed=False,
    )
    assert out["decision"] == "adopt_book"
    assert out["deliver"] == "book"
    assert out["adopted_overrides"] == {"knobs": {"earlier_results_count": 3}}
    assert out["corrections"]


def test_compose_adopt_uses_candidate_overrides_verbatim():
    probes = [
        {"kind": "generic", "passed": True, "soft_issues": []},
        {"kind": "book", "passed": False},
    ]
    out = settle_compose(
        ComposeDecision(
            decision="adopt_generic", adopted_overrides={"knobs": {"previous_chapter_tail": 999}}
        ),
        _cands(),
        probes,
        reference_passed=False,
    )
    assert out["adopted_overrides"] == {"knobs": {"previous_chapter_tail": 300}}


def test_compose_merge_must_be_subset_of_candidates():
    probes = [{"kind": "generic", "passed": False}, {"kind": "book", "passed": False}]
    ok = settle_compose(
        ComposeDecision(
            decision="merge",
            adopted_overrides={"knobs": {"previous_chapter_tail": 300, "earlier_results_count": 3}},
        ),
        _cands(),
        probes,
        reference_passed=False,
    )
    assert ok["decision"] == "merge"
    assert ok["deliver"] == "none"
    bad = settle_compose(
        ComposeDecision(
            decision="merge", adopted_overrides={"knobs": {"previous_chapter_tail": 600}}
        ),
        _cands(),
        probes,
        reference_passed=False,
    )
    assert bad["decision"] == "keep"
    assert bad["adopted_overrides"] == {}


def test_compose_never_delivers_failed_draft():
    probes = [{"kind": "generic", "passed": False}, {"kind": "book", "passed": False}]
    out = settle_compose(
        ComposeDecision(decision="keep", deliver="generic"), _cands(), probes, reference_passed=True
    )
    assert out["deliver"] == "reference"


# ---------------------------------------------------------------- 配置面


def test_base_pack_matches_python_constants_and_defaults():
    config = load_base_pack("v1")
    assert config.get_prompt("write") == chapter.CHAPTER_WRITE_SYSTEM
    assert config.get_prompt("repair") == chapter.CHAPTER_REPAIR_SYSTEM
    assert config.get_knob("open_promises_max") == 0
    assert config.get_memory() == ""
    run = _run()
    ctx = run.generation_context
    cast = ctx["production"]["cast"]
    assert chapter.h2_write_payload(ctx, cast, config) == chapter.build_write_payload(ctx, cast)
    for kind in ("propose", "compose"):
        assert len(load_improver_prompt(kind)) > 50


def test_validate_overrides_rejects_frozen_unknown_and_unsafe():
    base = load_base_pack("v1")
    clean, errors = validate_overrides(
        {
            "hard_gates": {"min_chars": 0},
            "budget_caps": 99,
            "secret": 1,
            "knobs": {"previous_chapter_tail": 9999, "earlier_results_count": 2, "nope": 1},
            "soft_checks": {
                "bad_regex": {"kind": "pattern", "patterns": ["(a+)+$"]},
                "broken": {"kind": "pattern", "patterns": ["(未闭合"]},
            },
            "hooks": {"odd": {"condition": "exec_code", "guidance": "随便写点"}},
            "skills": {"new_skill": {"instruction": "写短句。"}},
        },
        base,
    )
    assert clean == {"knobs": {"earlier_results_count": 2}}
    joined = "；".join(errors)
    for word in (
        "冻结面",
        "不认识的配置项",
        "越界",
        "未登记的旋钮",
        "嵌套量词",
        "无法编译",
        "未登记的钩子条件",
        "要么给 when",
    ):
        assert word in joined


def test_generic_candidate_with_book_names_is_rejected():
    base = load_base_pack("v1")
    clean, errors = validate_overrides(
        {"memory": "林晚每次开门前都要先摸钥匙。"}, base, book_terms=["林晚"], generic=True
    )
    assert clean == {}
    assert "林晚" in errors[0]
    kept, _ = validate_overrides(
        {"memory": "林晚每次开门前都要先摸钥匙。"}, base, book_terms=["林晚"]
    )
    assert kept == {"memory": "林晚每次开门前都要先摸钥匙。"}


def test_book_terms_cover_world_bible_places_and_aliases():
    """只查卡司人名不够：世界书里的地名与别名同样会让通用条目变成噪音。"""
    ctx = {
        "world_bible": {
            "personas": [{"name": "林晚", "aliases": ["晚晚"]}],
            "places": [{"name": "雨城码头", "aliases": ["码头"]}],
        }
    }
    terms = book_terms(ctx, {"陈默": {}})
    assert {"林晚", "晚晚", "雨城码头", "码头", "陈默"} <= set(terms)
    clean, errors = validate_overrides(
        {"memory": "在雨城码头这类地方，人物要先动手再解释。"},
        load_base_pack("v1"),
        book_terms=terms,
        generic=True,
    )
    assert clean == {}
    assert "雨城码头" in errors[0]


def test_safe_pattern_and_knob_range():
    with pytest.raises(ValueError, match="嵌套量词"):
        safe_pattern("(a+)+$")
    with pytest.raises(ValueError, match="反向引用"):
        safe_pattern(r"(.)\1")
    assert safe_pattern("原来.{0,20}竟然").search("原来他竟然来了")
    assert validate_knob_value("previous_chapter_tail", 300) == 300
    with pytest.raises(ValueError, match="越界"):
        validate_knob_value("previous_chapter_tail", 9999)
    with pytest.raises(ValueError, match="未登记"):
        validate_knob_value("unknown_key", 1)


def test_soft_checks_detect_overlap_repetition_and_patterns():
    config = load_base_pack("v1")
    shared = "雾从码头那边一层一层漫过来，灯笼的光被压成一团昏黄，他听见船板在水里轻轻撞了三下又停住，岸上没有一个人回头看"
    previous = "前文铺垫。" * 10 + shared
    content = shared + "。他推开门。" + "原来他竟然早就到了。"
    issues = run_soft_checks(content, config, previous_chapter=previous)
    assert any("章首与上一章结尾连续重合" in item for item in issues)
    assert any("套话句式：原来他竟然" in item for item in issues)
    span = (
        "他把那只装着旧信的木匣子放回柜顶，又把柜门轻轻合上，屋里只剩下钟声在走，窗外的雨一直没停"
    )
    repeated = span + "。中间隔开一段别的话，说的是另一件事情。" + span
    tighter = apply_overrides(config, {"knobs": {"soft_check_repetition_ngram": 40}})
    assert any("连续重复" in item for item in run_soft_checks(repeated, tighter))
    assert not any("连续重复" in item for item in run_soft_checks(repeated, config))


def test_hooks_fill_placeholders_from_view():
    config = load_base_pack("v1")
    view = {"previous_ends_mid_action": True, "oldest_promise_age": 7, "same_method_rejects": 2}
    hooks = resolve_hooks(config, view)
    assert any("接上那个动作" in item for item in hooks)
    assert any("悬了 7 章" in item for item in hooks)
    assert any("2 次因为重复用过的办法" in item for item in hooks)
    assert resolve_hooks(config, {"oldest_promise_age": 5}) == []


def test_writing_view_derives_hook_inputs():
    run = _run()
    ctx = run.generation_context
    ctx["chapter_no"] = 8
    ctx["recent_chapters"] = [{"content": "他伸手去够那把刀"}]
    production = ctx["production"]
    production["same_method_rejects"] = 1
    view = chapter.writing_view(ctx, production)
    assert view["previous_ends_mid_action"] is True
    assert view["oldest_promise_age"] == 7
    assert view["same_method_rejects"] == 1


def test_skills_follow_declarative_conditions_and_switches():
    config = load_base_pack("v1")
    assert "读者答应的那件事当场发生" in resolve_skills(config, {"payoff_due": True})
    assert "读者答应的那件事当场发生" in resolve_skills(config, {"shows_rule": True})
    assert "动作戏用短句" in resolve_skills(config, {"choice": "冲进库房"})
    assert resolve_skills(config, {"choice": "坐下喝茶"}) == ""
    forced = apply_overrides(config, {"skills": {"transition": True, "action": False}})
    text = resolve_skills(forced, {"choice": "冲进库房"})
    assert "过渡章只写一件小事" in text
    assert "动作戏用短句" not in text


# ---------------------------------------------------------------- 本书覆盖层


async def _seed_work(session):
    owner = uuid.uuid4()
    work_id = uuid.uuid4()
    session.add(NovelPrincipalModel(id=owner, subject=f"c:{owner}"))
    session.add(
        StoryWorkModel(id=work_id, owner_id=owner, state="READY", genre="悬疑", latest_chapter_no=0)
    )
    session.add(StoryGoalModel(id=uuid.uuid4(), work_id=work_id, raw_intent="信任的代价"))
    await session.flush()
    return owner, work_id


def _canon_run(
    work_id, chapter_no, *, overlay_id="", first_pass=True, state="CANONIZED", attempt=1
):
    return ChapterRunModel(
        id=uuid.uuid4(),
        work_id=work_id,
        branch_id=uuid.UUID(int=0),
        chapter_no=chapter_no,
        attempt=attempt,
        state=state,
        generation_context={
            "executor": H2,
            "harness_overlay_id": overlay_id,
            "production": {"first_pass": first_pass},
        },
    )


@pytest.mark.asyncio
async def test_start_run_on_h2_canary_pins_harness(novel_db, monkeypatch):
    monkeypatch.setenv("NOVEL_EXECUTOR_CANARY", H2)
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
        ctx = run.generation_context
        assert ctx["executor"] == H2
        assert ctx["harness_id"] == "base/v1"
        assert ctx["harness_hash"] == load_base_pack("v1").content_hash
        assert ctx["harness_overlay"] == {}
        assert ctx["harness_overlay_id"] == ""
        assert production_protocol(run) == "chapter"


@pytest.mark.asyncio
async def test_provisional_overlay_is_pinned_then_adopted_after_trial(novel_db):
    overlay = {"knobs": {"previous_chapter_tail": 300}}
    async with novel_db() as s:
        _, work_id = await _seed_work(s)
        entry = await lib.record_entry(
            s,
            scope="work",
            scope_key=str(work_id),
            source_work_id=work_id,
            status="provisional",
            overlay_after=overlay,
            since_chapter=2,
            baseline={"chapters": 3, "ok": 1},
            config_diff=overlay,
        )
        ctx: dict = {}
        await pin_harness(s, work_id=work_id, context=ctx)
        assert ctx["harness_overlay"] == overlay
        assert ctx["harness_overlay_id"] == str(entry.id)
        assert ctx["harness_hash"] == apply_overrides(load_base_pack("v1"), overlay).content_hash
        for no, ok in ((2, True), (3, False), (4, True)):
            s.add(_canon_run(work_id, no, overlay_id=str(entry.id), first_pass=ok))
        await s.flush()
        ctx = {}
        await pin_harness(s, work_id=work_id, context=ctx)
        await s.refresh(entry)
        assert entry.status == "adopted"
        assert entry.adopted_count == 1
        assert ctx["harness_overlay_id"] == str(entry.id)


@pytest.mark.asyncio
async def test_trial_below_baseline_rolls_back_to_previous_overlay(novel_db):
    older = {"knobs": {"earlier_results_count": 3}}
    newer = {"knobs": {"earlier_results_count": 3, "previous_chapter_tail": 300}}
    async with novel_db() as s:
        _, work_id = await _seed_work(s)
        adopted = await lib.record_entry(
            s,
            scope="work",
            scope_key=str(work_id),
            source_work_id=work_id,
            status="adopted",
            overlay_after=older,
            since_chapter=1,
        )
        adopted.decided_at = datetime.now(UTC)
        trial = await lib.record_entry(
            s,
            scope="work",
            scope_key=str(work_id),
            source_work_id=work_id,
            status="provisional",
            overlay_after=newer,
            since_chapter=5,
            baseline={"chapters": 3, "ok": 3},
            parent_entry_id=adopted.id,
        )
        s.add(_canon_run(work_id, 5, overlay_id=str(trial.id), first_pass=True))
        s.add(_canon_run(work_id, 6, overlay_id=str(trial.id), first_pass=False))
        s.add(_canon_run(work_id, 7, overlay_id=str(trial.id), state="TERMINAL_FAILED"))
        await s.flush()
        ctx: dict = {}
        await pin_harness(s, work_id=work_id, context=ctx)
        await s.refresh(trial)
        assert trial.status == "rolled_back"
        assert "低于基线" in trial.rollback_reason
        assert ctx["harness_overlay"] == older
        assert ctx["harness_overlay_id"] == str(adopted.id)


@pytest.mark.asyncio
async def test_regenerating_a_failed_chapter_does_not_erase_it_from_the_trial(novel_db):
    """覆盖层把一章写崩、重写后过了：试用仍记这一章没一次过，基线同一口径。"""
    from regent.novel.harnessing.overlay import trial_outcome

    async with novel_db() as s:
        _, work_id = await _seed_work(s)
        trial = await lib.record_entry(
            s,
            scope="work",
            scope_key=str(work_id),
            source_work_id=work_id,
            status="provisional",
            overlay_after={"knobs": {"previous_chapter_tail": 300}},
            since_chapter=5,
        )
        tid = str(trial.id)
        s.add(_canon_run(work_id, 5, overlay_id=tid))
        s.add(_canon_run(work_id, 6, overlay_id=tid, state="TERMINAL_FAILED"))
        s.add(_canon_run(work_id, 6, overlay_id=tid, attempt=2))
        s.add(_canon_run(work_id, 7, overlay_id=tid))
        await s.flush()
        assert await trial_outcome(s, trial) == {"chapters": 3, "ok": 2}
        assert await first_pass_baseline(s, work_id, before_chapter=8) == {"chapters": 3, "ok": 2}


@pytest.mark.asyncio
async def test_record_waits_while_another_trial_is_open(novel_db):
    async with novel_db() as s:
        _, work_id = await _seed_work(s)
        await lib.record_entry(
            s,
            scope="work",
            scope_key=str(work_id),
            source_work_id=work_id,
            status="provisional",
            overlay_after={"knobs": {"previous_chapter_tail": 300}},
            since_chapter=2,
        )
        hit = await lib.record_entry(s, scope="global", experience="缩短前章尾段")
        run = _run()
        run.chapter_no = 3
        production = run.generation_context["production"]
        production.update(
            h2_delivered_from="probe",
            h2_overrides={"knobs": {"earlier_results_count": 2}},
            h2_lesson={
                "kind": "generic",
                "experience": "林晚那样的人物要先动手",
                "cited": [str(hit.id)],
            },
        )
        entry = await record_verified_round(
            s, work=SimpleNamespace(id=work_id, genre="悬疑"), run=run, production=production
        )
        assert entry.status == "candidate"
        assert entry.overlay_after is None
        assert entry.scope == "work"  # 经验里带了本书人名，不进全局
        await s.flush()
        await s.refresh(hit)
        assert hit.effective_count == 1
        assert (
            await record_verified_round(
                s, work=SimpleNamespace(id=work_id), run=run, production=production
            )
            is None
        )


@pytest.mark.asyncio
async def test_baseline_only_counts_chapters_that_recorded_first_pass(novel_db):
    """基线只认 CHECK 真写下 ``first_pass`` 的章。

    老运行哪怕干干净净（没改稿没重写）也不计入：反推只能得出 True，含老章节的基线
    恒为 100%，试用判定就要求试用章也 100% 一次过——护栏被推向必然回退。
    """

    def _arm_run(work_id, chapter_no, executor, **production):
        return ChapterRunModel(
            id=uuid.uuid4(),
            work_id=work_id,
            branch_id=uuid.UUID(int=0),
            chapter_no=chapter_no,
            attempt=1,
            state="CANONIZED",
            generation_context={"executor": executor, "production": production},
        )

    async with novel_db() as s:
        _, work_id = await _seed_work(s)
        default = executor_app.CHAPTER_EXECUTOR
        # 1–3 是改动之前的默认臂运行：只有阶段与改稿计数，没有首过信号
        s.add(_arm_run(work_id, 1, default, phase="DONE", repairs=0, rewrites=0))
        s.add(_arm_run(work_id, 2, default, phase="DONE", repairs=1, rewrites=0))
        s.add(_arm_run(work_id, 3, default, phase="DONE", rewrites=1))
        s.add(_arm_run(work_id, 6, executor_app.SCENE_EXECUTOR, phase="DONE"))
        s.add(_arm_run(work_id, 7, default, phase="REPAIR"))
        s.add(_canon_run(work_id, 8, first_pass=True))
        s.add(_canon_run(work_id, 9, first_pass=False))
        await s.commit()
        assert await first_pass_baseline(s, work_id, before_chapter=4) == {"chapters": 0, "ok": 0}
        # 第 8、9 章写了真实信号，一过一没过——基线不是清一色 True
        assert await first_pass_baseline(s, work_id, before_chapter=10) == {"chapters": 2, "ok": 1}


def test_first_pass_reads_legacy_key_and_refuses_to_guess():
    from regent.novel.harnessing.overlay import _first_pass

    def _ctx(executor: str, production: dict) -> SimpleNamespace:
        return SimpleNamespace(generation_context={"executor": executor, "production": production})

    default = executor_app.CHAPTER_EXECUTOR
    assert _first_pass(_ctx(H2, {"h2_first_pass": False})) is False
    assert _first_pass(_ctx(default, {"first_pass": True})) is True
    assert _first_pass(_ctx(default, {"first_pass": False})) is False
    # 老运行只有阶段与改稿计数：干净章也不猜，猜出来的只会是 True
    assert _first_pass(_ctx(default, {"phase": "DONE", "repairs": 0, "rewrites": 0})) is None
    assert _first_pass(_ctx(default, {"phase": "DONE", "repairs": 1})) is None
    assert _first_pass(_ctx(default, {"phase": "REPAIR"})) is None
    assert _first_pass(_ctx(executor_app.SCENE_EXECUTOR, {"phase": "DONE"})) is None


# ---------------------------------------------------------------- 改进库


@pytest.mark.asyncio
async def test_library_search_is_scope_filtered(novel_db):
    mine, other = uuid.uuid4(), uuid.uuid4()
    async with novel_db() as s:
        await lib.record_entry(s, scope="global", experience="全局", failure_code="正文不足字")
        await lib.record_entry(s, scope="work", scope_key=str(mine), experience="本书")
        await lib.record_entry(s, scope="work", scope_key=str(other), experience="别的书")
        await lib.record_entry(s, scope="genre", genre_tag="悬疑", experience="同类型")
        await lib.record_entry(s, scope="genre", genre_tag="言情", experience="别的类型")
        gone = await lib.record_entry(s, scope="global", experience="已回退")
        await lib.record_rollback(s, gone.id, reason="变差")
        await s.commit()
        hits = await lib.search(
            s, work_id=mine, genre_tag="悬疑", failure_code="正文不足字", limit=10
        )
        assert {h["experience"] for h in hits} == {"全局", "本书", "同类型"}


@pytest.mark.asyncio
async def test_provisional_entry_is_visible_only_to_its_own_book(novel_db):
    mine, other = uuid.uuid4(), uuid.uuid4()
    async with novel_db() as s:
        await lib.record_entry(
            s,
            scope="global",
            experience="通用试用",
            status="provisional",
            source_work_id=mine,
            overlay_after={"knobs": {"previous_chapter_tail": 300}},
        )
        await lib.record_entry(
            s,
            scope="global",
            experience="通用已采纳",
            status="adopted",
            source_work_id=other,
        )
        await lib.record_entry(s, scope="global", experience="通用候选")
        await s.commit()
        assert {h["experience"] for h in await lib.search(s, work_id=mine, limit=10)} == {
            "通用试用",
            "通用已采纳",
            "通用候选",
        }
        assert {h["experience"] for h in await lib.search(s, work_id=other, limit=10)} == {
            "通用已采纳",
            "通用候选",
        }
        assert {h["experience"] for h in await lib.search(s, limit=10)} == {
            "通用已采纳",
            "通用候选",
        }


@pytest.mark.asyncio
async def test_prune_only_retires_aged_unused_candidates(novel_db):
    async with novel_db() as s:
        stale = await lib.record_entry(s, scope="global", experience="放久了")
        fresh = await lib.record_entry(s, scope="global", experience="刚写的")
        used = await lib.record_entry(s, scope="global", experience="有效过")
        used.effective_count = 1
        trial = await lib.record_entry(
            s, scope="work", scope_key="w", status="provisional", experience="试用中"
        )
        for row in (stale, used, trial):
            row.created_at = datetime.now(UTC) - timedelta(days=60)
        fresh.created_at = datetime.now(UTC)
        await s.flush()
        assert await lib.prune_unused(s, older_than_days=30) == 1
        await s.flush()
        await s.refresh(stale)
        assert stale.status == "retired"
        for row in (fresh, used, trial):
            await s.refresh(row)
            assert row.status != "retired"


# ---------------------------------------------------------------- 回放


@pytest.mark.asyncio
async def test_replay_hash_matches_production_call(monkeypatch):
    run = _run()
    run.generation_context["harness_overlay"] = {"knobs": {"previous_chapter_tail": 300}}
    run.generation_context["recent_chapters"] = [{"content": "上一章" * 300}]
    broker = FakeBroker({"ChapterProse": lambda *_: chapter.ChapterProse(content=GOOD)})
    monkeypatch.setattr("regent.novel.application.directing_chapter._call", broker)
    await _tick(None, run, _work(), broker)
    assert len(broker.calls[0]["payload"]["previous_chapter"]) == 300
    prompt_hash = hashlib.sha256(broker.calls[0]["system"].encode("utf-8")).hexdigest()
    result = check_write_replay(run.generation_context, stored_prompt_hash=prompt_hash)
    assert result["ok"] is True
    assert result["stored"] == compute_payload_hash(broker.calls[0]["payload"])
    # 只核入参不算通过：覆盖层改的是提示词，缺 prompt_hash 就证明不了配置被钉住
    partial = check_write_replay(run.generation_context)
    assert partial["input_hash_ok"] is True
    assert partial["prompt_hash_ok"] is None
    assert partial["ok"] is False
    run.generation_context["canon"] = [{"statement": "后来改过的正史"}]
    assert check_write_replay(run.generation_context, stored_prompt_hash=prompt_hash)["ok"] is False
