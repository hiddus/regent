"""Regression: identical tool-call anti-loop (warn then ASK) in AgentRunner."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from regent.agent.agent_runner import (
    REPEAT_CALL_ASK_AFTER,
    REPEAT_CALL_WARN_AFTER,
    AgentRunner,
)
from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import AgentBudget, ArtifactIncompleteError
from regent.application.agent_control import AskUserRequiredError
from regent.model.chat import ChatMessage, ChatResponse, ChatUsage, ToolCall


def _same_call_response(seq: int, *, tool: str = "run_command") -> ChatResponse:
    return ChatResponse(
        message=ChatMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id=f"call-{seq}",
                    name=tool,
                    arguments={"command": "ls -la /missing/path"}
                    if tool == "run_command"
                    else {"path": "."},
                )
            ],
        ),
        usage=ChatUsage(1, 1),
        model="m",
        finish_reason="tool_calls",
    )


def _stop_response() -> ChatResponse:
    return ChatResponse(
        message=ChatMessage(role="assistant", content="stopped", tool_calls=[]),
        usage=ChatUsage(1, 1),
        model="m",
        finish_reason="stop",
    )


@pytest.mark.asyncio
async def test_identical_call_loop_escalates_to_ask(tmp_path: Path) -> None:
    class _Prov:
        def __init__(self) -> None:
            self.n = 0

        async def chat(self, **kwargs: Any) -> Any:
            self.n += 1
            return _same_call_response(self.n)

    runner = AgentRunner(
        _Prov(),
        WorkspaceToolkit(tmp_path),
        budget=AgentBudget(max_turns=20, max_tokens=50_000, max_wall_seconds=60),
        execution_mode="act",
    )
    with pytest.raises(AskUserRequiredError) as exc:
        await runner.run({"goal_anchor_text": "x"}, verify=False)
    envelope = exc.value.envelope or {}
    assert envelope.get("ask_type") == "progress_loop"
    assert envelope.get("gap_kind") == "PROGRESS_LOOP"


@pytest.mark.asyncio
async def test_repeated_call_warns_without_executing(tmp_path: Path) -> None:
    total = REPEAT_CALL_WARN_AFTER + 2

    class _Prov:
        def __init__(self) -> None:
            self.n = 0

        async def chat(self, **kwargs: Any) -> Any:
            self.n += 1
            if self.n <= total:
                return _same_call_response(self.n, tool="list_files")
            return _stop_response()

    runner = AgentRunner(
        _Prov(),
        WorkspaceToolkit(tmp_path),
        budget=AgentBudget(max_turns=total + 4, max_tokens=50_000, max_wall_seconds=60),
        execution_mode="act",
    )
    executed = {"n": 0}
    real_exec = runner._execute_toolkit_call

    async def _counting_exec(call: Any, *, turn: int) -> str:
        executed["n"] += 1
        return await real_exec(call, turn=turn)

    runner._execute_toolkit_call = _counting_exec  # type: ignore[method-assign]

    with pytest.raises(ArtifactIncompleteError):
        await runner.run({"goal_anchor_text": "x"}, verify=False)

    # Calls 1..WARN_AFTER-1 execute; the rest hit the RepeatedToolCall warning
    # branch and must NOT be re-executed.
    assert executed["n"] == REPEAT_CALL_WARN_AFTER - 1
    assert REPEAT_CALL_ASK_AFTER > REPEAT_CALL_WARN_AFTER


def _alt_call_response(seq: int) -> ChatResponse:
    """Alternate between two distinct run_command calls (A, B, A, B, ...)."""
    command = "ls -la /missing/a" if seq % 2 else "ls -la /missing/b"
    return ChatResponse(
        message=ChatMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(id=f"call-{seq}", name="run_command", arguments={"command": command})
            ],
        ),
        usage=ChatUsage(1, 1),
        model="m",
        finish_reason="tool_calls",
    )


@pytest.mark.asyncio
async def test_alternating_call_pair_loop_escalates_to_ask(tmp_path: Path) -> None:
    """Regression: A,B,A,B,... evaded consecutive-only repeat detection."""

    class _Prov:
        def __init__(self) -> None:
            self.n = 0

        async def chat(self, **kwargs: Any) -> Any:
            self.n += 1
            return _alt_call_response(self.n)

    runner = AgentRunner(
        _Prov(),
        WorkspaceToolkit(tmp_path),
        budget=AgentBudget(max_turns=20, max_tokens=50_000, max_wall_seconds=60),
        execution_mode="act",
    )
    executed = {"n": 0}
    real_exec = runner._execute_toolkit_call

    async def _counting_exec(call: Any, *, turn: int) -> str:
        executed["n"] += 1
        return await real_exec(call, turn=turn)

    runner._execute_toolkit_call = _counting_exec  # type: ignore[method-assign]

    with pytest.raises(AskUserRequiredError) as exc:
        await runner.run({"goal_anchor_text": "x"}, verify=False)
    envelope = exc.value.envelope or {}
    assert envelope.get("ask_type") == "progress_loop"
    # Each of A/B appeared REPEAT_CALL_ASK_AFTER times; earlier repeats warned
    # without executing, so total executions stay well below the turn count.
    assert executed["n"] < REPEAT_CALL_ASK_AFTER * 2
