"""Shared types for the agentic generation engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from regent.model.chat import ChatMessage, ChatResponse, ChatUsage, ToolCall, ToolSpec

__all__ = [
    "AgentBudget",
    "BudgetExhaustedError",
    "ChatMessage",
    "ChatResponse",
    "ChatUsage",
    "ToolCall",
    "ToolSpec",
    "TranscriptTurn",
    "VerificationFailedError",
    "VerificationGap",
    "VerificationVerdict",
]


@dataclass(frozen=True, slots=True)
class AgentBudget:
    max_turns: int = 40
    max_tokens: int = 200_000
    max_wall_seconds: int = 900


@dataclass(frozen=True, slots=True)
class TranscriptTurn:
    turn: int
    role: str
    content: str | None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_arguments: dict[str, Any] | None = None
    tool_result: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class VerificationGap:
    code: str
    detail: str
    artifact_snippet: str = ""


@dataclass(frozen=True, slots=True)
class VerificationVerdict:
    verdict: Literal["PASS", "FAIL"]
    gaps: list[VerificationGap] = field(default_factory=list)
    smoke: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"


class BudgetExhaustedError(RuntimeError):
    """Raised when agent budget (turns/tokens/wall time) is exhausted."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.failure_code = "EXHAUSTED_BUDGET"


class VerificationFailedError(RuntimeError):
    """Raised when verification fails after agent generation."""

    def __init__(self, verdict: VerificationVerdict) -> None:
        gaps = "; ".join(f"{g.code}: {g.detail}" for g in verdict.gaps[:8])
        super().__init__(f"verification failed: {gaps or verdict.summary}")
        self.verdict = verdict
        self.failure_code = "VERIFICATION_FAILED"
