"""章末交接绑定，以及审校失败按计划层/正文层分流。"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from regent.novel.application.chapter_accept import finalize_chapter_acceptance
from regent.novel.application.chapter_review import _rewind_script_after_review
from regent.novel.domain.chapter_handoff import (
    annotate_chapter_handoff,
    build_chapter_handoff,
    continuation_issues,
    entry_summary_from_handoff,
    handoff_bound,
    initial_entry_from_previous,
    review_repair_layer,
    select_previous_handoff,
)
from regent.novel.domain.errors import ProductionStopped
from regent.novel.domain.script_protocol import PROTOCOL_SCRIPT_SCENE, empty_script_state


def _prose() -> str:
    return "他靠在门后，左臂还在渗血，追兵的脚步停在门外。"


def test_handoff_quotes_tail_and_rejects_stale_text():
    text = _prose()
    handoff = build_chapter_handoff(
        chapter_no=8,
        content=text,
        actual_state={"wound": "left_arm", "place": "door"},
        review_content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )
    assert handoff_bound(handoff, text)
    assert text.endswith(handoff["tail_excerpt"])
    assert handoff["review_matches_final"] is True
    summary = entry_summary_from_handoff(handoff)
    assert "左臂还在渗血" in summary
    assert "客栈庆功" not in summary
    assert not handoff_bound(handoff, text + "他已经到了客栈。")


def test_previous_handoff_uses_only_the_immediate_chapter():
    text = _prose()
    bound = build_chapter_handoff(chapter_no=8, content=text, actual_state={"place": "door"})
    older = build_chapter_handoff(chapter_no=7, content="更早的正文。", actual_state={})
    stale_context = {"chapter_handoff": {**bound, "content_hash": "0" * 64}}
    missing, missing_status = annotate_chapter_handoff("另一份正文", stale_context)
    assert missing is None
    assert missing_status == "stale"
    kept, kept_status = annotate_chapter_handoff(text, {"chapter_handoff": bound})
    assert kept_status == "bound"
    assert kept == bound
    chosen, status = select_previous_handoff(
        [
            {"chapter_handoff": None, "handoff_status": "stale"},
            {"chapter_handoff": older, "handoff_status": "bound"},
        ]
    )
    assert chosen is None
    assert status == "stale"
    chosen, status = select_previous_handoff(
        [{"chapter_handoff": kept, "handoff_status": "bound"}]
    )
    assert status == "bound"
    opening, entries = initial_entry_from_previous(chosen)
    assert entries == [opening]
    assert "左臂还在渗血" in opening


def test_continuation_requires_a_declared_bridge():
    assert continuation_issues(mode="", transition_note="", has_previous=False) == []
    assert continuation_issues(mode="direct", transition_note="", has_previous=True) == []
    assert continuation_issues(mode="", transition_note="", has_previous=True) == [
        "continuation_mode_required"
    ]
    assert continuation_issues(
        mode="time_jump", transition_note="", has_previous=True
    ) == ["transition_note_required"]
    assert continuation_issues(
        mode="pov_switch", transition_note="改由追兵视角看见门后的血", has_previous=True
    ) == []


def test_finalize_stores_handoff_bound_to_final_prose():
    text = _prose()
    digest = hashlib.sha256(text.encode()).hexdigest()
    run = SimpleNamespace(
        chapter_no=8,
        content=text,
        review={"passed": True},
        generation_context={},
    )
    production = {
        "working_state": {"wound": "left_arm"},
        "accepted": [],
        "takes": [],
        "script_protocol": {},
        "review_history": [{"content_hash": digest, "passed": True}],
    }
    finalize_chapter_acceptance(run, production, review_passed=True)
    handoff = run.generation_context["chapter_handoff"]
    assert handoff_bound(handoff, text)
    assert handoff["content_hash"] == run.generation_context["validated_content_hash"]
    assert handoff["actual_state"]["wound"] == "left_arm"
    assert handoff["review_matches_final"] is True
    assert "story_ledger" in run.generation_context


def test_review_plan_fault_returns_to_compile_and_drops_old_prose():
    sp = empty_script_state()
    sp.update(
        {
            "scene_plan": {"cards": [{"scene_id": "s0"}], "chapter_goal": "旧目标"},
            "scene_texts": ["已经写好的一场。"],
            "production_packet": {"script": {"title": "保留制作包"}},
            "working_summary": "后场摘要",
        }
    )
    production = {
        "protocol": PROTOCOL_SCRIPT_SCENE,
        "phase": "DONE",
        "takes": [{"status": "ACCEPTED", "content": "已经写好的一场。"}],
        "accepted": [0],
        "script_protocol": sp,
    }
    run = SimpleNamespace(
        content="已经写好的一场。",
        review={"passed": True},
        generation_context={"production": production},
    )
    ok = _rewind_script_after_review(
        run,
        production,
        issues=["同一事件在两场重复展开，分场责任场错误"],
        failed_scene_index=0,
        proto=PROTOCOL_SCRIPT_SCENE,
    )
    assert ok is False
    assert production["phase"] == "COMPILE_SCENES"
    assert sp["scene_texts"] == []
    assert sp["scene_plan"] == {}
    assert production["accepted"] == []
    assert production["takes"][0]["status"] == "SUPERSEDED"
    assert sp["production_packet"]["script"]["title"] == "保留制作包"
    assert "分场" in sp["review_plan_repair"][0]
    assert run.review["passed"] is False
    assert run.review["invalidated_reason"] == "plan_rewind"


def test_review_plan_rewind_stops_after_two_returns():
    production = {
        "protocol": PROTOCOL_SCRIPT_SCENE,
        "phase": "DONE",
        "takes": [],
        "accepted": [],
        "script_protocol": empty_script_state(),
    }
    run = SimpleNamespace(content="正文", review={}, generation_context={})
    for _ in range(2):
        _rewind_script_after_review(
            run,
            production,
            issues=["章节目标没有落到任何场"],
            failed_scene_index=0,
            proto=PROTOCOL_SCRIPT_SCENE,
        )
    with pytest.raises(ProductionStopped, match="分场返工次数已达上限"):
        _rewind_script_after_review(
            run,
            production,
            issues=["章节目标没有落到任何场"],
            failed_scene_index=0,
            proto=PROTOCOL_SCRIPT_SCENE,
        )


def test_prior_chapter_fact_is_not_rewritten_as_current_scene():
    production = {
        "protocol": PROTOCOL_SCRIPT_SCENE,
        "phase": "DONE",
        "takes": [{"status": "ACCEPTED"}],
        "accepted": [0],
        "script_protocol": empty_script_state(),
    }
    run = SimpleNamespace(content="正文", review={}, generation_context={})
    with pytest.raises(ProductionStopped, match="前章事实"):
        _rewind_script_after_review(
            run,
            production,
            issues=["[prior_canon] 旧章事实与本章伤势冲突"],
            failed_scene_index=0,
            proto=PROTOCOL_SCRIPT_SCENE,
        )
    assert production["phase"] == "DONE"
    assert production["takes"][0]["status"] == "ACCEPTED"


def test_expression_fault_stays_on_scene_rewrite_layer():
    assert review_repair_layer(["[front:redundant] 同章复读"]) == "expression"
    assert review_repair_layer(["钥匙应仍在甲手中"]) == "scene_event"
    assert review_repair_layer(["同一事件被两场重演"]) == "scene_plan"
