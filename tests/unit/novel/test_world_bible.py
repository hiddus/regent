"""故事圣经：世界 + 戏剧引擎 + 读者契约 + 行文风格。"""

from __future__ import annotations

from regent.novel.domain.world_bible import (
    WorldBible,
    bible_as_context,
    bible_director_block,
    bible_validation_issues,
    fallback_bible_from_direction,
)
from regent.novel.domain.work_conventions import conventions_as_rails


def _sample_bible(**overrides):
    base = {
        "world_premise": "外卖员意识落入一线小花身体，靠演艺系统翻盘。",
        "underlying_rules": [
            "穿越后穿的是原身衣服与神经反馈，默认合身",
            "系统宿主登记分穿越者前史与原身身份两栏",
        ],
        "background": (
            "当代都市文娱圈，恋综录制前夜。"
            "经纪公司与视频平台共同施压，镜头、热搜与舆论同时追人，资源位决定话语权。"
        ),
        "power_system": "鱼塘系统：技能抽奖池；绑定后先抽一轮应对录制危机。",
        "personas": [
            {
                "name": "阿野·意识",
                "identity": "前外卖员",
                "kind": "traveler",
                "bio": "车祸穿越的外卖员，嘴硬心软，目标是先活过录制并翻盘。",
                "voice": "短句，口语",
                "drives": "活命翻盘",
            },
            {
                "name": "林晚",
                "identity": "一线小花",
                "kind": "host_body",
                "bio": "高颜值艺人，有团队与硬脾气撑场，能独自赴约因资源与判断力。",
                "voice": "软而锋利",
                "drives": "维持人设",
            },
        ],
        "dramatic_engine": {
            "protagonist_want": "在恋综开播前稳住女身身份并用系统换到镜头位",
            "opposing_force": "经纪公司冷落、节目组试探、鱼塘名单里的旧关系反噬",
            "conflict_price": "每用一次系统就要暴露更多宿主侧把柄",
            "escalation_logic": "从化妆间自救→名单异常被看见→公开场必须用技能圆谎",
        },
        "reader_contract": {
            "must_deliver": ["性转体感落地", "系统首抽破局", "恋综镜头争夺"],
            "forbidden": ["把鱼塘写成断塘处决项圈", "靠裤管不合身证明换身"],
            "emotional_payoff": "嘴硬翻盘的爽感夹着身份暴露的心虚",
        },
        "prose_style": {
            "viewpoint": "第一人称有限·林晚身体",
            "narrative_distance": "近",
            "tone": "克制锋利，短句多",
            "dialogue_density": "对白略密于叙述",
            "avoid": ["说明书式系统播报", "总结腔复盘"],
        },
        "conventions": [
            {
                "convention_id": "power_first_draw",
                "lens_id": "power_as_toolkit",
                "statement": "本作鱼塘绑定后先给针对录制危机的首抽。",
            }
        ],
        "open_questions": [],
    }
    base.update(overrides)
    return WorldBible.model_validate(base)


def test_world_bible_validates():
    bible = _sample_bible()
    assert not bible_validation_issues(
        bible, direction_keywords=["性转", "系统流", "都市文娱"]
    )


def test_world_bible_dual_gap():
    bible = _sample_bible(
        personas=[
            {
                "name": "林晚",
                "identity": "艺人",
                "kind": "",
                "bio": "只有一个普通人设小传，没有区分穿越者与原身。",
                "voice": "软",
                "drives": "出道",
            },
            {
                "name": "经纪人",
                "identity": "经纪人",
                "kind": "",
                "bio": "负责档期与公关危机处理，不是穿越双身设定中的任一栏。",
                "voice": "急",
                "drives": "保合约",
            },
        ]
    )
    issues = bible_validation_issues(bible, direction_keywords=["性转", "系统流"])
    assert any("穿越者" in x or "原身" in x for x in issues)


