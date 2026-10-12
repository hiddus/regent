"""事件身份层反证：换名重演 / 角色洗白 / 空引用 / 非法 role / 义务落场。

真机形态（第 3 章 s1/s3 受刑）：同一故事事件被排进两场。
只靠字面 beat 匹配或 role 自由串会全部漏检。
"""

from __future__ import annotations

import pytest

from regent.novel.domain.scene_card import EventRef, SceneCard
from regent.novel.domain.script_events import (
    ScriptEvent,
    _events_look_synonymous,
    ground_orphan_event_roles,
    validate_plan_event_refs,
    validate_script_events,
)


def _card(scene_id: str, refs: list[dict], acceptance_ids: list[str] | None = None) -> dict:
    return {
        "scene_id": scene_id,
        "event_refs": refs,
        "acceptance_ids": list(acceptance_ids or []),
    }


def _occ(event_id: str, beat_id: str, role: str = "occurrence") -> dict:
    return {"event_id": event_id, "beat_id": beat_id, "role": role}


SEARCH_EVENTS = [
    {"event_id": "evt_search", "description": "搜身", "event_type": "occurrence", "aliases": []}
]

TORTURE_EVENTS = [
    {
        "event_id": "evt_torture",
        "description": "老瘸子受刑",
        "event_type": "occurrence",
        "aliases": ["老瘸子被抽了七鞭"],
    }
]


def test_schema_allows_empty_but_validate_blocks():
    """schema 允许骨架中间态空 refs；生产 COMPILE 由 validate 硬拦。"""
    SceneCard(scene_id="s1", purpose="x", event_refs=[])
    cards = [_card("s1", []), _card("s3", [])]
    errs = validate_plan_event_refs(cards, SEARCH_EVENTS)
    assert any("event_refs 为空" in e for e in errs)


def test_schema_rejects_free_role():
    with pytest.raises(Exception):
        EventRef(event_id="evt_a", beat_id="b1", role="new_round")


def test_a_same_id_two_occurrence_blocked():
    cards = [
        _card("s1", [_occ("evt_search", "b1")]),
        _card("s3", [_occ("evt_search", "b14")]),
    ]
    errs = validate_plan_event_refs(cards, SEARCH_EVENTS)
    assert any("同一事件只能在一个场景发生" in e for e in errs)


def test_orphan_consequence_becomes_the_occurrence():
    """真机停章：s2 只把决定标成后果，清单里也没有另一场 occurrence。"""
    events = [
        {"event_id": "evt_other", "description": "其他", "event_type": "occurrence", "aliases": []},
        {
            "event_id": "evt_decision_to_find_watch",
            "description": "决定去找表",
            "event_type": "consequence",
            "aliases": [],
        },
    ]
    cards = [
        _card("s1", [_occ("evt_other", "b1")]),
        _card("s2", [_occ("evt_decision_to_find_watch", "b2", role="consequence")]),
    ]
    grounded = ground_orphan_event_roles(cards, events)
    assert grounded[0]["event_refs"][0]["role"] == "occurrence"
    assert grounded[1]["event_refs"][0]["role"] == "occurrence"
    assert validate_plan_event_refs(grounded, events) == []


def test_existing_occurrence_is_not_rewritten_by_grounding():
    cards = [
        _card("s1", [_occ("evt_search", "b1")]),
        _card("s3", [_occ("evt_search", "b14", role="consequence")]),
    ]
    grounded = ground_orphan_event_roles(cards, SEARCH_EVENTS)
    assert grounded[1]["event_refs"][0]["role"] == "consequence"
    errs = validate_plan_event_refs(grounded, SEARCH_EVENTS)
    assert any("consequence 须是独立 event_id" in e for e in errs)


def test_b_role_launder_consequence_blocked():
    cards = [
        _card("s1", [_occ("evt_search", "b1")]),
        _card("s3", [_occ("evt_search", "b14", role="consequence")]),
    ]
    errs = validate_plan_event_refs(cards, SEARCH_EVENTS)
    assert any("consequence 须是独立 event_id" in e for e in errs)


def test_d_empty_refs_blocked():
    cards = [_card("s1", []), _card("s3", [])]
    errs = validate_plan_event_refs(cards, SEARCH_EVENTS)
    assert any("event_refs 为空" in e for e in errs)


