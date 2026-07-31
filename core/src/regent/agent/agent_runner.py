"""Multi-turn agent runner with budgeted tool loop + dual-track compact (P1-1)."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Protocol

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
        context_artifacts: Any | None = None,
        execution_plans: Any | None = None,
        goal_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        producer_ref: str = "regent-agent",
    ) -> None:
        self._provider = provider
        self._toolkit = toolkit
        self._budget = budget or AgentBudget()
        self._regent_md = regent_md
        self._context_artifacts = context_artifacts
        self._execution_plans = execution_plans
        self._goal_id = goal_id
        self._run_id = run_id
        self._producer_ref = producer_ref
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
        on_turn: Callable[[int, str], Awaitable[None]] | None = None,
        _allow_nested_repair: bool = True,
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

            if on_turn is not None:
                await on_turn(
                    turn,
                    f"正在生成应用（第 {turn + 1}/{max_turns} 轮）…",
                )

            # Persist the complete transcript before any lossy compact operation.
            if (
                self._context_artifacts is not None
                and self._goal_id is not None
                and self._compactor.needs_auto_compact(conversation)
            ):
                await self._context_artifacts.save_transcript_before_compact(
                    goal_id=self._goal_id,
                    transcript=[asdict(m) for m in conversation],
                    producer_ref=self._producer_ref,
                    run_id=self._run_id,
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
                message_result = result_text
                if self._context_artifacts is not None and self._goal_id is not None:
                    ref = await self._context_artifacts.offload_tool_result(
                        goal_id=self._goal_id,
                        run_id=self._run_id,
                        text=result_text,
                        producer_ref=self._producer_ref,
                    )
                    if ref is not None:
                        message_result = json.dumps(ref.as_dict(), ensure_ascii=False)
                if (
                    call.name == "todo_write"
                    and self._execution_plans is not None
                    and self._goal_id is not None
                ):
                    from regent.application.execution_plan import UpsertPlanItem
                    from regent.domain.errors import DomainError, ErrorCode

                    # Scope keys by run so replan/regenerate does not try to reopen
                    # completed items from a prior run (INVALID_STATE → empty delivery).
                    run_scope = str(self._run_id) if self._run_id is not None else ""
                    try:
                        await self._execution_plans.upsert_items(
                            [
                                UpsertPlanItem(
                                    goal_id=self._goal_id,
                                    run_id=self._run_id,
                                    item_key=(
                                        f"{run_scope}:{item.get('id')}"
                                        if run_scope
                                        else str(item.get("id") or "")
                                    ),
                                    content=str(item.get("content") or ""),
                                    status=str(item.get("status") or "pending"),
                                )
                                for item in self._toolkit.todos
                                if item.get("id")
                            ]
                        )
                    except DomainError as exc:
                        if exc.code != ErrorCode.INVALID_STATE:
                            raise
                        # Absorb terminal-immutability conflicts: keep generating
                        # instead of aborting the whole run into DeliveryGapExhaust.
                tool_msg = ChatMessage(
                    role="tool",
                    content=message_result,
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
            # GQ-2: exactly one controlled repair when verification fails.
            if (
                _allow_nested_repair
                and verification is not None
                and not verification.passed
                and verification.gaps
            ):
                if on_turn is not None:
                    await on_turn(-1, "验证失败，启动受控修正轮…")
                repaired = await self.run(
                    plan,
                    prior_gaps=list(verification.gaps),
                    verify=True,
                    run_smoke=run_smoke,
                    on_turn=on_turn,
                    _allow_nested_repair=False,
                )
                if repaired.verification is not None:
                    smoke = dict(repaired.verification.smoke or {})
                    smoke["controlled_repair_attempted"] = True
                    smoke["pre_repair_gaps"] = [
                        {"code": g.code, "detail": g.detail} for g in verification.gaps[:8]
                    ]
                    repaired.verification = VerificationVerdict(
                        verdict=repaired.verification.verdict,
                        gaps=list(repaired.verification.gaps),
                        smoke=smoke,
                        summary=repaired.verification.summary,
                    )
                return repaired

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
