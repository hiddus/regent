"""修复计划缺口：作用域离店、语义关闭、无摘录硬问题、别名。"""

from __future__ import annotations

from regent.novel.domain.continuity_ledger import apply_delta, empty_ledger
from regent.novel.domain.name_aliases import alias_map_from_bible, apply_alias_map
from regent.novel.domain.review_issues import partition_hard_by_grounding
from regent.novel.domain.self_review_check import (
    apply_semantic_resolutions,
    verify_structural_constraints,
)


def test_model_scope_key_cannot_bypass_ability_read():
    ledger = empty_ledger()
    ledger, errs = apply_delta(
        ledger,
        {"prop_registrations": [{"prop_id": "gear", "name": "齿轮"}]},
    )
    assert not errs
    ledger, errs = apply_delta(
        ledger,
        {
            "one_time_events": [
                {
                    "scope_key": "ability_read:gear:other",
                    "event_type": "ability_read",
                    "object_id": "gear",
                }
            ]
        },
    )
    assert not errs
    _, errs = apply_delta(
        ledger,
        {
            "one_time_events": [
                {
                    "scope_key": "a-different-key",
                    "event_type": "ability_read",
                    "object_id": "gear",
                }
            ]
        },
    )
    assert any(
        "ability_read_duplicate" in item or "one_time_event_duplicate" in item for item in errs
    )


def test_return_then_leave_is_a_new_visit():
    ledger = empty_ledger()
    ledger, errs = apply_delta(
        ledger,
        {"actor_position_updates": [{"actor_id": "沈寻", "location": "钟表行"}]},
        scene_id="s1",
    )
    assert not errs
    ledger, errs = apply_delta(
        ledger,
        {"actor_position_updates": [{"actor_id": "沈寻", "location": "街口"}]},
        scene_id="s2",
    )
    assert not errs
    ledger, errs = apply_delta(
        ledger,
        {"actor_position_updates": [{"actor_id": "沈寻", "location": "钟表行"}]},
        scene_id="s3",
    )
    assert not errs
    assert ledger["actor_positions"]["沈寻"]["visit_round"] == 2
    ledger, errs = apply_delta(
        ledger,
        {"actor_position_updates": [{"actor_id": "沈寻", "location": "街口"}]},
        scene_id="s4",
    )
    assert not errs
    assert "leave:沈寻:钟表行:r2" in ledger["one_time_events"]


def test_same_visit_cannot_leave_twice():
    ledger = empty_ledger()
    ledger, _ = apply_delta(
        ledger,
        {"actor_position_updates": [{"actor_id": "沈寻", "location": "钟表行"}]},
        scene_id="s1",
    )
    ledger, errs = apply_delta(
        ledger,
        {"actor_position_updates": [{"actor_id": "沈寻", "location": "街口"}]},
        scene_id="s2",
    )
    assert not errs
    _, errs = apply_delta(
        ledger,
        {
            "one_time_events": [
                {"event_type": "leave", "actor_id": "沈寻", "location": "钟表行"}
            ]
        },
        scene_id="s3",
    )
    assert any("one_time_event_duplicate" in item for item in errs)


def test_enter_new_visit_flag_does_not_bump_round():
    ledger = empty_ledger()
    ledger, _ = apply_delta(
        ledger,
        {"actor_position_updates": [{"actor_id": "沈寻", "location": "钟表行"}]},
    )
    ledger, errs = apply_delta(
        ledger,
        {
            "actor_position_updates": [
                {"actor_id": "沈寻", "location": "钟表行", "enter_new_visit": True}
            ]
        },
    )
    assert not errs
    assert ledger["actor_positions"]["沈寻"]["visit_round"] == 1


def test_unquoted_review_issue_is_evidence_defect():
    grounded, defect = partition_hard_by_grounding(
        ["怀表被读了两次"],
        "他只是把怀表握在手里。",
    )
    assert grounded == []
    assert defect == ["怀表被读了两次"]


def test_grounded_quote_stays_hard():
    grounded, defect = partition_hard_by_grounding(
        ["冲突「他把怀表握在手里」"],
        "他把怀表握在手里，没有打开。",
    )
    assert defect == []
    assert grounded


