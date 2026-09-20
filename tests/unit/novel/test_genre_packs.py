"""原则透镜 + 本作公约 + 机械器物规则。"""

from __future__ import annotations

from regent.novel.application import commons_audit as ca
from regent.novel.domain import genre_packs as gp
from regent.novel.domain import principle_lenses as pl
from regent.novel.domain.work_conventions import WorkConventionBundle, conventions_as_rails


def test_resolve_packs_urban_only_mechanical():
    packs = gp.resolve_packs(["都市文娱", "系统流", "爽文"])
    ids = [p.pack_id for p in packs]
    assert ids == ["urban_entertainment"]
    rails = gp.flatten_rails(packs)
    rule_ids = {r["rule_id"] for r in rails}
    assert "im_contact_density" in rule_ids
    assert "clothes_fit_host" not in rule_ids
    assert "fishpond_is_pool" not in rule_ids


def test_lenses_activate_for_system_body_swap():
    lenses = pl.resolve_lenses(["都市文娱", "系统流", "性转"])
    ids = {x.lens_id for x in lenses}
    assert "host_world_continuity" in ids
    assert "sensorimotor_primacy" in ids
    assert "dual_identity_ledger" in ids
    assert "power_as_toolkit" in ids


def test_work_conventions_become_rails_without_check():
    bundle = WorkConventionBundle(
        conventions=[
            {
                "convention_id": "power_first_option",
                "lens_id": "power_as_toolkit",
                "statement": "本作『星轨』绑定后先给一次针对录制危机的抽取。",
                "check_hint": "绑定后是否出现抽取/可选项",
            }
        ],
        rationale="覆盖 power_as_toolkit",
    )
    rails = conventions_as_rails(bundle)
    assert len(rails) == 1
    assert rails[0]["kind"] == "work_convention"
    assert rails[0]["check"] is None
    assert "星轨" in rails[0]["statement"]


def test_assemble_merges_mechanical_and_work():
    rails, lenses = gp.assemble_context_rails(
        ["都市文娱", "系统流"],
        work_conventions={
            "conventions": [
                {
                    "convention_id": "c1",
                    "lens_id": "power_as_toolkit",
                    "statement": "本作资源池叫星轨，是技能抽取库。",
                }
            ]
        },
    )
    kinds = {r["kind"] for r in rails}
    assert "world_default" in kinds
    assert "work_convention" in kinds
    assert any(x["lens_id"] == "power_as_toolkit" for x in lenses)


def test_im_five_contacts_as_whole_fails():
    rails = gp.commons_rails_for_keywords(["都市文娱"])
    text = (
        "我打开微信。置顶聊天框一共四个，前三个的备注分别是“阿深”“默默”“小狼狗”，"
        "第四个是苏瑶。这就是全部了。"
    )
    issues = ca.deterministic_commons_issues(text, commons_rails=rails, chapter_no=1)
    assert any("im_contact_density" in x for x in issues)


def test_im_with_remainder_passes():
    rails = gp.commons_rails_for_keywords(["都市文娱"])
    text = (
        "我打开微信。置顶里有苏瑶，下面还在刷，一长串备注往下翻，"
        "划了很久才停在刚才那条未读。"
    )
    issues = ca.deterministic_commons_issues(text, commons_rails=rails, chapter_no=1)
    assert not any("im_contact_density" in x for x in issues)


def test_story_specific_pants_not_deterministic():
    """裤管类问题不得靠写死 regex；应走本作公约 + LLM。"""
    rails, _ = gp.assemble_context_rails(["都市文娱", "性转"])
    text = "他低头一看，裤管长了一截，这衣服根本不合身，才发现自己换了身体。"
    issues = ca.deterministic_commons_issues(text, commons_rails=rails, chapter_no=1)
    assert not any("clothes_fit_host" in x for x in issues)


def test_llm_audit_runs_when_work_conventions_present():
    rails = conventions_as_rails(
        {
            "conventions": [
                {
                    "convention_id": "sense_first",
                    "lens_id": "sensorimotor_primacy",
                    "statement": "换身先体感冲突，看镜只确认。",
                }
            ]
        }
    )
    assert ca.should_run_llm_commons_audit("任意正文", commons_rails=rails)


def test_dual_dossier_gaps_generic():
    from regent.novel.domain import dossiers as dd

    assert dd.needs_dual_dossiers(["性转", "系统流"])
    gaps = dd.dual_dossier_gaps(
        [{"name": "主角", "identity": {"role": "艺人", "kind": "", "bio": ""}}]
    )
    assert any("穿越者" in g for g in gaps)
    assert any("原身" in g for g in gaps)


def test_no_rails_no_false_positive():
    text = "置顶聊天框一共四个，备注是“阿深”“默默”。"
    issues = ca.deterministic_commons_issues(text, commons_rails=[], chapter_no=1)
    assert issues == []


def test_merge_commons_preserves_order():
    base = ["事实冲突"]
    merged = ca.merge_commons_into_issues(
        base, ["[commons:im_contact_density] x", "事实冲突"]
    )
    assert merged == ["事实冲突", "[commons:im_contact_density] x"]


def test_plan_rails_does_not_require_fishpond_keywords():
    from regent.novel.application import direction as d

    narr = d.NarrativeSpec(
        viewpoint="主角",
        distance="近",
        style="短",
        reader_effect="爽",
        disclosure_rule="不揭穿越大幕",
    )
    scene = d.SceneBrief(
        purpose="绑定外挂并亮明身份",
        setting="化妆间",
        conflict="是否接受系统",
        exit_condition="接受绑定并首次抽取",
        actors=[
            d.ActorDirection(
                persona="林晚", role="主角身份", objective="活命", instruction="试探系统"
            )
        ],
        narrative=narr,
    )
    direction = d.ChapterDirection(
        title="开播前夜",
        reader_intent="看外挂落地",
        ending_reason="接受绑定",
        scenes=[scene],
        new_personas=[],
    )
    issues = d._plan_story_rails_issues(
        direction,
        chapter_no=1,
        context={
            "direction_keywords": ["系统流", "都市文娱"],
            "commons_rails": gp.commons_rails_for_keywords(["系统流", "都市文娱"]),
            "principle_lenses": pl.lenses_payload(pl.resolve_lenses(["系统流"])),
            "work_conventions": {
                "conventions": [
                    {
                        "convention_id": "c1",
                        "lens_id": "power_as_toolkit",
                        "statement": "本作外挂绑定后先给抽取。",
                    }
                ]
            },
            "locked_direction": {"protagonist_desire": "用系统翻盘"},
            "cast": {
                "林晚": {
                    "identity": {
                        "kind": "host_body",
                        "bio": "一线小花，能独自赴约因团队与硬脾气撑场。",
                    }
                },
                "意识": {
                    "identity": {
                        "kind": "traveler",
                        "bio": "前外卖员车祸穿越，嘴硬心软，目标先活过录制。",
                    }
                },
            },
        },
    )
    assert not any("chosen_one_scale" in x for x in issues)
    assert not any("fishpond" in x for x in issues)
