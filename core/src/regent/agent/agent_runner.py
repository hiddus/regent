"""Multi-turn agent runner with budgeted tool loop + single-trajectory repair (M3-1)."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from regent.agent.compact import ContextCompactor, HeuristicSummarizer, micro_compact
from regent.agent.context_assembler import ContextAssembler
from regent.agent.primary_failure import classify_finish_reason
from regent.agent.repair_policy import plan_repair, record_branch_cost
from regent.agent.run_ledger import AgentRunLedger
from regent.agent.runtime_profile_v1 import RuntimeProfileV1, parse_runtime_profile_v1
from regent.agent.skills import select_skills_for_goal
from regent.agent.tools import TOOL_SPECS, WorkspaceToolkit
from regent.agent.types import (
    AgentBudget,
    ArtifactIncompleteError,
    BudgetExhaustedError,
    ChatMessage,
    TranscriptTurn,
    VerificationGap,
    VerificationVerdict,
)
from regent.agent.verification import VerificationAgent
from regent.model import ModelTruncatedError, ToolCallInvalidError


def _preview(value: Any, limit: int = 240) -> str:
    try:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


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
    ledger: AgentRunLedger = field(default_factory=AgentRunLedger)
    submitted: bool = False
    repair_branch_log: list[dict[str, Any]] = field(default_factory=list)
    skill_refs: list[dict[str, Any]] = field(default_factory=list)


class AgentRunner:
    """Tool-using agent loop with explicit submit and non-recursive repair."""

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
        runtime_profile: RuntimeProfileV1 | dict[str, Any] | None = None,
        skills_enabled: bool = True,
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
        if isinstance(runtime_profile, RuntimeProfileV1):
            self._profile = runtime_profile
        else:
            self._profile = parse_runtime_profile_v1(
                dict(runtime_profile) if runtime_profile else None
            )
        self._skills_enabled = skills_enabled
        self._compactor = ContextCompactor(
            toolkit=toolkit,
            summarizer=HeuristicSummarizer(),
            context_window_tokens=context_window_tokens,
        )

    def _raise_budget_exhausted(
        self,
        reason: str,
        *,
        transcript: list[TranscriptTurn],
        ledger: AgentRunLedger,
        compact_events: list[dict[str, Any]],
    ) -> None:
        report = self._toolkit.snapshot_files_report()
        ledger.primary_failure_code = "BUDGET_EXHAUSTED"
        ledger.snapshot_file_count = len(report.files)
        ledger.snapshot_truncated = report.truncated
        ledger.transcript_turns = len(transcript)
        manifest: dict[str, Any] = {
            "primary_failure_code": "BUDGET_EXHAUSTED",
            "reason": reason,
            "promote_allowed": False,
            "snapshot": report.as_dict(),
            "ledger": ledger.as_dict(),
            "compact_events": compact_events,
            "transcript_turns": len(transcript),
        }
        root = self._toolkit.root
        (root / ".regent_budget_exhausted.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (root / ".regent_run_ledger.json").write_text(
            json.dumps(ledger.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        payload = [asdict(t) if hasattr(t, "__dataclass_fields__") else t for t in transcript]
        # TranscriptTurn is dataclass — use manual dict for safety
        payload = [
            {
                "turn": t.turn,
                "role": t.role,
                "content": t.content,
                "tool_name": t.tool_name,
                "tool_call_id": t.tool_call_id,
                "tool_arguments": t.tool_arguments,
                "tool_result": t.tool_result,
                "input_tokens": t.input_tokens,
                "output_tokens": t.output_tokens,
            }
            for t in transcript
        ]
        (root / ".regent_agent_transcript.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise BudgetExhaustedError(
            reason,
            diagnostic_manifest=manifest,
            files=dict(report.files),
            ledger=ledger,
        )

    async def run(
        self,
        plan: dict[str, Any],
        *,
        prior_gaps: list[VerificationGap] | None = None,
        verify: bool = True,
        run_smoke: bool = True,
        on_turn: Callable[[int, str], Awaitable[None]] | None = None,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        _nested_repair_budget: int | None = None,
        _parent_ledger: AgentRunLedger | None = None,
    ) -> AgentRunResult:
        # Keep _nested_repair_budget name for call-site compat; used as max repair rounds.
        if _nested_repair_budget is None:
            from regent.config import get_settings

            _nested_repair_budget = get_settings().agent_nested_repair_max

        skill_refs: list[dict[str, Any]] = []
        if self._skills_enabled:
            skills = select_skills_for_goal(
                str(plan.get("goal_anchor_text") or ""),
                enabled=True,
            )
            skill_refs = [s.as_dict() for s in skills]
            if skills:
                guidance = "\n\n".join(
                    f"### Skill {s.skill_id}@{s.version} ({s.content_hash[:12]})\n{s.guidance}"
                    for s in skills
                )
                plan = {
                    **plan,
                    "skill_guidance": guidance,
                    "skill_refs": skill_refs,
                }

        profile = self._profile
        if plan.get("runtime_profile"):
            profile = parse_runtime_profile_v1(dict(plan["runtime_profile"])) or profile

        assembler = ContextAssembler(
            plan=plan,
            toolkit=self._toolkit,
            regent_md=self._regent_md,
            gaps=prior_gaps,
        )
        conversation: list[ChatMessage] = []
        transcript: list[TranscriptTurn] = []
        compact_events: list[dict[str, Any]] = []
        repair_branch_log: list[dict[str, Any]] = []
        ledger = AgentRunLedger()
        input_tokens = 0
        output_tokens = 0
        model_ref = ""
        started = time.monotonic()
        max_turns = self._budget.max_turns
        goal_anchor = assembler._goal_anchor_segment()  # noqa: SLF001
        repair_rounds_left = int(_nested_repair_budget)
        gap_repeat: dict[str, int] = {}
        turn = 0
        verification: VerificationVerdict | None = None

        while turn < max_turns:
            wall = time.monotonic() - started
            if wall > self._budget.max_wall_seconds:
                ledger.wall_seconds = wall
                self._raise_budget_exhausted(
                    f"wall time {wall:.0f}s exceeded max_wall_seconds="
                    f"{self._budget.max_wall_seconds}",
                    transcript=transcript,
                    ledger=ledger,
                    compact_events=compact_events,
                )
            if input_tokens + output_tokens > self._budget.max_tokens:
                ledger.wall_seconds = time.monotonic() - started
                self._raise_budget_exhausted(
                    f"token budget exceeded ({input_tokens + output_tokens} > "
                    f"{self._budget.max_tokens})",
                    transcript=transcript,
                    ledger=ledger,
                    compact_events=compact_events,
                )

            if on_turn is not None:
                await on_turn(
                    turn,
                    f"正在生成应用（第 {turn + 1}/{max_turns} 轮）…",
                )

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
            auto = await self._compactor.maybe_auto_compact(
                conversation,
                goal_anchor=goal_anchor,
                todos=list(self._toolkit.todos),
            )
            if auto.did_compact:
                conversation = auto.messages
                ledger.compact_events += 1
                compact_events.append(
                    {"turn": turn, "kind": "autoCompact", "summary_chars": len(auto.summary)}
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
            try:
                response = await self._provider.chat(
                    messages=messages,
                    tools=TOOL_SPECS,
                    temperature=0,
                )
            except (ModelTruncatedError, ToolCallInvalidError):
                ledger.wall_seconds = time.monotonic() - started
                ledger.add_usage(input_tokens=input_tokens, output_tokens=output_tokens)
                raise
            # Record provider HTTP attempts when available (M1-2 ledger).
            attempts = getattr(self._provider, "last_http_attempts", None)
            if attempts:
                ledger.notes.append(f"provider_attempts@{turn}={len(attempts)}")

            model_ref = getattr(response, "model", model_ref) or model_ref
            usage = getattr(response, "usage", None)
            turn_in = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
            turn_out = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
            turn_cached = getattr(usage, "cached_tokens", None) if usage else None
            turn_cached_i = int(turn_cached or 0) if turn_cached is not None else 0
            input_tokens += turn_in
            output_tokens += turn_out
            ledger.add_usage(
                input_tokens=turn_in,
                output_tokens=turn_out,
                cached_tokens=turn_cached_i,
            )
            ledger.add_turn(1)

            assistant: ChatMessage = response.message
            conversation.append(assistant)
            transcript.append(
                TranscriptTurn(
                    turn=turn,
                    role="assistant",
                    content=assistant.content,
                    input_tokens=turn_in,
                    output_tokens=turn_out,
                )
            )

            finish_reason = str(getattr(response, "finish_reason", "stop") or "stop")
            classified = classify_finish_reason(
                finish_reason, had_tool_calls=bool(assistant.tool_calls)
            )
            if classified is not None:
                from regent.agent.primary_failure import PrimaryFailureCode

                if classified is PrimaryFailureCode.MODEL_TRUNCATED:
                    raise ModelTruncatedError(
                        f"model finish_reason={finish_reason!r} without complete tool turn"
                    )
                raise ToolCallInvalidError(
                    f"model finish_reason={finish_reason!r} inconsistent with tool_calls"
                )

            if not assistant.tool_calls:
                # Soft stop without submit → incomplete (M1-3).
                break

            submitted_this_turn = False
            for call in assistant.tool_calls:
                ledger.add_tool_invocation(1)
                result_text = await self._toolkit.execute(call)
                message_result = result_text
                if call.name == "submit":
                    submitted_this_turn = True
                if on_event is not None:
                    await on_event(
                        {
                            "type": "tool_call",
                            "turn": turn,
                            "tool": call.name,
                            "args_preview": _preview(call.arguments),
                            "result_preview": _preview(result_text),
                        }
                    )
                if self._context_artifacts is not None and self._goal_id is not None:
                    ref = await self._context_artifacts.offload_tool_result(
                        goal_id=self._goal_id,
                        run_id=self._run_id,
                        text=result_text,
                        producer_ref=self._producer_ref,
                    )
                    if ref is not None:
                        message_result = json.dumps(ref.as_dict(), ensure_ascii=False)
                        # Make offload readable via read_artifact (M3-3).
                        self._toolkit.register_artifact(
                            ref=str(getattr(ref, "uri", None) or ref),
                            text=result_text,
                        )
                if (
                    call.name == "todo_write"
                    and self._execution_plans is not None
                    and self._goal_id is not None
                ):
                    from regent.application.execution_plan import UpsertPlanItem
                    from regent.domain.errors import DomainError, ErrorCode

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

            turn += 1

            if not submitted_this_turn and not self._toolkit.submitted:
                continue

            # Explicit submit → verify on same trajectory (M1-3 / M3-1).
            if not verify:
                break

            acceptance = dict(plan.get("acceptance_contract") or {})
            verification = await VerificationAgent(
                self._toolkit, runtime_profile=profile
            ).verify(
                acceptance_contract=acceptance,
                success_criteria=acceptance.get("success_criteria"),
                run_smoke=run_smoke,
                runtime_profile=profile,
            )
            if verification.passed:
                break

            if repair_rounds_left <= 0 or not verification.gaps:
                break

            primary = verification.gaps[0].code
            gap_repeat[primary] = gap_repeat.get(primary, 0) + 1
            remaining = self._budget.max_tokens - (input_tokens + output_tokens)
            repair = plan_repair(
                primary,
                repeat_count=gap_repeat[primary],
                remaining_token_budget=remaining,
            )
            repair_branch_log.append(
                record_branch_cost(repair, tokens_used=input_tokens + output_tokens)
            )
            if repair.max_extra_turns <= 0:
                break

            repair_rounds_left -= 1
            ledger.repair_rounds += 1
            if on_turn is not None:
                await on_turn(-1, f"验证失败，同轨迹修正（{repair.strategy}）…")
            # Append structured gaps as a new user turn — no recursive self.run().
            gap_blob = json.dumps(
                [
                    {
                        "code": g.code,
                        "detail": g.detail,
                        "blocked_by": g.blocked_by,
                        "status": g.status,
                    }
                    for g in verification.gaps[:12]
                ],
                ensure_ascii=False,
                indent=2,
            )
            conversation.append(
                ChatMessage(
                    role="user",
                    content=(
                        "Verification failed. Repair with minimal edits "
                        f"(strategy={repair.strategy}). Gaps:\n{gap_blob}\n"
                        "Use edit_file/grep/glob when possible. Call submit when ready."
                    ),
                )
            )
            self._toolkit.submitted = False
            prior_gaps = list(verification.gaps)
            assembler = ContextAssembler(
                plan=plan,
                toolkit=self._toolkit,
                regent_md=self._regent_md,
                gaps=prior_gaps,
            )
            continue

        else:
            ledger.wall_seconds = time.monotonic() - started
            self._raise_budget_exhausted(
                f"max_turns={max_turns} exhausted",
                transcript=transcript,
                ledger=ledger,
                compact_events=compact_events,
            )

        ledger.wall_seconds = time.monotonic() - started
        report = self._toolkit.snapshot_files_report()
        files = report.files
        ledger.snapshot_file_count = len(files)
        ledger.snapshot_truncated = report.truncated
        ledger.transcript_turns = len(transcript)

        if not self._toolkit.submitted:
            raise ArtifactIncompleteError(
                "agent stopped without submit; refusing ReleaseCandidate"
            )
        if not files:
            raise ArtifactIncompleteError("agent submitted but produced no files")

        if verify and verification is None:
            acceptance = dict(plan.get("acceptance_contract") or {})
            verification = await VerificationAgent(
                self._toolkit, runtime_profile=profile
            ).verify(
                acceptance_contract=acceptance,
                success_criteria=acceptance.get("success_criteria"),
                run_smoke=run_smoke,
                runtime_profile=profile,
            )

        result = AgentRunResult(
            files=files,
            transcript=transcript,
            verification=verification,
            model_ref=model_ref,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            turns=len({t.turn for t in transcript}),
            compact_events=compact_events,
            ledger=ledger,
            submitted=True,
            repair_branch_log=repair_branch_log,
            skill_refs=skill_refs,
        )
        if _parent_ledger is not None:
            _parent_ledger.merge(ledger)
        (self._toolkit.root / ".regent_run_ledger.json").write_text(
            json.dumps(ledger.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result