def test_semantic_issue_is_not_closed_by_being_repeated():
    issues = apply_semantic_resolutions(
        [{"description": "灯绳不是前章事实", "status": "OPEN", "origin": "compile_self_review"}],
        [],
        "他拉灭灯绳，又打开。",
    )
    assert issues[0]["status"] == "OPEN"
    closed = apply_semantic_resolutions(
        issues,
        [{"description": "灯绳不是前章事实", "verdict": "closed", "evidence": "不在正文"}],
        "他拉灭灯绳，又打开。",
    )
    assert closed[0]["status"] == "OPEN"
    closed = apply_semantic_resolutions(
        issues,
        [{"description": "灯绳不是前章事实", "verdict": "closed", "evidence": "他拉灭灯绳"}],
        "他拉灭灯绳，又打开。",
    )
    assert closed[0]["status"] == "RESOLVED"


def test_structural_constraint_survives_until_sentence_is_gone():
    issues = [
        {
            "category": "structural",
            "description": "删掉灯绳",
            "text_must_exclude": "拉灭灯绳又打开",
        }
    ]
    assert verify_structural_constraints("他拉灭灯绳又打开。", issues)
    assert verify_structural_constraints("他没有再提灯。", issues) == []


def test_alias_map_replaces_only_explicit_names():
    mapping = alias_map_from_bible(
        {
            "personas": [
                {"name": "沈寻", "aliases": ["沈知时"]},
                {"name": "纪云岫", "aliases": ["林晚棠"]},
            ]
        }
    )
    text = apply_alias_map("沈知时在时光修理铺遇见林晚棠", mapping)
    assert text == "沈寻在时光修理铺遇见纪云岫"
    assert "沈望" not in mapping


def test_blank_ledger_delta_is_missing():
    from regent.novel.domain.continuity_ledger import ledger_delta_is_blank

    assert ledger_delta_is_blank(None)
    assert ledger_delta_is_blank(
        {
            "prop_updates": [],
            "one_time_events": [],
            "actor_position_updates": [],
            "time_marker": "",
        }
    )
    assert not ledger_delta_is_blank({"time_marker": "夜"})


def test_text_read_type_cannot_bypass_ability_read():
    ledger = empty_ledger()
    ledger, errs = apply_delta(
        ledger,
        {"prop_registrations": [{"prop_id": "gear", "name": "齿轮"}]},
    )
    assert not errs
    _, errs = apply_delta(
        ledger,
        {
            "one_time_events": [
                {"event_type": "ability_read", "object_id": "gear", "read_type": "text"}
            ]
        },
    )
    assert any("read_type_conflict" in item for item in errs)
    ledger, errs = apply_delta(
        ledger,
        {"one_time_events": [{"event_type": "ability_read", "object_id": "gear"}]},
    )
    assert not errs
    _, errs = apply_delta(
        ledger,
        {
            "one_time_events": [
                {
                    "event_type": "ability_read",
                    "object_id": "gear",
                    "read_type": "text",
                }
            ]
        },
    )
    assert any("read_type_conflict" in item for item in errs)


def test_preview_uses_event_description_not_id_spelling():
    from regent.novel.domain.continuity_ledger import preview_plan_conflicts

    ledger = empty_ledger()
    ledger["props"]["gear"] = {
        "prop_id": "gear",
        "name": "齿轮",
        "lifecycle": "intact",
        "read_status": {"ability": {"count": 1, "last_scene": "s0"}},
    }
    cards = [
        {
            "scene_id": "s1",
            "purpose": "进门",
            "event_refs": [{"event_id": "evt_opaque_a", "role": "occurrence"}],
        },
        {
            "scene_id": "s2",
            "purpose": "再看一眼",
            "event_refs": [{"event_id": "evt_opaque_b", "role": "occurrence"}],
        },
    ]
    events = [
        {"event_id": "evt_opaque_a", "description": "沈寻进门"},
        {"event_id": "evt_opaque_b", "description": "再次能力读取齿轮"},
    ]
    errs = preview_plan_conflicts(ledger, cards, events)
    assert any("plan_ability_reread" in item for item in errs)
    dup = preview_plan_conflicts(
        empty_ledger(),
        [
            {"scene_id": "s1", "event_refs": [{"event_id": "evt_same", "role": "occurrence"}]},
            {"scene_id": "s2", "event_refs": [{"event_id": "evt_same", "role": "occurrence"}]},
        ],
    )
    assert any("plan_duplicate_occurrence" in item for item in dup)


