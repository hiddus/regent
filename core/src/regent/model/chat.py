"""Chat / tool-calling message types shared by model providers and agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ChatUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ChatResponse:
    message: ChatMessage
    usage: ChatUsage
    model: str
    finish_reason: str = "stop"
