from __future__ import annotations

import pytest

from regent.agent.tools import validate_tool_call
from regent.agent.types import ToolCall
from regent.agent.agent_runner import _reserved_input_tokens


def test_protocol_guard_accepts_valid_action() -> None:
    validate_tool_call(
        ToolCall(id="1", name="read_file", arguments={"path": "README.md"})
    )


@pytest.mark.parametrize(
    "call",
    [
        ToolCall(id="1", name="read_file", arguments={}),
        ToolCall(id="2", name="read_file", arguments={"path": "x", "surprise": True}),
        ToolCall(id="3", name="run_command", arguments={"command": "pytest", "timeout_seconds": "slow"}),
        ToolCall(
            id="4",
            name="todo_write",
            arguments={"todos": [{"id": "a", "content": "x", "status": "invalid"}]},
        ),
    ],
)
def test_protocol_guard_rejects_malformed_action_before_execution(call: ToolCall) -> None:
    with pytest.raises(ValueError):
        validate_tool_call(call)


def test_model_hold_reserves_bounded_uncertainty_not_full_context() -> None:
    reserved = _reserved_input_tokens(
        8_000, context_window=128_000, max_output=8_192
    )
    assert reserved == 10_000
    assert reserved < 128_000 - 8_192