def test_boundary_catches_later_phrase_and_ledger_reservation():
    from regent.novel.domain.scene_boundary import audit_cooperation_gaps, check_scene_boundary

    plan = {
        "cards": [
            {"scene_id": "s1", "purpose": "只进门", "event_refs": []},
            {
                "scene_id": "s2",
                "purpose": "后场才读",
                "event_refs": [{"event_id": "evt_late", "role": "occurrence"}],
            },
        ]
    }
    events = [
        {
            "event_id": "evt_other",
            "statement": "他当场能力读取齿轮上的刻痕",
            "modality": "occurrence",
            "responsibility_scene_id": "s1",
        }
    ]
    script_events = [{"event_id": "evt_late", "description": "能力读取齿轮上的刻痕"}]
    violations = check_scene_boundary(
        "s1", events, plan, {}, script_events=script_events
    )
    assert any("phrase:evt_late" in item for item in violations)

    ledger = {"props": {"gear": {"prop_id": "gear", "name": "齿轮"}}}
    delta = {"one_time_events": [{"event_type": "ability_read", "object_id": "gear"}]}
    violations = check_scene_boundary(
        "s1",
        [],
        plan,
        ledger,
        delta,
        script_events=script_events,
    )
    assert any("ledger:evt_late" in item for item in violations)
    gaps = audit_cooperation_gaps(
        "s1",
        events,
        plan,
        {"one_time_events": [], "time_marker": ""},
        require_events=True,
    )
    assert "missing_ledger_delta" in gaps
    assert "ability_read_unrecorded" in gaps


def test_alias_binding_requires_both_sources():
    from regent.novel.domain.name_aliases import (
        accept_alias_bindings,
        outline_mentions_canon,
    )

    bible = {
        "personas": [{"name": "沈寻", "aliases": [], "bio": "沈寻守着拾光钟表行。"}],
        "world_premise": "沈寻在拾光钟表行",
        "background": "钟表行业",
    }
    outline = "沈知时走进时光修理铺"
    assert outline_mentions_canon(bible, [outline]) is False
    assert outline_mentions_canon(bible, ["沈寻回到店里"]) is True
    accepted = accept_alias_bindings(
        bible,
        [
            {
                "alias": "沈知时",
                "canon_name": "沈寻",
                "same_entity": True,
                "evidence": "大纲写沈知时，世界书是沈寻",
            },
            {
                "alias": "时光修理铺",
                "canon_name": "拾光钟表行",
                "same_entity": True,
                "evidence": "时光修理铺就是拾光钟表行",
            },
            {
                "alias": "沈望舒",
                "canon_name": "沈寻",
                "same_entity": True,
                "evidence": "看起来像",
            },
        ],
        outline_text=outline,
        bible_text="沈寻在拾光钟表行",
    )
    assert accepted["personas"][0]["aliases"] == ["沈知时"]
    assert accepted["places"][0]["aliases"] == ["时光修理铺"]
    assert "沈望舒" not in accepted["personas"][0]["aliases"]


def test_later_duplicate_occurrence_becomes_reference():
    from regent.novel.domain.script_events import collapse_duplicate_occurrences

    cards = collapse_duplicate_occurrences(
        [
            {"scene_id": "s1", "event_refs": [{"event_id": "evt_a", "role": "occurrence"}]},
            {"scene_id": "s2", "event_refs": [{"event_id": "evt_a", "role": "occurrence"}]},
        ]
    )
    assert cards[0]["event_refs"][0]["role"] == "occurrence"
    assert cards[1]["event_refs"][0]["role"] == "reference"


def test_replay_consequence_becomes_reference():
    from regent.novel.domain.script_events import demote_replay_consequences

    events = [
        {
            "event_id": "evt_shenchi_chooses_to_continue",
            "event_type": "occurrence",
            "description": "沈迟选择继续",
        }
    ]
    cards = demote_replay_consequences(
        [
            {
                "scene_id": "s1",
                "event_refs": [
                    {"event_id": "evt_shenchi_chooses_to_continue", "role": "occurrence"}
                ],
            },
            {
                "scene_id": "s2",
                "event_refs": [
                    {"event_id": "evt_shenchi_chooses_to_continue", "role": "consequence"}
                ],
            },
        ],
        events,
    )
    assert cards[0]["event_refs"][0]["role"] == "occurrence"
    assert cards[1]["event_refs"][0]["role"] == "reference"


def test_compile_epoch_changes_command_id():
    from types import SimpleNamespace

    from regent.novel.application.directing_calls import _script_scene_command_id

    run = SimpleNamespace(generation_context={"call_key_version": 2}, input_version=1)
    production = {"call_key_version": 2, "decisions": []}
    first = _script_scene_command_id(
        run, production, {"compile_epoch": 0}, "COMPILE_SCENES"
    )
    second = _script_scene_command_id(
        run, production, {"compile_epoch": 1}, "COMPILE_SCENES"
    )
    assert first != second
    assert ":cep1" in second
    assert ":cep" not in first


