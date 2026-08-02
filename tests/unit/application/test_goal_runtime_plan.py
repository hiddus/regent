"""Unit tests for run-think-learn runtime plan / fork / lessons helpers."""

from regent.application.goal_runtime_plan import (
    append_failure_lesson,
    build_runtime_plan,
    lessons_for_acceptance,
    synthesize_fork_options,
    synthesize_steps,
)


def test_synthesize_steps_prefers_model_steps() -> None:
    steps = synthesize_steps(
        first_deliverable="可预览登录页",
        problem="忘记密码",
        proposed_steps=["调研", "做原型", "验证"],
    )
    assert steps == ["调研", "做原型", "验证"]


def test_synthesize_steps_fallback_when_empty() -> None:
    steps = synthesize_steps(
        first_deliverable="可预览登录页",
        problem="忘记密码",
        proposed_steps=[],
    )
    assert len(steps) >= 3
    assert "可预览登录页" in steps[0]


def test_build_runtime_plan_clear_path_no_fork() -> None:
    plan = build_runtime_plan(
        app_name="Demo",
        product_intent="demo",
        target_users="用户",
        problem="问题",
        first_deliverable="预览",
        success_criteria={"preview": "可打开"},
        unknowns=["细节待定"],
        proposed_steps=["一步", "二步", "三步"],
        deduction_clear=True,
    )
    assert plan["needs_user_fork"] is False
    assert plan["fork_options"] == []
    assert plan["proposed_steps"] == ["一步", "二步", "三步"]


def test_build_runtime_plan_unclear_requires_fork() -> None:
    plan = build_runtime_plan(
        app_name="Demo",
        product_intent="demo",
        target_users="用户",
        problem="问题",
        first_deliverable="预览",
        success_criteria={},
        unknowns=["A还是B?", "面向谁?", "先做什么?"],
        proposed_steps=[],
        deduction_clear=False,
        fork_options=[
            {"id": "a", "label": "方向A", "description": "偏工具"},
            {"id": "b", "label": "方向B", "description": "偏社区"},
        ],
    )
    assert plan["needs_user_fork"] is True
    assert len(plan["fork_options"]) == 2
    assert plan["fork_options"][0]["id"] == "a"


def test_synthesize_fork_options_defaults_when_unclear() -> None:
    options = synthesize_fork_options(
        unknowns=["范围过大"],
        fork_options=None,
        deduction_clear=False,
    )
    assert len(options) >= 2
    assert all(o.get("id") and o.get("label") for o in options)


def test_append_failure_lesson_dedupes_and_caps() -> None:
    meta: dict = {}
    meta = append_failure_lesson(
        meta,
        code="GEN_FAIL",
        summary="missing package.json",
        avoid="ensure package.json before install",
        gap_kind="generation",
    )
    meta = append_failure_lesson(
        meta,
        code="GEN_FAIL",
        summary="missing package.json",
        avoid="ensure package.json before install",
        gap_kind="generation",
    )
    assert len(meta["failure_lessons"]) == 1
    digest = meta["failure_lessons"][0]["lesson_digest"]
    assert digest

    for i in range(20):
        meta = append_failure_lesson(
            meta,
            code=f"E{i}",
            summary=f"err {i}",
            avoid=f"avoid {i}",
        )
    assert len(meta["failure_lessons"]) <= 12
    lessons = lessons_for_acceptance(meta, limit=3)
    assert len(lessons) == 3


def test_lessons_for_acceptance_normalizes_legacy_gap_shape() -> None:
    """Legacy delivery-gap lessons lack summary/avoid but must still inject."""
    meta = {
        "failure_lessons": [
            {
                "lesson_digest": "legacyabc",
                "gap_kind": "presentation",
                "attempt": 1,
                "escalation_method": "REUSE",
                "gap_reasons": ["stylesheet-present: missing", "deployment-failed"],
                "learned_constraints": ["Include CSS stylesheet", "Fix deploy"],
                "last_error": "boom",
                "halt_message": "preview down",
            }
        ]
    }
    lessons = lessons_for_acceptance(meta, limit=8)
    assert len(lessons) == 1
    assert "stylesheet-present" in lessons[0]["summary"]
    assert "Include CSS" in lessons[0]["avoid"]
    assert lessons[0]["gap_kind"] == "presentation"
    assert lessons[0]["gap_reasons"][0] == "stylesheet-present: missing"


def test_lessons_for_acceptance_keeps_new_and_legacy_mixed() -> None:
    meta = {
        "failure_lessons": [
            {
                "lesson_digest": "legacy1",
                "gap_reasons": ["deploy failed"],
                "learned_constraints": ["retry with healthcheck"],
            },
            {
                "lesson_digest": "new1",
                "summary": "missing package.json",
                "avoid": "write package.json first",
                "gap_kind": "generation",
            },
            {"noise": True},
        ]
    }
    lessons = lessons_for_acceptance(meta, limit=8)
    assert len(lessons) == 2
    digests = {str(x.get("lesson_digest")) for x in lessons}
    assert digests == {"legacy1", "new1"}
    by_digest = {str(x["lesson_digest"]): x for x in lessons}
    assert by_digest["new1"]["summary"] == "missing package.json"
    assert "deploy failed" in by_digest["legacy1"]["summary"]
