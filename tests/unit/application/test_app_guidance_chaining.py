"""CD-4.1: AppGuidanceService bounded multi-step guidance loop."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from regent.application.app_guidance_service import (
    AppGuidanceService,
    GuidanceInterpretation,
    GuidanceReceipt,
    _guidance_model_context,
    _deterministic_control_intent,
)
from regent.model.chat import ToolSpec


class _FakeStructuredResponse:
    def __init__(self, output: GuidanceInterpretation, model: str = "fake-v1") -> None:
        self.output = output
        self.model = model


class _FakeProvider:
    def __init__(self, output: GuidanceInterpretation) -> None:
        self._output = output

    async def generate_structured(self, **_: Any) -> _FakeStructuredResponse:
        return _FakeStructuredResponse(self._output)


def _service(interpretation: GuidanceInterpretation) -> AppGuidanceService:
    service = AppGuidanceService(sessions=AsyncMock(), provider=_FakeProvider(interpretation))
    service._context = AsyncMock(return_value={"goal": {"status": "ACTIVE"}})  # type: ignore[method-assign]
    service._conversation_history = AsyncMock(return_value=[])  # type: ignore[method-assign]
    service._maybe_resume_research_more = AsyncMock(return_value=None)  # type: ignore[method-assign]
    return service


def _receipt(command_type: str, response: str) -> GuidanceReceipt:
    return GuidanceReceipt(
        command_id=uuid.uuid4(),
        command_type=command_type,
        resulting_goal_id=None,
        requires_confirmation=False,
        response=response,
    )


def test_guidance_model_context_excludes_large_runtime_diagnostics() -> None:
    full = {
            "project": {"name": "p", "product_intent": "x", "status": "ACTIVE"},
            "goal": {
                "id": "g",
                "objective": "build",
                "status": "ACTIVE",
                "execution_stage": "GENERATING",
                "metadata": {
                    "needs_user_fork": False,
                    "tool_events": ["huge"] * 1_000,
                    "failure_envelopes": [{"stderr": "huge" * 1_000}],
                },
            },
            "goal_spec": {"unknowns": []},
            "pending_human_tasks": [],
        }
    slim = _guidance_model_context(full)
    encoded = str(slim)
    assert "tool_events" not in encoded
    assert "failure_envelopes" not in encoded
    assert slim["goal"]["metadata"]["needs_user_fork"] is False
    assert len(encoded) < len(str(full)) * 0.1


@pytest.mark.parametrize(
    ("message", "status", "pending", "expected"),
    [
        ("进度怎么样？", "ACTIVE", False, "QUERY"),
        ("先停一下", "ACTIVE", False, "PAUSE"),
        ("恢复执行", "PAUSED", False, "RESUME"),
        ("确认", "WAITING_HUMAN", True, "APPROVE"),
        ("不行", "WAITING_HUMAN", True, "REJECT"),
        ("继续使用 SQLite 完成导出", "ACTIVE", False, None),
    ],
)
def test_deterministic_control_intent_is_conservative(
    message: str, status: str, pending: bool, expected: str | None
) -> None:
    assert _deterministic_control_intent(
        message, goal_status=status, has_pending_task=pending
    ) == expected


@pytest.mark.asyncio
async def test_available_tools_exposes_all_nine_handlers() -> None:
    service = AppGuidanceService(sessions=AsyncMock(), provider=AsyncMock())
    tools = service.available_tools()
    assert len(tools) == 9
    assert all(isinstance(t, ToolSpec) for t in tools)
    assert {"query", "continue", "modify", "pause", "resume", "correct", "approve", "reject", "select_option"} == {
        t.name for t in tools
    }


@pytest.mark.asyncio
async def test_guide_without_follow_up_dispatches_once() -> None:
    interpretation = GuidanceInterpretation(command_type="QUERY", summary="status?")
    service = _service(interpretation)
    service._handle_query = AsyncMock(return_value=_receipt("QUERY", "current status"))  # type: ignore[method-assign]

    receipt = await service.guide(uuid.uuid4(), message="how's it going?", actor="tester")

    assert receipt.command_type == "QUERY"
    assert receipt.response == "current status"
    service._handle_query.assert_awaited_once()


@pytest.mark.asyncio
async def test_guide_chains_single_follow_up_command() -> None:
    interpretation = GuidanceInterpretation(
        command_type="QUERY",
        summary="status then continue",
        follow_up_command="CONTINUE",
        follow_up_summary="proceed",
    )
    service = _service(interpretation)
    service._handle_query = AsyncMock(return_value=_receipt("QUERY", "current status"))  # type: ignore[method-assign]
    service._handle_continue = AsyncMock(return_value=_receipt("CONTINUE", "resumed execution"))  # type: ignore[method-assign]

    receipt = await service.guide(uuid.uuid4(), message="status, then go ahead", actor="tester")

    service._handle_query.assert_awaited_once()
    service._handle_continue.assert_awaited_once()
    assert receipt.command_type == "QUERY+CONTINUE"
    assert "current status" in receipt.response
    assert "resumed execution" in receipt.response


@pytest.mark.asyncio
async def test_guide_chain_is_bounded_and_terminates() -> None:
    """Manually-constructed follow-ups never carry their own follow_up_command,
    so the bounded loop must terminate after exactly one chained hop (2 total
    dispatches), well within _MAX_GUIDANCE_STEPS."""
    interpretation = GuidanceInterpretation(
        command_type="QUERY", summary="s", follow_up_command="CONTINUE"
    )
    service = _service(interpretation)
    service._handle_query = AsyncMock(return_value=_receipt("QUERY", "q"))  # type: ignore[method-assign]
    service._handle_continue = AsyncMock(return_value=_receipt("CONTINUE", "c"))  # type: ignore[method-assign]

    await service.guide(uuid.uuid4(), message="m", actor="tester")

    assert service._handle_query.await_count == 1
    assert service._handle_continue.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("[补充信息] 增加导出功能", "CORRECT"),
        ("[纠正方向] 不要使用 GraphQL", "CORRECT"),
        ("[询问进度] 现在做到哪里了", "QUERY"),
        ("[继续执行] 按当前方案继续", "CONTINUE"),
    ],
)
async def test_explicit_console_intent_bypasses_model(
    message: str, expected: str,
) -> None:
    provider = AsyncMock()
    service = AppGuidanceService(sessions=AsyncMock(), provider=provider)
    service._context = AsyncMock(return_value={"goal": {"status": "ACTIVE"}})  # type: ignore[method-assign]
    service._conversation_history = AsyncMock(return_value=[])  # type: ignore[method-assign]
    service._dispatch = AsyncMock(return_value=_receipt(expected, "ok"))  # type: ignore[method-assign]

    receipt = await service.guide(uuid.uuid4(), message=message, actor="tester")

    assert receipt.command_type == expected
    provider.generate_structured.assert_not_called()
    assert service._dispatch.await_args.args[3].command_type == expected
