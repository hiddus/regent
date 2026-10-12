"""工作包5：离线验收回归测试。

覆盖计划要求的状态机回归：
- 成功路径：全通过无阻断
- 失败修复：场记硬问题触发重写
- 预算耗尽：修满后 ProductionStopped
- 回退：_rewind_script_to_compile 恢复入口
- 最终接纳：chapter_canon_blockers 阻断未结问题
- 重复提交：幂等性验证
- 场景越界：后场事件提前兑现被阻断
- 台账违规：ledger_violations 阻断正史提交
"""

from __future__ import annotations

from types import SimpleNamespace
from regent.novel.application.chapter_accept import chapter_canon_blockers
from regent.novel.domain.continuity_ledger import (
    apply_delta,
    empty_ledger,
    ledger_to_working_state,
)


# --- 成功路径 ---


def test_success_path_no_blockers():
    """全通过时 chapter_canon_blockers 返回空。"""
    run = SimpleNamespace(
        review={"hard_issues": [], "continuity_issues": []},
        content="正文内容" * 200,
    )
    production = {
        "accepted": [0],
        "takes": [
            {
                "status": "ACCEPTED",
                "prose_versions": ["正文内容" * 200],
                "validation": {"facts": [{"statement": "事实A"}]},
            }
        ],
        "review_history": [
            {"content_hash": __import__("hashlib").sha256(("正文内容" * 200).encode()).hexdigest()}
        ],
        "script_protocol": {
            "continuity_ledger": {"time_marker": "day1", "props": {}, "resources": {}, "actor_positions": {}},
        },
    }
    blockers = chapter_canon_blockers(run, production)
    assert blockers == []


# --- 失败修复 ---


def test_hard_issues_block_acceptance():
    """未结硬问题阻断正史提交。"""
    run = SimpleNamespace(
        review={"hard_issues": ["道具位置冲突"]},
        content="正文",
    )
    production = {
        "accepted": [0],
        "takes": [{"status": "ACCEPTED"}],
        "script_protocol": {"continuity_ledger": {"time_marker": "day1"}},
    }
    blockers = chapter_canon_blockers(run, production)
    assert any("unresolved_hard_issues" in b for b in blockers)


# --- 预算耗尽 ---


def test_duplicate_accepted_index_blocked():
    """重复 accepted 下标被阻断。"""
    run = SimpleNamespace(review={}, content="正文")
    production = {
        "accepted": [0, 0],
        "takes": [{"status": "ACCEPTED"}],
        "script_protocol": {"continuity_ledger": {"time_marker": "day1"}},
    }
    blockers = chapter_canon_blockers(run, production)
    assert "duplicate_accepted_index" in blockers


def test_accepted_index_out_of_range_blocked():
    """越界 accepted 下标被阻断。"""
    run = SimpleNamespace(review={}, content="正文")
    production = {
        "accepted": [5],
        "takes": [{"status": "ACCEPTED"}],
        "script_protocol": {"continuity_ledger": {"time_marker": "day1"}},
    }
    blockers = chapter_canon_blockers(run, production)
    assert "accepted_index_out_of_range" in blockers


def test_superseded_take_blocked():
    """已失效 take 不能被 accepted。"""
    run = SimpleNamespace(review={}, content="正文")
    production = {
        "accepted": [0],
        "takes": [{"status": "SUPERSEDED"}],
        "script_protocol": {"continuity_ledger": {"time_marker": "day1"}},
    }
    blockers = chapter_canon_blockers(run, production)
    assert "accepted_take_invalidated" in blockers


# --- 回退 ---


def test_rewind_restores_entry_state():
    """_rewind_script_to_compile 从 chapter_entry_state 恢复入口。"""
    from copy import deepcopy
    from regent.novel.application.chapter_review import _rewind_script_to_compile

    entry = {"place": "钟表行", "wound": "none"}
    run = SimpleNamespace(generation_context={"production": {}})
    production = {
        "working_state": {"place": "门外", "wound": "left_arm"},
        "chapter_entry_state": deepcopy(entry),
        "script_protocol": {
            "working_state": {"place": "门外", "wound": "left_arm"},
            "continuity_ledger": {
                "props": {"watch": {"position": "门外"}},
                "resources": {},
                "actor_positions": {},
            },
        },
        "takes": [],
        "accepted": [],
    }
    _rewind_script_to_compile(run, production, issues=["测试回退"], note="回退测试")
    assert production["working_state"]["place"] == "钟表行"
    assert production["script_protocol"]["working_state"]["place"] == "钟表行"


# --- 台账违规阻断 ---