def test_reference_event_cannot_stay_occurrence():
    from regent.novel.domain.script_events import demote_reference_occurrences

    cards = demote_reference_occurrences(
        [
            {
                "scene_id": "s2",
                "event_refs": [
                    {"event_id": "evt_memory_fragment_father", "role": "occurrence"}
                ],
            }
        ],
        [
            {
                "event_id": "evt_memory_fragment_father",
                "event_type": "reference",
                "description": "父亲留下的记忆碎片",
            }
        ],
    )
    assert cards[0]["event_refs"][0]["role"] == "reference"


def test_structural_quote_must_already_be_in_the_plan():
    from regent.novel.domain.self_review_check import (
        explain_plan_constraint,
        ground_structural_quotes,
    )

    plan = "开门时手还在抖。章末停在门口。"
    grounded = ground_structural_quotes(
        plan,
        [
            {
                "category": "structural",
                "description": "删掉手抖",
                "text_must_exclude": "开门时手还在抖",
                "text_must_include": "一句计划里没有的新句子",
            }
        ],
    )
    assert grounded[0]["category"] == "structural"
    assert grounded[0]["text_must_exclude"] == "开门时手还在抖"
    assert grounded[0]["text_must_include"] == ""
    assert explain_plan_constraint("constraint_present:开门时手还在抖") == (
        "分场原文不得再出现「开门时手还在抖」"
    )


def test_unclassified_opinions_become_semantic():
    from regent.novel.domain.self_review_check import (
        fill_unclassified_as_semantic,
        unclassified_opinions,
    )

    review = {
        "passed": False,
        "issues": ["灯绳不是前章事实"],
        "revision_note": "删掉这句",
        "classified_issues": [],
    }
    assert unclassified_opinions(review) == ["灯绳不是前章事实", "删掉这句"]
    filled = fill_unclassified_as_semantic(review)
    assert filled["classified_issues"][0]["category"] == "semantic"
    assert unclassified_opinions({"classified_issues": [{"description": "已分类"}]}) == []


def test_whole_beat_delete_becomes_semantic_short_phrase_stays():
    from regent.novel.domain.self_review_check import demote_sole_passage_deletes

    beat = "周悬推门进来，手里拿着沈鹤年失踪案的补充材料。他注意到沈迟右手皮肤异样地年轻。"
    demoted = demote_sole_passage_deletes(
        beat,
        [
            {
                "category": "structural",
                "description": "这一场要拆开",
                "text_must_exclude": beat,
                "text_must_include": "",
            }
        ],
        [beat],
    )
    assert demoted[0]["category"] == "semantic"
    assert demoted[0]["text_must_exclude"] == ""

    longer = "他开门时手还在抖，把怀表放回绒布。"
    kept = demote_sole_passage_deletes(
        longer,
        [
            {
                "category": "structural",
                "description": "删掉手抖",
                "text_must_exclude": "开门时手还在抖",
                "text_must_include": "",
            }
        ],
        [longer],
    )
    assert kept[0]["category"] == "structural"
    assert kept[0]["text_must_exclude"] == "开门时手还在抖"


def test_short_exclude_is_stripped_from_revised_plan():
    from regent.novel.domain.self_review_check import strip_excluded_phrases

    issues = [
        {
            "category": "structural",
            "text_must_exclude": "他甚至没注意到是什么时候丢的",
            "text_must_include": "突然想不起父亲教他调游丝时手指的温度",
        }
    ]
    text = "他突然想不起父亲教他调游丝时手指的温度，他甚至没注意到是什么时候丢的。"
    stripped = strip_excluded_phrases(text, issues)
    assert "他甚至没注意到是什么时候丢的" not in stripped
    assert "突然想不起父亲教他调游丝时手指的温度" in stripped


def test_role_field_exclude_is_not_a_sentence_to_delete():
    from regent.novel.domain.self_review_check import (
        demote_role_field_excludes,
        verify_structural_constraints,
    )

    plan = '{"event_id": "evt_echo", "role": "reference"}'
    issues = [
        {
            "category": "structural",
            "description": "evt_echo 应标为 occurrence",
            "text_must_exclude": '"role": "reference"',
            "text_must_include": "",
        },
        {
            "category": "structural",
            "description": "删掉手抖",
            "text_must_exclude": "开门时手还在抖",
            "text_must_include": "",
        },
    ]
    demoted = demote_role_field_excludes(issues)
    assert demoted[0]["category"] == "semantic"
    assert demoted[0]["text_must_exclude"] == ""
    assert demoted[1]["category"] == "structural"
    assert verify_structural_constraints(plan, demoted) == []
