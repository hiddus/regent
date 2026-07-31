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


@pytest.mark.asyncio
async def test_available_tools_exposes_all_eight_handlers() -> None:
    service = AppGuidanceService(sessions=AsyncMock(), provider=AsyncMock())
    tools = service.available_tools()
    assert len(tools) == 8
    assert all(isinstance(t, ToolSpec) for t in tools)
    assert {"query", "continue", "modify", "pause", "resume", "correct", "approve", "reject"} == {
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
