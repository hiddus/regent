"""工作包补充：自审分类、条件触发、字数预算。"""

from __future__ import annotations

from regent.novel.application.directing_contracts import RoleSelfReview, SelfReviewIssue
from regent.novel.application.directing_script_loop import _trim_context_for_writer
from regent.novel.domain.self_review_check import (
    check_structural_constraints,
    should_trigger_self_review,
)


def test_self_review_issue_structural():
    issue = SelfReviewIssue(
        category="structural",
        description="入场摘要已完成的动作被重演",
        text_constraint="删掉重复开门",
        programmatic_check="repeated_action:开门",
    )
    assert issue.category == "structural"
    assert issue.description == "入场摘要已完成的动作被重演"


def test_self_review_issue_semantic():
    issue = SelfReviewIssue(
        category="semantic",
        description="这句虚构了前章事实",
        involves_prior_chapter=True,
    )
    assert issue.category == "semantic"
    assert issue.involves_prior_chapter is True


def test_role_self_review_with_classified_issues():
    review = RoleSelfReview(
        passed=False,
        issues=["旧格式问题"],
        classified_issues=[
            SelfReviewIssue(category="structural", description="结构问题"),
            SelfReviewIssue(category="semantic", description="语义问题"),
        ],
        revision_note="需要修改",
    )
    assert len(review.classified_issues) == 2
    assert review.classified_issues[0].category == "structural"
    assert review.classified_issues[1].category == "semantic"


def test_check_structural_constraints_detects_repeated_action():
    violations = check_structural_constraints(
        scene_text="他打开了门，走了进去",
        scene_card={},
        entry_summary="已完成：打开了门",
        working_state={},
        ledger={},
    )
    assert any("repeated_action" in v for v in violations)


def test_check_structural_constraints_detects_state_mismatch():
    violations = check_structural_constraints(
        scene_text="他坐在椅子上",
        scene_card={},
        entry_summary="",
        working_state={"伤势": "左臂渗血"},
        ledger={},
    )
    assert any("state_mismatch" in v for v in violations)


def test_should_trigger_self_review_with_repetition():
    assert should_trigger_self_review(
        scene_text="测试文本",
        scene_card={},
        entry_summary="",
        working_state={},
        ledger={},
        repetition_warnings=[{"scene_id": "s1", "warnings": ["test"]}],
    ) is True


def test_should_trigger_self_review_without_violations():
    assert should_trigger_self_review(
        scene_text="测试文本",
        scene_card={},
        entry_summary="",
        working_state={},
        ledger={},
    ) is False


def test_trim_context_enforces_budget():
    ctx = {
        "story_ledger_block": "x" * 3000,
        "canon": "y" * 3000,
        "verified_facts": "z" * 3000,
        "other": "short",
    }
    trimmed = _trim_context_for_writer(ctx)
    assert len(str(trimmed["story_ledger_block"])) <= 2003
    assert len(str(trimmed["canon"])) <= 2003
    assert len(str(trimmed["verified_facts"])) <= 2003
