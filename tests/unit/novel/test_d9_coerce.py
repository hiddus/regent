"""缺陷 9：命令被 Runtime 拒绝即判死整章。

真机死法：节拍用尽时导演仍选 CONTINUE。这是**可判定**的非法，且 RENDER 是节拍
用尽后唯一还能执行的路径。

「存在硬失败时导演仍选 ACCEPT」**故意不收束**：既有测试
``test_director_cannot_override_failed_independent_validation`` 编码了「独立核验
没过的东西导演不能绕过去」这条安全属性。把它改成 REWRITE 是产品决定，不是实现
细节，必须由人拍板。这里用 ``test_accept_is_never_coerced`` 把它钉住，防止后来者
顺手"修好"它。
"""

from __future__ import annotations

from regent.novel.application import direction as d


def _take(action: str = "CONTINUE") -> d.TakeDirection:
    return d.TakeDirection(
        action=action, observation="表演到位", evidence=["后盖纹丝未动"],
        instruction="再收紧一点",
    )


def _prose(action: str = "ACCEPT") -> d.ProseDirection:
    return d.ProseDirection(
        action=action, observation="节奏偏平", evidence=["后盖纹丝未动"],
        instruction="把犹豫写进动作",
    )


def test_continue_is_coerced_to_render_when_beats_are_exhausted():
    result = _take("CONTINUE")
    notes = d._coerce_illegal_action("WATCH_TAKE", result, {"turn": d.MAX_TURNS - 1})
    assert result.action == "RENDER"
    assert notes and "RENDER" in notes[0]


def test_continue_is_left_alone_while_beats_remain():
    result = _take("CONTINUE")
    assert d._coerce_illegal_action("WATCH_TAKE", result, {"turn": 0}) == []
    assert result.action == "CONTINUE", "还有节拍时不该替导演收束"


def test_accept_is_never_coerced_even_on_hard_failure():
    """硬失败时 ACCEPT 不许被改成 REWRITE：那等于替导演绕过独立核验。"""
    result = _prose("ACCEPT")
    notes = d._coerce_illegal_action("WATCH_PROSE", result, {"validation": {"passed": False}})
    assert notes == [] and result.action == "ACCEPT"


def test_accept_is_left_alone_when_validation_passed():
    result = _prose("ACCEPT")
    assert d._coerce_illegal_action("WATCH_PROSE", result, {"validation": {"passed": True}}) == []
    assert result.action == "ACCEPT"


def test_other_phases_are_untouched():
    """收束只在两个已确认的死法上生效，不得扩大成普遍的命令改写。"""
    result = _take("RETAKE")
    assert d._coerce_illegal_action("WATCH_TAKE", result, {"turn": d.MAX_TURNS - 1}) == []
    assert result.action == "RETAKE"
    assert d._coerce_illegal_action("VALIDATE", _take("CONTINUE"), {"turn": 99}) == []