def test_placeholder_dramatic_engine_fails_lock():
    bible = _sample_bible(
        dramatic_engine={
            "protagonist_want": "待编剧补全主角欲望",
            "opposing_force": "待编剧补全阻力来源",
            "conflict_price": "待编剧补全推进代价",
            "escalation_logic": "待编剧补全升级逻辑",
        }
    )
    issues = bible_validation_issues(bible, direction_keywords=["都市文娱"])
    assert any("dramatic_engine" in x for x in issues)


def test_bible_as_context_rails():
    ctx = bible_as_context(_sample_bible())
    assert ctx["world_bible"]["world_premise"]
    assert ctx["dramatic_engine"]["protagonist_want"]
    assert ctx["prose_style"]["viewpoint"]
    rails = conventions_as_rails(ctx["work_conventions"])
    assert rails and rails[0]["kind"] == "work_convention"
    block = bible_director_block(ctx["world_bible"])
    assert "戏剧引擎" in block and "行文" in block


def test_fallback_bible_has_new_sections():
    class Chosen:
        title = "开播翻盘"
        protagonist_desire = "稳住身份拿镜头"
        core_conflict = "公司冷落与节目试探"
        genre_promise = "性转恋综爽文"
        pacing = "快"

    draft = fallback_bible_from_direction(Chosen(), raw_intent="外卖员穿越")
    assert draft["dramatic_engine"]["protagonist_want"]
    assert draft["reader_contract"]["must_deliver"]
    assert draft["prose_style"]["viewpoint"]


def test_onboarding_status_enum():
    from regent.novel.application.works import OnboardingStatus

    assert OnboardingStatus.WORLD_REVIEW.value == "WORLD_REVIEW"
    assert OnboardingStatus.DIRECTIONS.value == "DIRECTIONS"


def test_prose_style_hint_and_pov_hard_gate():
    from regent.novel.domain.world_bible import (
        pov_contract_hard_fails,
        prose_style_writer_hint,
        resolve_prose_style,
        wants_she_host_narration,
    )

    style = {
        "viewpoint": "第三人称有限·沈栀（当前意识为穿越者陈默）",
        "narrative_distance": "近距，叙述用『她』而非『我』。",
        "tone": "克制",
        "dialogue_density": "对白适中",
        "avoid": [],
    }
    cast = {
        "沈栀": {"kind": "host_body", "identity": {"kind": "host_body"}},
        "陈默": {"kind": "traveler", "identity": {"kind": "traveler"}},
    }
    assert wants_she_host_narration(style)
    hint = prose_style_writer_hint(style)
    assert "行文契约" in hint and "沈栀" in hint
    assert resolve_prose_style({"prose_style": style})["viewpoint"].startswith("第三人称")

    bad = ("陈默醒来。" * 12 + "他摸了摸脸，系统还在闪。" * 55)
    assert len(bad) >= 600
    fails = pov_contract_hard_fails(bad, style, cast=cast)
    assert any("pov:pronoun" in x for x in fails)
    assert any("pov:host_name" in x for x in fails)

    good = ("沈栀睁开眼。" + "她把手机扣在枕边，继续听周予安说话。" * 45)
    assert len(good) >= 600
    assert pov_contract_hard_fails(good, style, cast=cast) == []


def test_script_brief_uses_prose_style_not_cast0():
    from regent.novel.application import direction as d

    production = {"cast": {"陈默": {}, "沈栀": {}, "周予安": {}}}
    packet = {
        "script": {
            "end_change": "代价落地",
            "start_state": "开播前夜",
            "opposition": "经纪人施压",
            "protagonist_want": "稳住",
            "reading_question": "她怎么既用他又不被咬",
        }
    }
    brief = d._script_brief_from_packet(
        production,
        packet,
        prose_style={
            "viewpoint": "第三人称有限·沈栀",
            "narrative_distance": "近，用『她』",
            "tone": "冷",
        },
    )
    assert "沈栀" in brief["narrative"]["viewpoint"]
    assert "陈默" not in brief["narrative"]["viewpoint"]
    assert "她" in brief["narrative"]["distance"]
    sys_prompt = d._script_writer_system(
        {"viewpoint": "第三人称·沈栀", "narrative_distance": "用『她』"}
    )
    assert "行文契约" in sys_prompt
    assert "沈栀" in sys_prompt