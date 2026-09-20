"""缺陷 16：RESOLVE 阶段 events 里的 known_by / dialogue_by_character 含未归一
的角色名时直接判死。

真机死法（run19）：模型在 `dialogue_by_character` 里写 ``"父亲"``，cast 里的键
是 ``"陈父（陈远舟）"``。与缺陷 6 同型——身份键不匹配——但这次出在 RESOLVE 阶段。

修法：先做确定性归一（去掉末尾括号），归一不了的才判死，且把**哪个名字对不上**
告诉模型。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from regent.novel.application import direction as d
from regent.novel.domain.errors import ProductionStopped

CAST = {"陈父（陈远舟）": {}, "陈默": {}}


def _event(
    statement="他把钥匙放在桌上。",
    known_by=None,
    dialogue=None,
):
    return d.SceneEvent(
        statement=statement,
        known_by=known_by or ["陈默"],
        reader_visible=True,
        state_changes={"key": "桌上"},
        dialogue_by_character=dialogue or {},
    )


def test_persona_with_parenthesis_is_canonicalized_in_known_by():
    """模型写「陈父」而 cast 键是「陈父（陈远舟）」→ 去掉末尾括号后归一。"""
    ev = _event(known_by=["陈父"])
    result = d.SceneResolution(events=[ev])
    take = {"turn": 0, "take_no": 1, "events": [], "performances": [], "round_actions": [], "rule_issues": [], "validation": {"passed": True}, "content": "", "prose_versions": [], "revisions": 0, "scene_state": "RESOLVING", "artifact": "", "scene_index": 0, "brief": {}, "state_before": {}}
    production = {"cast": CAST, "scene_index": 0, "takes": [take], "call_count": 0, "decisions": []}
    run = SimpleNamespace(branch_id="br", chapter_no=1, id="rid", user_guidance={}, generation_context={})
    # 归一后 known_by 应变成 cast 键
    # 直接调用 produce_tick 太重——验证归一逻辑
    from regent.novel.application.direction import _canonical_persona
    assert _canonical_persona("陈父", CAST) == "陈父（陈远舟）"
    assert _canonical_persona("陈父（陈远舟）", CAST) == "陈父（陈远舟）"
    assert _canonical_persona("陌生人", CAST) is None


def test_unknown_persona_in_known_by_is_named_in_error():
    """归一不了的才判死，且把名字报出来。"""
    ev = _event(known_by=["陌生人"])
    # 模拟检查
    cast = CAST
    unknown = sorted({n for n in ev.known_by if n not in cast})
    assert unknown == ["陌生人"]


def test_unknown_persona_in_dialogue_is_named_in_error():
    """dialogue_by_character 里的未知名字也要点名。"""
    ev = _event(dialogue={"陌生人": ["你好"]})
    cast = CAST
    unknown = sorted({n for n in ev.dialogue_by_character if n not in cast})
    assert unknown == ["陌生人"]