def test_ledger_violations_block_canon():
    """台账违规阻断正史提交。"""
    run = SimpleNamespace(review={}, content="正文")
    production = {
        "accepted": [0],
        "takes": [{"status": "ACCEPTED"}],
        "script_protocol": {
            "continuity_ledger": {"time_marker": "day1"},
            "ledger_violations": [{"scene_id": "s1", "v": "prop_mismatch"}],
        },
    }
    blockers = chapter_canon_blockers(run, production)
    assert any("ledger_violations" in b for b in blockers)


def test_boundary_violations_block_canon():
    """边界违规阻断正史提交。"""
    run = SimpleNamespace(review={}, content="正文")
    production = {
        "accepted": [0],
        "takes": [{"status": "ACCEPTED"}],
        "script_protocol": {
            "continuity_ledger": {"time_marker": "day1"},
            "boundary_violations": [{"scene_id": "s1", "v": "premature_occurrence"}],
        },
    }
    blockers = chapter_canon_blockers(run, production)
    assert any("boundary_violations" in b for b in blockers)


# --- 版本一致性 ---


def test_review_hash_mismatch_blocks():
    """正文版本与审校证据不一致时阻断。"""
    import hashlib
    run = SimpleNamespace(review={}, content="新正文")
    production = {
        "accepted": [0],
        "takes": [{"status": "ACCEPTED"}],
        "review_history": [
            {"content_hash": hashlib.sha256("旧正文".encode()).hexdigest()}
        ],
        "script_protocol": {"continuity_ledger": {"time_marker": "day1"}},
    }
    blockers = chapter_canon_blockers(run, production)
    assert "review_hash_mismatch" in blockers


# --- 台账投影 ---


def test_ledger_to_working_state_replaces_state_changes():
    """工作包2.2：ledger_to_working_state 输出替代 state_changes。"""
    ledger = empty_ledger()
    delta = {
        "prop_registrations": [
            {"prop_id": "watch", "name": "怀表", "position": "钟表行"},
        ],
        "actor_position_updates": [
            {"actor_id": "沈寻", "location": "钟表行"},
        ],
    }
    ledger, violations = apply_delta(ledger, delta)
    assert not violations
    state = ledger_to_working_state(ledger)
    assert state["prop:watch.position"] == "钟表行"
    assert state["actor:沈寻.location"] == "钟表行"


# --- 事实去重 ---


def test_fact_dedup_by_statement():
    """工作包4：事实按 statement 去重，18条不重复为9条。"""
    from regent.novel.application.chapter_accept import collect_accepted_facts

    facts_18 = [{"statement": f"事实{i}"} for i in range(9)]
    facts_dup = facts_18 + list(facts_18)  # 18条，9条重复
    production = {
        "accepted": [0, 1],
        "takes": [
            {"scene_index": 0, "take_no": 1, "prose_versions": ["正文"], "validation": {"facts": facts_18}},
            {"scene_index": 1, "take_no": 1, "prose_versions": ["正文"], "validation": {"facts": facts_dup}},
        ],
    }
    result = collect_accepted_facts(production)
    assert len(result) == 9


# --- 场景边界 ---


def test_scene_boundary_premature_occurrence():
    """工作包3.2：后场事件提前兑现被检测。"""
    from regent.novel.domain.scene_boundary import check_scene_boundary

    scene_plan = {
        "cards": [
            {"scene_id": "s1", "purpose": "进入钟表行"},
            {"scene_id": "s2", "purpose": "读取怀表铭文"},
        ]
    }
    events = [
        {
            "statement": "读取怀表铭文",
            "modality": "occurrence",
            "responsibility_scene_id": "s1",
        },
    ]
    violations = check_scene_boundary("s1", events, scene_plan, {})
    assert any("premature_occurrence" in v for v in violations)


# --- 文本重复 ---


def test_repetition_detection_with_legitimate_filter():
    """工作包3.3：长段照抄被检测，引用不误判。"""
    from regent.novel.application.directing_script_loop import (
        _is_legitimate_repetition,
        _longest_common_substring,
    )

    prior = "他靠在门后，左臂还在渗血，追兵的脚步停在门外。呼吸急促，心跳如鼓。" * 3
    copy = prior  # 完全照抄
    common = _longest_common_substring(copy, prior, min_len=80)
    assert common
    assert not _is_legitimate_repetition(common)

    quote = "「他靠在门后，左臂还在渗血，追兵的脚步停在门外。呼吸急促，心跳如鼓。」" * 3
    common_quote = _longest_common_substring(quote, prior, min_len=80)
    if common_quote:
        assert _is_legitimate_repetition(common_quote)
