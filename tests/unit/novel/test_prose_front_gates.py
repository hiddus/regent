"""前置硬门：人称/操作员/题文/金手指/复读；身份来自活设定。"""

from __future__ import annotations

from regent.novel.domain.prose_front_gates import (
    front_gate_hard_fails,
    front_gate_writer_hint,
    review_issues_may_coerce,
)


STYLE = {
    "viewpoint": "第三人称有限·沈栀（当前意识为穿越者陈默）",
    "narrative_distance": "近距，叙述用『她』而非『我』。",
    "tone": "克制",
    "dialogue_density": "对白适中",
    "avoid": [],
}
CAST = {
    "沈栀": {"identity": {"kind": "host_body"}},
    "陈默": {"identity": {"kind": "traveler"}},
    "周予安": {"identity": {"kind": ""}},
}


def test_writer_hint_covers_front_constraints():
    hint = front_gate_writer_hint(prose_style=STYLE, chapter_no=1, cast=CAST)
    assert "行文契约" in hint
    assert "操作员" in hint
    assert "金手指" in hint
    assert "沈栀" in hint


def test_banned_operator_label():
    text = ("沈栀醒来。" + "她看向窗外。" * 40 + "临时操作员收到系统提示。")
    fails = front_gate_hard_fails(text, prose_style=STYLE, chapter_no=1, cast=CAST)
    assert any("banned_label" in x for x in fails)


def test_power_surface_missing_on_ch1():
    text = ("沈栀睁开眼。" + "她把手机扣在枕边，周予安还在等回电。" * 45)
    packet = {
        "script": {
            "power_mechanism": "浮名系统抽取技能",
            "power_payoff": "羁绊值兑换浓度",
            "power_limits": "七天冷却",
        }
    }
    fails = front_gate_hard_fails(
        text,
        prose_style=STYLE,
        production_packet=packet,
        chapter_no=1,
        cast=CAST,
    )
    assert any("power_surface" in x for x in fails)


def test_title_cast_ghost_name():
    text = ("沈栀睁开眼。" + "她听周予安说话，系统弹出抽取面板。" * 40)
    fails = front_gate_hard_fails(
        text,
        prose_style=STYLE,
        title="林晚的开播前夜",
        cast=CAST,
        production_packet={"script": {"power_mechanism": "系统抽取"}},
        chapter_no=1,
    )
    assert any("title_cast" in x for x in fails)


def test_redundant_identical_paragraph():
    para = "她把手机扣在枕边，继续听周予安把剧本压过来，系统提示下一次抽取还要七天。"
    text = para + "\n\n" + para + "\n\n" + ("她没有回电话，只是盯着冷却倒计时。" * 40)
    fails = front_gate_hard_fails(
        text,
        prose_style=STYLE,
        production_packet={"script": {"power_mechanism": "系统抽取"}},
        chapter_no=1,
        cast=CAST,
        min_chars=400,
    )
    assert any("redundant" in x for x in fails)


def test_good_draft_passes_front_gates():
    text = (
        "沈栀睁开眼。"
        + "她把手机扣在枕边，系统面板弹出抽取与羁绊说明。" * 30
        + "周予安还在等她回话。"
    )
    fails = front_gate_hard_fails(
        text,
        prose_style=STYLE,
        title="第1章 开播前夜",
        cast=CAST,
        production_packet={
            "script": {
                "power_mechanism": "浮名系统抽取",
                "power_payoff": "羁绊浓度",
            }
        },
        chapter_no=1,
    )
    assert fails == []


def test_review_coerce_only_advisory():
    """仅结构化顾问标签可 coerce；自然语言「跳场/因果断裂」一律不可。"""
    assert review_issues_may_coerce(["[editor-soft:redundant_beats] x", "[commons:y]"])
    assert review_issues_may_coerce(["[soft-cont:space] 空间省略，已说明"])
    assert not review_issues_may_coerce(["第二场跳场"])
    assert not review_issues_may_coerce(["第二场因果断裂"])
    assert not review_issues_may_coerce(["[front:banned_label] 操作员"])
    assert not review_issues_may_coerce(["[front:redundant] 同章复读"])
    assert not review_issues_may_coerce(
        ["[editor-soft:x]", "连续性：状态矛盾"]
    )


def test_top_review_fix_notes_caps_and_dedupes():
    from regent.novel.application.chapter_review import _top_review_fix_notes

    issues = [
        "怀表后盖状态冲突：已开却再撬",
        "怀表后盖状态冲突：已开却再撬（重复表述）",
        "齿轮取出后表状态未交代",
        "食指与无名指代价不一致",
        "林见鹿现身缺过渡",
    ]
    top = _top_review_fix_notes(issues, limit=3)
    assert len(top) == 3
    assert "后盖" in top[0]
    assert "齿轮" in top[1]


def test_living_roles_without_sample_hardcode():
    """换一套宿主/穿越者名也应工作，不依赖沈栀/陈默写死。"""
    style = {
        "viewpoint": "第三人称有限·阿梨（意识为穿越者韩舟）",
        "narrative_distance": "叙述用『她』",
        "tone": "冷",
        "dialogue_density": "中",
        "avoid": [],
    }
    cast = {
        "阿梨": {"identity": {"kind": "host_body"}},
        "韩舟": {"identity": {"kind": "traveler"}},
    }
    bad = ("韩舟醒来。" * 20 + "他摸了摸脸，系统还在闪。" * 50)
    fails = front_gate_hard_fails(
        bad, prose_style=style, cast=cast, chapter_no=1, min_chars=400
    )
    assert any("host_name" in x or "pronoun" in x for x in fails)
