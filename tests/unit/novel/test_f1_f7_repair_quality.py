"""F1/F2/F3/F7 反例：ChatGPT 858a 代码提示词评估必修项。"""

from __future__ import annotations

from types import SimpleNamespace

from regent.novel.application.directing_script_loop import _audit_cont_softable
from regent.novel.domain.prose_front_gates import review_issues_may_coerce
from regent.novel.domain.repair_locate import (
    LocatedIssue,
    RepairTarget,
    apply_repair_target_to_script_state,
    format_revision_instruction,
    locate_fail_messages,
    repair_target_from_scene_index,
    select_repair_target,
)
from regent.novel.domain.scene_card import (
    SceneCard,
    StructuredBeat,
    is_empty_state_discovery_false_positive,
)

PARA = "雨水沿窗棂流下，两个人仍旧没有开口，钥匙静静躺在桌面正中。"


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


def _gate(*, hard_fails: list[str], continuity_issues: list[str] | None = None):
    return SimpleNamespace(
        critical_missed=[],
        hard_fails=hard_fails,
        continuity_issues=list(continuity_issues or []),
        continuity_block=True,
    )


# --- F1 ---


def test_f1_nonempty_state_knowledge_conflict_not_softable():
    """非空已知状态下「入场状态…不知道」不得 soft。"""
    msg = "入场状态明确他不知道密码；正文未获知密码却直接输入正确密码"
    assert (
        is_empty_state_discovery_false_positive(
            msg, working_state={"password_known": False, "location": "门廊"}
        )
        is False
    )
    gate = _gate(hard_fails=[msg])
    assert (
        _audit_cont_softable(
            gate, working_state={"password_known": False, "location": "门廊"}
        )
        is False
    )


def test_f1_empty_state_discovery_still_softable():
    """真正空入场 + 发现误报句式 → 可 soft。"""
    msg = "入场状态中沈默不知道黄铜小钥匙的存在，但正文中他从座钟底座摸出钥匙"
    assert is_empty_state_discovery_false_positive(msg, working_state={}) is True
    gate = _gate(hard_fails=[msg])
    assert _audit_cont_softable(gate, working_state={}) is True


def test_f1_missing_state_keeps_hard():
    """未传入 working_state → 不得仅凭关键词判误报。"""
    msg = "入场状态中他不知道钥匙"
    assert is_empty_state_discovery_false_positive(msg) is False
    assert is_empty_state_discovery_false_positive(msg, working_state=None) is False


def test_f1_review_coerce_rejects_knowledge_conflict_wording():
    assert (
        review_issues_may_coerce(
            ["入场状态明确他不知道密码，正文却输入正确密码"]
        )
        is False
    )


# --- F2 ---


def test_f2_invalid_scene_id_not_default_first():
    cards = [_card("s0"), _card("s1"), _card("s2")]
    texts = ["甲。" * 40, "乙。" * 40, "丙。" * 40]
    bogus = LocatedIssue(
        issue_id="x#1",
        code="continuity",
        scene_ids=("no_such_scene",),
        evidence_quotes=("钥匙在小王手中",),
        expected_action="rewrite_scene",
        message="钥匙应在小李手中",
    )
    target = select_repair_target(
        [bogus], cards=cards, scene_texts=texts, instruction_prefix="改本场："
    )
    assert target.locatable is False
    assert target.scene_index == -1


def test_f2_repair_target_from_none_or_oob_not_locatable():
    cards = [_card("s0"), _card("s1")]
    t0 = repair_target_from_scene_index(
        scene_index=None, cards=cards, instruction_prefix="审校："
    )
    assert t0.locatable is False
    t1 = repair_target_from_scene_index(
        scene_index=5, cards=cards, instruction_prefix="审校："
    )
    assert t1.locatable is False
    t2 = repair_target_from_scene_index(
        scene_index=-1, cards=cards, instruction_prefix="审校："
    )
    assert t2.locatable is False


def test_f2_mixed_valid_and_invalid_keeps_valid_earliest():
    cards = [_card("s0"), _card("s1"), _card("s2")]
    texts = ["甲。" * 40, "乙。" * 40, "丙。" * 40]
    issues = [
        LocatedIssue(
            issue_id="a",
            code="x",
            scene_ids=("ghost",),
            expected_action="rewrite_scene",
            message="无效",
        ),
        LocatedIssue(
            issue_id="b",
            code="y",
            scene_ids=("s2",),
            expected_action="rewrite_scene",
            message="真问题",
        ),
    ]
    target = select_repair_target(
        issues, cards=cards, scene_texts=texts, instruction_prefix="改："
    )
    assert target.locatable is True
    assert target.scene_index == 2
    assert target.scene_id == "s2"
    assert len(target.issues) == 2


# --- F3 ---


def test_f3_short_scene_does_not_shift_redundant_map():
    """s0 仅短句，s1/s2 同长段重复 → 定位 s1/s2，不得落到 s0。"""
    long = PARA + "更多叙述让它超过三十二字。"
    texts = [
        "短。",
        long + "\n\n" + "中场独有。" * 10,
        long + "\n\n" + "末场独有。" * 10,
    ]
    cards = [_card("s0"), _card("s1"), _card("s2")]
    msg = "[front:redundant] 同章出现完全相同段落重复；摘录「" + PARA + "」"
    located = locate_fail_messages([msg], scene_texts=texts, cards=cards)
    target = select_repair_target(
        located, cards=cards, scene_texts=texts, instruction_prefix="改本场："
    )
    assert target.locatable
    assert target.scene_index == 1
    assert "s0" not in target.scene_ids
    assert "s1" in target.scene_ids


# --- F7 ---


def test_f7_fact_issue_not_wrapped_as_dedup():
    issue = LocatedIssue(
        issue_id="fact#1",
        code="continuity",
        scene_ids=("s0",),
        evidence_quotes=("钥匙在小王手中",),
        expected_action="rewrite_scene",
        message="钥匙应在小李手中",
        verify_rule="纠正后不得再成立错误断言",
    )
    text = format_revision_instruction(
        prefix="章节核验未过，请改本场", issues=[issue], scene_ids=("s0",)
    )
    assert "不得原样保留两处" not in text
    assert "问题证据" in text
    assert "钥匙在小王手中" in text
    assert "rewrite_scene" in text

    sp: dict = {
        "scene_texts": ["正文。" * 40],
        "scene_state_trail": [{}],
        "working_state": {},
    }
    apply_repair_target_to_script_state(
        sp,
        RepairTarget(
            scene_index=0,
            scene_id="s0",
            scene_ids=("s0",),
            issues=(issue,),
            instruction=text,
            locatable=True,
        ),
    )
    ticket = sp["pending_repair_ticket"]
    assert ticket["must_remove_quotes"] == []
    assert "钥匙在小王手中" in ticket["evidence_quotes"]
    # 「钥匙应在小李」是目标正确状态，不得进 forbidden_claims
    assert not any("钥匙应在小李" in c for c in ticket.get("forbidden_claims") or [])
    assert any("钥匙应在小李" in c for c in ticket.get("required_facts") or [])


def test_f7_redundant_still_uses_dedup_template():
    issue = LocatedIssue(
        issue_id="front:redundant#1",
        code="front:redundant",
        scene_ids=("s0",),
        evidence_quotes=(PARA,),
        expected_action="remove_dup",
        message="[front:redundant] 复读",
    )
    text = format_revision_instruction(
        prefix="改本场", issues=[issue], scene_ids=("s0",)
    )
    assert "不得原样保留两处" in text
    assert "问题证据" not in text
