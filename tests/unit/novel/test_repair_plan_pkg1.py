"""工作包1.1/1.2：审校议题严重性分级、证据接地与正史硬门。"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

from regent.novel.application.chapter_accept import (
    chapter_canon_blockers,
    finalize_chapter_acceptance,
)
from regent.novel.domain.review_issues import (
    is_hard_issue,
    partition_hard_by_grounding,
    partition_issues,
    quote_in_content,
)

CONTENT = "他把怀表放回口袋，滴答声骤然停了，门外追兵的脚步也停下。"


# --- 严重性分级（工作包1.1：严重性与递延标签分离）---


def test_soft_prefixes_are_not_hard():
    for soft in (
        "[soft-cont:space] 空间省略，已说明",
        "[editor-soft:x] 文风建议",
        "[soft-outline]目标节点名",
        "[commons:y] 常识提示",
        "[front:power_surface] 缺金手指外显",
    ):
        assert is_hard_issue(soft) is False, soft


def test_real_conflicts_are_hard():
    for hard in (
        "[front:redundant] 同章复读",
        "[front:banned_label] 操作员",
        "[pov:x] 视角越界",
        "[prior_canon] 与前章冲突",
        "[editor:x] 硬编辑问题",
        "第二场跳场，缺少因果衔接",
    ):
        assert is_hard_issue(hard) is True, hard


def test_unknown_prefix_is_hard():
    """无已知软前缀的议题一律按硬处理。"""
    assert is_hard_issue("[gate_debt:cont]滴答声矛盾") is True
    assert is_hard_issue("[gate_debt:prose_front] redundant 复读") is True
    assert is_hard_issue("[gate_debt:prose_front] power_surface 外显") is True


def test_partition_splits_and_dedups():
    hard, soft = partition_issues(
        ["第二场跳场", "[soft-cont:space] 省略", "第二场跳场", "[front:redundant] 复读"]
    )
    assert hard == ["第二场跳场", "[front:redundant] 复读"]
    assert soft == ["[soft-cont:space] 省略"]


# --- 证据接地（工作包1.1：硬问题须可程序核对）---


def test_quote_matching_content_is_grounded():
    issue = "[front:redundant] 复读；摘录「滴答声骤然停了」"
    grounded, defect = partition_hard_by_grounding([issue], CONTENT)
    assert grounded == [issue]
    assert defect == []


def test_quote_missing_from_content_is_evidence_defect():
    """引用了对不上正文的摘录 → 证据缺陷，须复核，不直接触发重写。"""
    issue = "持物冲突；摘录「他手里握着手枪」"
    grounded, defect = partition_hard_by_grounding([issue], CONTENT)
    assert grounded == []
    assert defect == [issue]


def test_unquoted_deterministic_issue_is_not_a_defect():
    """前置硬门/字数/自然语言冲突没有摘录，本就无需接地，不算证据缺陷。"""
    for issue in ("[front:banned_label] 操作员", "整章正文不足800字符", "第二场跳场"):
        grounded, defect = partition_hard_by_grounding([issue], CONTENT)
        assert grounded == [issue]
        assert defect == []


def test_grounding_normalizes_whitespace_and_punctuation_but_keeps_facts():
    assert quote_in_content("滴答声 骤然停了", CONTENT) is True
    assert quote_in_content("滴答声骤然停了。", CONTENT) is True
    # 数字/否定差异不得被规范化抹去
    assert quote_in_content("滴答声骤然响了", CONTENT) is False
    assert quote_in_content("追兵没有停下", CONTENT) is False


# --- 正史硬门（工作包1.2）---


def _run(review: dict, content: str = CONTENT) -> SimpleNamespace:
    return SimpleNamespace(
        chapter_no=1,
        content=content,
        review=review,
        generation_context={},
    )


def test_canon_gate_blocks_unresolved_hard_issues():
    run = _run({"passed": True, "hard_issues": ["第二场跳场"]})
    blockers = chapter_canon_blockers(run, {"review_history": [], "accepted": [], "takes": []})
    assert "unresolved_hard_issues:1" in blockers


def test_canon_gate_blocks_review_hash_mismatch():
    run = _run({"passed": True, "hard_issues": []})
    production = {
        "review_history": [{"content_hash": "0" * 64}],
        "accepted": [],
        "takes": [],
    }
    assert "review_hash_mismatch" in chapter_canon_blockers(run, production)


def test_canon_gate_blocks_invalidated_accepted_take():
    run = _run({"passed": True, "hard_issues": []})
    digest = hashlib.sha256(CONTENT.encode()).hexdigest()
    production = {
        "review_history": [{"content_hash": digest}],
        "accepted": [0, 0],
        "takes": [{"status": "SUPERSEDED"}],
    }
    blockers = chapter_canon_blockers(run, production)
    assert "duplicate_accepted_index" in blockers
    assert "accepted_take_invalidated" in blockers


def test_canon_gate_clean_pass_has_no_blockers():
    run = _run({"passed": True, "hard_issues": []})
    digest = hashlib.sha256(CONTENT.encode()).hexdigest()
    production = {
        "review_history": [{"content_hash": digest}],
        "accepted": [0],
        "takes": [{"status": "ACCEPTED"}],
    }
    assert chapter_canon_blockers(run, production) == []


def test_finalize_skips_ledger_when_blocked(monkeypatch):
    """review.passed 不能单独替代硬门：有未结硬问题时不写正式记忆/账本。"""
    committed = {"called": False}

    def fake_commit(run, *, production, facts):
        committed["called"] = True

    monkeypatch.setattr(
        "regent.novel.application.chapter_accept.commit_run_story_ledger", fake_commit
    )
    digest = hashlib.sha256(CONTENT.encode()).hexdigest()
    run = _run({"passed": True, "hard_issues": ["第二场跳场"]})
    production = {
        "working_state": {},
        "review_history": [{"content_hash": digest}],
        "accepted": [],
        "takes": [],
        "script_protocol": {},
    }
    finalize_chapter_acceptance(run, production, review_passed=True)
    assert committed["called"] is False
    assert run.generation_context["canon_blockers"] == ["unresolved_hard_issues:1"]


def test_finalize_commits_ledger_when_clean(monkeypatch):
    committed = {"called": False}

    def fake_commit(run, *, production, facts):
        committed["called"] = True

    monkeypatch.setattr(
        "regent.novel.application.chapter_accept.commit_run_story_ledger", fake_commit
    )
    digest = hashlib.sha256(CONTENT.encode()).hexdigest()
    run = _run({"passed": True, "hard_issues": []})
    production = {
        "working_state": {},
        "review_history": [{"content_hash": digest}],
        "accepted": [],
        "takes": [],
        "script_protocol": {},
    }
    finalize_chapter_acceptance(run, production, review_passed=True)
    assert committed["called"] is True
    assert run.generation_context["canon_blockers"] == []
