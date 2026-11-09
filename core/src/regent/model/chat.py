"""Chat / tool-calling message types shared by model providers and agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    # Worst-case monetary cost declared by the tool. Local workspace tools are free.
    max_cost: float = 0.0


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
    # DeepSeek V4 thinking: must round-trip on tool-call turns when thinking enabled.
    reasoning_content: str | None = None


@dataclass(frozen=True, slots=True)
class ChatUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    # Prompt-cache hits reported by the provider (OpenAI-style prompt_tokens_details
    # or vendor equivalents). None = field absent; 0 = explicitly none cached.
    cached_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ChatResponse:
    message: ChatMessage
    usage: ChatUsage
    model: str
    finish_reason: str = "stop"
