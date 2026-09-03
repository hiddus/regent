from __future__ import annotations

import httpx
import pytest
from regent.application import goal_task_verifier
from regent.application.goal_task_verifier import verify_goal_task_completion


@pytest.mark.asyncio
async def test_unknown_criteria_fail_closed() -> None:
    verdict = await verify_goal_task_completion(
        "http://preview.test",
        {"weekly_business_diagnosis_is_actionable": True},
    )

    assert verdict.passed is False
    assert verdict.skipped_reason == "no_criteria_matched"


@pytest.mark.asyncio
async def test_recognized_task_criteria_are_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            request=request,
            text=(
                '<html><meta name="viewport" content="width=device-width">'
                '<form><input type="text"><input type="submit"></form></html>'
            ),
        )
    )
    client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(goal_task_verifier.httpx, "AsyncClient", lambda **_: client)

    verdict = await verify_goal_task_completion(
        "http://preview.test",
        {"表单输入后可以提交": True, "移动端响应式页面": True},
    )

    assert verdict.passed is True
    assert {criterion.label for criterion in verdict.criteria} == {
        "has_submit_form",
        "has_responsive",
    }


@pytest.mark.asyncio
async def test_missing_requested_feature_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, request=request, text="<html>plain</html>")
    )
    client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(goal_task_verifier.httpx, "AsyncClient", lambda **_: client)

    verdict = await verify_goal_task_completion(
        "http://preview.test",
        {"页面需要登录入口": True},
    )

    assert verdict.passed is False
    assert verdict.criteria[0].label == "has_login_page"
