"""PR-C/D/E 行为测试：soft 边界、修订无进展、恢复路由、连续创作。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from regent.model import ModelUsage, StructuredModelResponse
from regent.novel.application import direction as d
from regent.novel.application.works_continuation import (
    ensure_next_run,
    get_continuation_policy,
    set_continuation_policy,
)
from regent.novel.application.works_run_control import resume_work
from regent.novel.domain.errors import ProductionStopped
from regent.novel.domain.models import WorkResumeOut
from regent.novel.domain.prose_front_gates import review_issues_may_coerce
from regent.novel.domain.repair_locate import content_hash
from regent.novel.domain.scene_card import SceneCard, StructuredBeat
from regent.novel.domain.script_protocol import (
    PROTOCOL_SCRIPT_SCENE,
    ChapterScript,
    ScriptChoice,
    empty_script_state,
)
from regent.novel.domain.states import ChapterRunState, StoryWorkState
from regent.novel.application.chapter_review import (
    _coerce_review_soft,
    _review_issues_soft_after_creative,
)

PARA = "雨水沿窗棂流下，两个人仍旧没有开口，钥匙静静躺在桌面正中。"


class Provider:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    async def generate_structured(self, *, response_model, **kwargs):
        output = self.outputs.pop(0)
        return StructuredModelResponse(
            output=output.model_copy(deep=True), usage=ModelUsage(10, 20), model="test"
        )


class Session:
    def __init__(self):
        self.events = []
        self.flushed = 0

    async def scalar(self, query):
        return None

    async def scalars(self, query):
        return SimpleNamespace(all=lambda: [])

    def add(self, row):
        pass

    async def flush(self):
        self.flushed += 1

    async def commit(self):
        pass

    async def execute(self, *args, **kwargs):
        return SimpleNamespace(scalar_one_or_none=lambda: None, all=lambda: [])


def _card(sid: str) -> SceneCard:
    return SceneCard(
        scene_id=sid,
        purpose="x",
        setting="s",
        entry_state="e",
        protagonist_objective="o",
        opposition="p",
        core_choice="c",
        emotion_arc="a",
        exit_change="x",
        beats=[StructuredBeat(beat_id="b", text="t", must_show=True)],
        target_chars=400,
    )


def _scene_base(texts: list[str], phase: str = "VALIDATE"):
    cards = [_card("s0").model_dump(mode="json"), _card("s1").model_dump(mode="json")]
    packet = {
        "script": ChapterScript(
            title_line="选定",
            end_change="x",
            cost="y",
            cost_type="y",
            power_payoff="z",
            beats=["b"],
        ).model_dump(mode="json"),
        "direction": ScriptChoice(selected_id="alpha", must_land_beats=["b"]).model_dump(
            mode="json"
        ),
        "must_land_beats": ["b"],
    }
    sp = empty_script_state()
    sp.update(
        {
            "selected_id": "alpha",
            "production_packet": packet,
            "choice": packet["direction"],
            "candidates": {"alpha": packet["script"]},
            "creative_repairs": 0,
            "scene_plan": {"cards": cards, "chapter_goal": "x"},
            "scene_index": len(texts) - 1,
            "scene_texts": list(texts),
            "scene_state_trail": [{"i": i} for i in range(len(texts))],
            "working_state": {},
            "chapter_validate_repairs": 0,
        }
    )
    chapter = "\n\n".join(texts)
    take = {
        "take_no": 1,
        "content": chapter,
        "events": [],
        "status": "DRAFT",
        "artifact": "PROSE",
        "brief": {"purpose": "x"},
        "turn": 0,
        "performances": [],
        "round_actions": [],
        "revisions": 0,
        "prose_versions": [chapter],
        "state_before": {},
        "validation": {"passed": False, "issues": [], "facts": []},
    }
    production = {
        "schema_version": 1,
        "protocol": PROTOCOL_SCRIPT_SCENE,
        "phase": phase,
        "cast": {},
        "takes": [take],
        "accepted": [],
        "decisions": [],
        "working_state": {},
        "script_protocol": sp,
        "call_count": 0,
        "committed_minor": 0,
        "scene_index": 0,
        "call_key_version": 2,
    }
    run = SimpleNamespace(
        id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        chapter_no=1,
        generation_context={
            "architecture_version": d.SCRIPT_SCENE_ARCHITECTURE,
            "executor": d.SCRIPT_SCENE_ARCHITECTURE,
            "executor_version": "director_script_scene@1",
            "canon": [],
            "actual_state": {},
            "recent_chapters": [],
            "memory": [],
            "production": production,
        },
        performances=[],
        review={},
        user_guidance={},
        current_step="PRODUCE",
        content=chapter,
        title="第1章",
        word_count=len(chapter),
        input_version=1,
    )
    return run, SimpleNamespace(id=uuid.uuid4())


# --- PR-C soft 边界 ---


def test_review_coerce_structured_only():
    assert review_issues_may_coerce(["[editor-soft:x]"])
    assert not review_issues_may_coerce(["第二场跳场"])
    assert not review_issues_may_coerce("[front:redundant] 复读")
    assert not _review_issues_soft_after_creative(["第二场跳场"])
    assert _review_issues_soft_after_creative(["[soft-cont:space] 空间省略"])


def test_coerce_review_soft_preserves_original():
    run = SimpleNamespace(
        review={"passed": False, "continuity_issues": ["跳场"]},
        generation_context={"production": {}},
    )
    _coerce_review_soft(run, {}, ["[editor-soft:x]"], flag="coerced_after_creative")
    assert run.review["passed"] is True
    assert run.review["verdict_original"]["passed"] is False
    assert run.review["accept_decision"] == "coerced_soft"


@pytest.mark.asyncio
async def test_validate_no_progress_same_hash_stops():
    from regent.novel.domain.repair_locate import locate_fail_messages

    texts = ["独有甲。独有甲。" + "铺垫。" * 20, "独有乙。" * 40]
    run, work = _scene_base(texts)
    sp = run.generation_context["production"]["script_protocol"]
    chapter = "\n\n".join(texts)
    fail_loc = (
        "[front:redundant] 同章出现完全相同段落重复；摘录「"
        + "独有甲。"
        + "」"
    )
    cards = list((sp.get("scene_plan") or {}).get("cards") or [])
    located = locate_fail_messages([fail_loc], scene_texts=texts, cards=cards)
    issue_id = located[0].issue_id if located else "front:redundant#x"
    sp["chapter_validate_repairs"] = 1
    sp["pending_repair_ticket"] = {
        "scene_index": 0,
        "scene_id": "s0",
        "issue_ids": [issue_id],
        "issues": [located[0].as_dict()] if located else [],
        "base_content_hash": content_hash(chapter),
        "must_remove_quotes": [],
        "verify_rules": [],
    }
    run.generation_context["production"]["takes"][0]["content"] = chapter
    provider = Provider(
        [
            d.ScriptChapterValidation(
                hard_fails=[fail_loc], soft_notes=[], facts=[]
            )
        ]
    )
    with pytest.raises(ProductionStopped, match="修订无进展"):
        await d.produce_tick(Session(), provider=provider, work=work, run=run)


# --- PR-D resume ---


def _work(state: str, *, bible: dict | None = None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        state=state,
        version=1,
        latest_chapter_no=2,
        story_bible=dict(bible or {}),
        story_bible_locked_at="2026-01-01",
        deleted_at=None,
        total_volume_count=1,
    )


class FakeDB:
    """最小 resume 依赖：_latest_run / _get_owned_work / flush / events。"""

    def __init__(self, work, run):
        self.work = work
        self.run = run
        self.events = []

    async def scalar(self, query):
        return self.run

    async def flush(self):
        pass

    def add(self, row):
        pass


@pytest.mark.asyncio
async def test_resume_terminal_failed_returns_blocker_not_running():
    work = _work(StoryWorkState.FAILED.value)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        work_id=work.id,
        branch_id=work.branch_id,
        chapter_no=3,
        attempt=4,
        state=ChapterRunState.TERMINAL_FAILED.value,
        current_step="VALIDATE",
        generation_context={"production": {"phase": "VALIDATE"}},
        version=1,
        lease_expires_at=None,
    )
    db = FakeDB(work, run)

    async def _owned(session, *, owner_id, work_id):
        return work

    async def _latest(session, *, work):
        return run

    async def _append(session, *, work_id, event_type, data=None, **kwargs):
        db.events.append((event_type, data))

    import regent.novel.application.works_run_control as rc

    orig_owned, orig_latest, orig_append = (
        rc._get_owned_work,
        rc._latest_run,
        rc.append_event,
    )
    rc._get_owned_work = _owned
    rc._latest_run = _latest
    rc.append_event = _append
    try:
        out = await resume_work(db, owner_id=uuid.uuid4(), work_id=work.id)
    finally:
        rc._get_owned_work = orig_owned
        rc._latest_run = orig_latest
        rc.append_event = orig_append
    assert isinstance(out, WorkResumeOut)
    assert out.blocker_code == "content_hard_fail"
    assert work.state == StoryWorkState.FAILED.value
    assert "regenerate_chapter" in out.recommended_actions
    assert any(e[0] == "work.resume_blocked" for e in db.events)


@pytest.mark.asyncio
async def test_resume_canonized_without_policy_advises_start_run():
    work = _work(StoryWorkState.FAILED.value)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        work_id=work.id,
        branch_id=work.branch_id,
        chapter_no=2,
        attempt=1,
        state=ChapterRunState.CANONIZED.value,
        current_step="CANON",
        generation_context={},
        version=1,
        lease_expires_at=None,
    )
    db = FakeDB(work, run)

    async def _owned(*a, **k):
        return work

    async def _latest(*a, **k):
        return run

    async def _append(*a, **k):
        return None

    import regent.novel.application.works_run_control as rc

    o1, o2, o3 = rc._get_owned_work, rc._latest_run, rc.append_event
    rc._get_owned_work, rc._latest_run, rc.append_event = _owned, _latest, _append
    try:
        out = await resume_work(db, owner_id=uuid.uuid4(), work_id=work.id)
    finally:
        rc._get_owned_work, rc._latest_run, rc.append_event = o1, o2, o3
    assert out.blocker_code in ("continuation_not_authorized", "no_active_run", "")
    assert "start_run" in out.recommended_actions
    assert work.state != StoryWorkState.RUNNING.value or out.blocker_code == ""


# --- PR-E continuation ---


def test_continuation_policy_default_off():
    work = _work(StoryWorkState.RUNNING.value)
    assert get_continuation_policy(work).enabled is False
    pol = set_continuation_policy(work, enabled=True, target_chapter_no=5)
    assert pol.enabled is True
    assert pol.target_chapter_no == 5
    assert get_continuation_policy(work).enabled is True


@pytest.mark.asyncio
async def test_ensure_next_run_requires_policy():
    work = _work(StoryWorkState.RUNNING.value)
    completed = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_no=2,
        state=ChapterRunState.CANONIZED.value,
    )

    class S:
        async def scalar(self, q):
            return None

        async def flush(self):
            pass

        def add(self, row):
            pass

        async def scalars(self, q):
            return SimpleNamespace(all=lambda: [])

    out = await ensure_next_run(S(), work=work, completed_run=completed)
    assert out is None


@pytest.mark.asyncio
async def test_ensure_next_run_idempotent_and_respects_target():
    work = _work(StoryWorkState.RUNNING.value)
    set_continuation_policy(work, enabled=True, target_chapter_no=3)
    completed = SimpleNamespace(
        id=uuid.uuid4(),
        chapter_no=3,
        state=ChapterRunState.CANONIZED.value,
    )

    class S:
        async def scalar(self, q):
            return None

        async def flush(self):
            pass

        def add(self, row):
            pass

        async def scalars(self, q):
            return SimpleNamespace(all=lambda: [])

    out = await ensure_next_run(S(), work=work, completed_run=completed)
    # 目标章=3 已完成 → 不开第 4 章
    assert out is None


def test_call_key_vrep_version_compat():
    """缺省/显式 1：vrep=0 时不得带 vrep 段；显式 2 含 vrep0。"""
    sp = empty_script_state()
    sp.update({"scene_index": 0, "creative_repairs": 1, "chapter_validate_repairs": 0})
    production = {"decisions": [{}], "call_key_version": 1}
    run = SimpleNamespace(input_version=4, generation_context={})
    cid = d._script_scene_command_id(run, production, sp, "WRITE_SCENE")
    assert ":vrep" not in cid
    # 缺省字段 → 旧格式（F6）
    production_legacy = {"decisions": [{}]}
    cid_leg = d._script_scene_command_id(run, production_legacy, sp, "WRITE_SCENE")
    assert ":vrep" not in cid_leg
    # 根 context 写 2 时 hydrate/解析应带 vrep
    run_new = SimpleNamespace(input_version=4, generation_context={"call_key_version": 2})
    cid_ctx = d._script_scene_command_id(run_new, {"decisions": [{}]}, sp, "WRITE_SCENE")
    assert ":vrep0" in cid_ctx
    production["call_key_version"] = 2
    cid2 = d._script_scene_command_id(run, production, sp, "WRITE_SCENE")
    assert ":vrep0" in cid2


def test_projection_volume_expansion_actions():
    from regent.novel.application.works_projection import projection_for
    from regent.novel.domain.states import StoryWorkState

    plain = projection_for(StoryWorkState.PENDING_DECISION.value, pending=0)
    assert "open_decision" in plain.available_actions

    expand = projection_for(
        StoryWorkState.PENDING_DECISION.value,
        pending=0,
        pending_kind="volume_expansion",
    )
    assert expand.public_stage == "volume_expansion_pending"
    assert "expand_volume" in expand.available_actions
    assert "set_ending_intent" in expand.available_actions
    assert "open_decision" not in expand.available_actions


def test_pending_kind_from_ending_reason():
    from regent.novel.application.works_query import _pending_kind_from_run

    run = SimpleNamespace(
        generation_context={
            "ending_decision": {
                "choice": "undecided",
                "reason": "已判定应继续下一卷，等待用户确认扩卷：弧未完",
            }
        }
    )
    assert _pending_kind_from_run(run) == "volume_expansion"
    run2 = SimpleNamespace(
        generation_context={"ending_decision": {"choice": "undecided", "reason": "需选人"}}
    )
    assert _pending_kind_from_run(run2) is None
