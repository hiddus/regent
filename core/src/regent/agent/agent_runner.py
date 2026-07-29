"""Multi-turn agent runner with budgeted tool loop + dual-track compact (P1-1)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from regent.agent.compact import ContextCompactor, HeuristicSummarizer, micro_compact
from regent.agent.context_assembler import ContextAssembler
from regent.agent.tools import TOOL_SPECS, WorkspaceToolkit
from regent.agent.types import (
    AgentBudget,
    BudgetExhaustedError,
    ChatMessage,
    TranscriptTurn,
    VerificationGap,
    VerificationVerdict,
)
from regent.agent.verification import VerificationAgent


class ChatProvider(Protocol):
    async def chat(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[Any] | None = None,
        temperature: float = 0,
    ) -> Any: ...


@dataclass
class AgentRunResult:
    files: dict[str, str]
    transcript: list[TranscriptTurn] = field(default_factory=list)
    verification: VerificationVerdict | None = None
    model_ref: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    compact_events: list[dict[str, Any]] = field(default_factory=list)


class AgentRunner:
    """Replace single-shot structured generation with a tool-using agent loop."""

    def __init__(
        self,
        provider: ChatProvider,
        toolkit: WorkspaceToolkit,
        *,
        budget: AgentBudget | None = None,
        regent_md: str = "",
        context_window_tokens: int = 128_000,
    ) -> None:
        self._provider = provider
        self._toolkit = toolkit
        self._budget = budget or AgentBudget()
        self._regent_md = regent_md
        self._compactor = ContextCompactor(
            toolkit=toolkit,
            summarizer=HeuristicSummarizer(),
            context_window_tokens=context_window_tokens,
        )

    async def run(
        self,
        plan: dict[str, Any],
        *,
        prior_gaps: list[VerificationGap] | None = None,
        verify: bool = True,
        run_smoke: bool = True,
    ) -> AgentRunResult:
        assembler = ContextAssembler(
            plan=plan,
            toolkit=self._toolkit,
            regent_md=self._regent_md,
            gaps=prior_gaps,
        )
        conversation: list[ChatMessage] = []
        transcript: list[TranscriptTurn] = []
        compact_events: list[dict[str, Any]] = []
        input_tokens = 0
        output_tokens = 0
        model_ref = ""
        started = time.monotonic()
        max_turns = self._budget.max_turns
        goal_anchor = assembler._goal_anchor_segment()  # noqa: SLF001 — shared text

        for turn in range(max_turns):
            wall = time.monotonic() - started
            if wall > self._budget.max_wall_seconds:
                raise BudgetExhaustedError(
                    f"wall time {wall:.0f}s exceeded max_wall_seconds="
                    f"{self._budget.max_wall_seconds}"
                )
            if input_tokens + output_tokens > self._budget.max_tokens:
                raise BudgetExhaustedError(
                    f"token budget exceeded ({input_tokens + output_tokens} > "
                    f"{self._budget.max_tokens})"
                )

            # P1-1 autoCompact near window.
            auto = await self._compactor.maybe_auto_compact(
                conversation,
                goal_anchor=goal_anchor,
                todos=list(self._toolkit.todos),
            )
            if auto.did_compact:
                conversation = auto.messages
                compact_events.append(
                    {
                        "turn": turn,
                        "kind": "autoCompact",
                        "summary_chars": len(auto.summary),
                    }
                )
            elif auto.failed:
                compact_events.append(
                    {
                        "turn": turn,
                        "kind": "autoCompact_failed",
                        "detail": auto.summary,
                        "failures": self._compactor.state.auto_failures,
                    }
                )

            messages = assembler.assemble(turn=turn, conversation=conversation)
            response = await self._provider.chat(
                messages=messages,
                tools=TOOL_SPECS,
                temperature=0,
            )
            model_ref = getattr(response, "model", model_ref) or model_ref
            usage = getattr(response, "usage", None)
            if usage is not None:
                input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
                output_tokens += int(getattr(usage, "output_tokens", 0) or 0)

            assistant: ChatMessage = response.message
            conversation.append(assistant)
            transcript.append(
                TranscriptTurn(
                    turn=turn,
                    role="assistant",
                    content=assistant.content,
                    input_tokens=int(getattr(usage, "input_tokens", 0) or 0) if usage else 0,
                    output_tokens=int(getattr(usage, "output_tokens", 0) or 0) if usage else 0,
                )
            )

            if not assistant.tool_calls:
                break

            for call in assistant.tool_calls:
                result_text = await self._toolkit.execute(call)
                tool_msg = ChatMessage(
                    role="tool",
                    content=result_text,
                    tool_call_id=call.id,
                    name=call.name,
                )
                conversation.append(tool_msg)
                transcript.append(
                    TranscriptTurn(
                        turn=turn,
                        role="tool",
                        content=None,
                        tool_name=call.name,
                        tool_call_id=call.id,
                        tool_arguments=dict(call.arguments),
                        tool_result=result_text[:4_000],
                    )
                )
                conversation = micro_compact(conversation, keep_recent=8)
        else:
            raise BudgetExhaustedError(f"max_turns={max_turns} exhausted")

        files = self._toolkit.snapshot_files()
        if not files:
            raise RuntimeError("agent produced no files")

        verification: VerificationVerdict | None = None
        if verify:
            acceptance = dict(plan.get("acceptance_contract") or {})
            verification = await VerificationAgent(self._toolkit).verify(
                acceptance_contract=acceptance,
                success_criteria=acceptance.get("success_criteria"),
                run_smoke=run_smoke,
            )

        return AgentRunResult(
            files=files,
            transcript=transcript,
            verification=verification,
            model_ref=model_ref,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            turns=len({t.turn for t in transcript}),
            compact_events=compact_events,
        )