def test_e_synonym_ids_blocked():
    """第 3 章真机形态：老瘸子受刑 / 老瘸子被抽了七鞭 换名排进 s1+s3。"""
    events = [
        {
            "event_id": "evt_torture",
            "description": "老瘸子受刑",
            "event_type": "occurrence",
            "aliases": ["老瘸子被抽了七鞭"],
        },
        {
            "event_id": "evt_beaten",
            "description": "老瘸子被抽了七鞭",
            "event_type": "occurrence",
            "aliases": [],
        },
    ]
    cards = [
        _card("s1", [_occ("evt_torture", "b6")]),
        _card("s3", [_occ("evt_beaten", "b14")]),
    ]
    errs = validate_plan_event_refs(cards, events)
    assert any("同义" in e or "换名" in e for e in errs)


def test_f_illegal_role_new_round_blocked():
    cards = [
        _card("s1", [_occ("evt_search", "b1")]),
        _card("s3", [_occ("evt_search", "b14", role="new_round")]),
    ]
    errs = validate_plan_event_refs(cards, SEARCH_EVENTS)
    assert any("role=new_round 非法" in e or "只允许 occurrence" in e for e in errs)


def test_reference_before_occurrence_blocked():
    cards = [
        _card("s1", [_occ("evt_search", "b1", role="reference")]),
        _card("s3", [_occ("evt_search", "b14")]),
    ]
    errs = validate_plan_event_refs(cards, SEARCH_EVENTS)
    assert any("引用只能出现在其后" in e or "尚无任何场景作为 occurrence" in e for e in errs)


def test_g_acceptance_owner_without_occurrence_blocked():
    cards = [_card("s1", [_occ("evt_search", "b1", role="reference")], acceptance_ids=["a3"])]
    acc = [{"item_id": "a3", "required": True, "scene_ids": ["s1"]}]
    errs = validate_plan_event_refs(cards, SEARCH_EVENTS, acceptance_items=acc)
    assert any("义务无法落场" in e for e in errs)


def test_valid_plan_passes():
    events = [
        {"event_id": "evt_search", "description": "搜身", "event_type": "occurrence", "aliases": []},
        {
            "event_id": "evt_night",
            "description": "夜里按小腹",
            "event_type": "occurrence",
            "aliases": [],
        },
    ]
    cards = [
        _card("s1", [_occ("evt_search", "b1")], acceptance_ids=["a1"]),
        _card("s3", [_occ("evt_night", "b9")], acceptance_ids=["a2"]),
    ]
    acc = [
        {"item_id": "a1", "required": True, "scene_ids": ["s1"]},
        {"item_id": "a2", "required": True, "scene_ids": ["s3"]},
    ]
    assert validate_plan_event_refs(cards, events, acceptance_items=acc) == []


def test_new_round_requires_new_reason():
    bad = ScriptEvent(
        event_id="evt_search2",
        description="第二次搜身",
        event_type="new_round",
    )
    errs = validate_script_events([bad])
    assert any("new_reason" in e for e in errs)

    ok = ScriptEvent(
        event_id="evt_search2",
        description="第二次搜身",
        event_type="new_round",
        new_reason="怀疑藏物升级，赵铁头带人二次搜查",
    )
    assert validate_script_events([ok]) == []


def test_synonym_helper_contains_match():
    a = {"description": "老瘸子受刑", "aliases": []}
    b = {"description": "鞭打", "aliases": ["老瘸子受刑之后"]}
    assert _events_look_synonymous(a, b) is True
    assert _events_look_synonymous(a, {"description": "王虎得赏", "aliases": []}) is False


def test_recap_inside_new_round_is_not_synonym():
    """第2章真机：打电话的短句被写进第二格的长说明，不能判成同一事件。"""
    call = {
        "description": "林默给苏晚打电话",
        "aliases": ["苏晚说父亲也这么说"],
        "event_type": "occurrence",
    }
    second = {
        "description": (
            "林默从回避夺取转为追查，进入第二格。"
            "林默给苏晚打电话只是前情，随后灰色大衣女人在小区门口出现，"
            "父亲纸条指向旧货店，林默决定带上怀表出门。"
        ),
        "aliases": [],
        "event_type": "new_round",
    }
    assert _events_look_synonymous(call, second) is False
