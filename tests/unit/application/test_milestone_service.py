"""Unit tests for GAC-E goal-driven milestone decomposition."""

from __future__ import annotations

from regent.application.milestone_service import (
    GOAL_SCALE_LARGE,
    GOAL_SCALE_SMALL,
    acceptance_for_current_milestone,
    classify_goal_scale,
    derive_goal_slices,
    is_final_milestone,
    plan_from_metadata,
    propose_milestones,
)


def test_milestones_follow_explicit_goal_list_not_fixed_three() -> None:
    milestones = propose_milestones(
        original_input="ignored when list present",
        success_criteria={
            "milestones": [
                "采集授权源",
                "聚合去重排序",
                "可分享阅读页",
                "运营后台指标",
            ]
        },
        first_deliverable=None,
        goal_scale=GOAL_SCALE_LARGE,
    )
    assert len(milestones) == 4
    assert milestones[0].title.startswith("采集")
    assert milestones[-1].is_final is True
    assert milestones[0].is_final is False
    # Must not be the old fixed template keys.
    assert {m.key for m in milestones} != {"m1-surface", "m2-content", "m3-criteria"}


def test_milestones_follow_goal_clauses() -> None:
    goal = (
        "先做 RSS 抓取与清洗；然后做头条聚合页；最后加上用户订阅与推送。"
    )
    milestones = propose_milestones(
        original_input=goal,
        success_criteria={},
        first_deliverable=None,
        goal_scale=GOAL_SCALE_LARGE,
    )
    assert len(milestones) == 3
    assert "RSS" in milestones[0].title or "抓取" in milestones[0].title
    assert milestones[-1].is_final is True


def test_two_feature_criteria_become_two_milestones() -> None:
    milestones = propose_milestones(
        original_input="新闻产品",
        success_criteria={
            "first_deliverable": "digest",
            "min_list_items": 5,
            "min_outbound_links": 3,
        },
        first_deliverable="digest",
        goal_scale=GOAL_SCALE_LARGE,
    )
    assert len(milestones) == 2
    assert milestones[0].acceptance.get("min_list_items") == 5
    assert milestones[1].acceptance.get("min_outbound_links") == 3


def test_large_with_single_slice_expands_to_goal_derived_pair() -> None:
    milestones = propose_milestones(
        original_input="做一个可用的 AI 新闻摘要页",
        success_criteria={"first_deliverable": "AI 新闻摘要页"},
        first_deliverable="AI 新闻摘要页",
        goal_scale=GOAL_SCALE_LARGE,
        metadata={"force_milestones": True},
    )
    assert len(milestones) == 2
    assert "first_deliverable" in milestones[0].acceptance
    assert milestones[1].is_final is True
    assert milestones[0].key != "m1-surface"


def test_small_goal_single_final_milestone() -> None:
    scale = classify_goal_scale("做一个简单新闻页", {"usable": True})
    assert scale == GOAL_SCALE_SMALL
    milestones = propose_milestones(
        original_input="做一个简单新闻页",
        success_criteria={"usable": True},
        first_deliverable="简单新闻页",
        goal_scale=scale,
    )
    assert len(milestones) == 1
    assert milestones[0].is_final is True


def test_derive_slices_from_inference_deliverables() -> None:
    slices = derive_goal_slices(
        original_input="x",
        success_criteria={},
        first_deliverable=None,
        system_inferences={"deliverables": ["登录", "内容流", "设置"]},
    )
    assert len(slices) == 3
    assert slices[1].deliverable == "内容流"


def test_non_final_forbids_full_goal_claim() -> None:
    plan_meta = {
        "goal_scale": GOAL_SCALE_LARGE,
        "current_milestone_ordinal": 1,
        "milestones": [
            {
                "ordinal": 1,
                "key": "m1-a",
                "title": "A",
                "acceptance": {"first_deliverable": "A"},
                "is_final": False,
            },
            {
                "ordinal": 2,
                "key": "m2-b",
                "title": "B",
                "acceptance": {"first_deliverable": "B"},
                "is_final": True,
            },
        ],
    }
    assert is_final_milestone(plan_from_metadata(plan_meta)) is False
    acceptance = acceptance_for_current_milestone(plan_meta)
    assert acceptance["forbid_full_goal_claim"] is True
    assert acceptance["milestone_count"] == 2
