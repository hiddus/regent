from regent.application.app_guidance_service import (
    build_reviewable_plan_response,
    looks_like_plan_query,
)


def test_explicit_plan_requests_are_detected_without_model_classification() -> None:
    assert looks_like_plan_query("给我看看当前计划")
    assert looks_like_plan_query("这个方案有哪些步骤？")
    assert looks_like_plan_query("show me the plan")
    assert not looks_like_plan_query("继续执行")


def test_reviewable_plan_contract_is_complete_and_does_not_dump_internal_state() -> None:
    context = {
        "goal": {
            "objective": "制作预约应用",
            "status": "DRAFT",
            "metadata": {
                "target_users": "景区游客",
                "problem": "电话预约效率低",
                "first_deliverable": "可点击原型",
                "runtime_plan": {"proposed_steps": ["确认预约字段", "制作原型"]},
                "budget_limit": 12000,
                "clarification_rounds": 1,
                "feasibility_verdict": "REVISION_REQUIRED",
                "secret_internal_nonce": "must-not-leak",
            },
        },
        "goal_spec": {
            "success_criteria": {"booking": "用户可完成一次预约"},
            "unknowns": ["是否需要在线支付？"],
        },
    }
    response = build_reviewable_plan_response(context)
    for heading in ("目标", "当前假设", "步骤", "验收标准", "待确认", "预算与停止条件", "下一动作"):
        assert f"## {heading}" in response
    assert "可行性通过前不会开始执行" in response
    assert "must-not-leak" not in response
    assert "DRAFT" not in response


def test_feasible_plan_asks_for_lock_confirmation_not_immediate_execution() -> None:
    response = build_reviewable_plan_response({
        "goal": {"objective": "目标", "metadata": {"clarification_rounds": 2, "feasibility_verdict": "FEASIBLE", "budget_limit": 100}},
        "goal_spec": {"unknowns": [], "success_criteria": {"done": "交付可验收"}},
    })
    assert "请确认锁定当前目标版本" in response
    assert "确认后才开始执行" in response
