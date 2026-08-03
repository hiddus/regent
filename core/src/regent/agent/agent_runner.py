"""Multi-turn agent runner with budgeted tool loop + single-trajectory repair (M3-1)."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from regent.agent.compact import ContextCompactor, HeuristicSummarizer, micro_compact
from regent.agent.context_assembler import ContextAssembler
from regent.agent.primary_failure import classify_finish_reason
from regent.agent.repair_policy import (
    IDENTICAL_GAP_STOP_AFTER,
    gap_fingerprint,
    plan_repair,
    record_branch_cost,
)
from regent.agent.run_ledger import AgentRunLedger
from regent.agent.runtime_profile_v1 import RuntimeProfileV1, parse_runtime_profile_v1
from regent.agent.skills import select_skills_for_goal
from regent.agent.tools import TOOL_SPECS, WorkspaceToolkit
from regent.agent.types import (
    AgentBudget,
    ArtifactIncompleteError,
    BudgetExhaustedError,
    ChatMessage,
    PlanApproveRequiredError,
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


async def _emit(
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None,
    payload: dict[str, Any],
) -> None:
    if on_event is None:
        return
    await on_event(payload)


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
        execution_mode: str = "ask",
        permission_always_tools: set[str] | frozenset[str] | None = None,
        subagent_depth: int = 0,
        max_subagent_depth: int = 1,
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
        self._execution_mode = "act" if str(execution_mode).lower() == "act" else "ask"
        self._permission_always = set(permission_always_tools or ())
        self._subagent_depth = int(subagent_depth)
        self._max_subagent_depth = int(max_subagent_depth)
        self._compactor = ContextCompactor(
            toolkit=toolkit,
            summarizer=HeuristicSummarizer(),
            context_window_tokens=context_window_tokens,
        )

    def _raise_if_aborted(self, plan: dict[str, Any]) -> None:
        from regent.application.agent_control import UserAbortError, is_abort_requested

        meta = dict(plan.get("goal_metadata") or {})
        if is_abort_requested(
            str(self._goal_id) if self._goal_id else plan.get("goal_id"),
            meta,
        ):
            raise UserAbortError("user_abort")

    def _maybe_require_tool_permission(
        self, plan: dict[str, Any], tool_name: str, arguments: dict[str, Any]
    ) -> None:
        from regent.application.agent_control import (
            ToolPermissionRequiredError,
            permission_ask_envelope,
            tool_needs_permission,
        )

        # One-shot allow from prior human answer on this lease.
        once = set(plan.get("permission_allow_once_tools") or ())
        always = set(self._permission_always) | set(
            plan.get("permission_always_tools") or ()
        )
        if tool_name in once:
            once.discard(tool_name)
            plan["permission_allow_once_tools"] = sorted(once)
            return
        if not tool_needs_permission(
            tool_name,
            execution_mode=self._execution_mode,  # type: ignore[arg-type]
            always_tools=always,
        ):
            return
        preview = _preview(arguments, 180)
        raise ToolPermissionRequiredError(
            tool_name,
            args_preview=preview,
            envelope=permission_ask_envelope(
                tool_name=tool_name,
                args_preview=preview,
                execution_mode=self._execution_mode,  # type: ignore[arg-type]
            ),
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

    async def _seed_work_plan(self, plan: dict[str, Any]) -> None:
        """Restore todos from plan checkpoint or durable ExecutionPlan items."""
        if self._toolkit.todos:
            return
        seeded = list(plan.get("work_plan_items") or plan.get("todos") or [])
        if not seeded and self._execution_plans is not None and self._goal_id is not None:
            try:
                views = await self._execution_plans.list_items(self._goal_id)
                seeded = [
                    {
                        "id": v.item_key.rsplit(":", 1)[-1],
                        "content": v.content,
                        "status": v.status,
                        **({"owner_agent_id": v.owner_agent_id} if v.owner_agent_id else {}),
                        **({"dependencies": list(v.dependencies)} if v.dependencies else {}),
                    }
                    for v in views
                    if v.status not in {"cancelled"}
                ]
            except Exception:
                seeded = []
        if seeded:
            from regent.application.work_plan import normalize_single_in_progress

            self._toolkit.todos = normalize_single_in_progress(
                [
                    {
                        "id": str(item.get("id") or item.get("item_key") or ""),
                        "content": str(item.get("content") or ""),
                        "status": str(item.get("status") or "pending"),
                        **(
                            {"owner_agent_id": str(item["owner_agent_id"])}
                            if item.get("owner_agent_id")
                            else {}
                        ),
                    }
                    for item in seeded
                    if isinstance(item, dict) and (item.get("id") or item.get("item_key"))
                ]
            )

    def _step0_blocks_write(self, plan: dict[str, Any], tool_name: str) -> str | None:
        from regent.config import get_settings
        from regent.application.work_plan import (
            WRITE_TOOLS,
            has_active_plan_items,
            is_trivial_work,
            step0_rejection_message,
        )

        if tool_name not in WRITE_TOOLS:
            return None
        if not bool(getattr(get_settings(), "agent_work_plan_required", True)):
            return None
        if is_trivial_work(plan):
            return None
        if has_active_plan_items(self._toolkit.todos):
            return None
        return step0_rejection_message()

    async def _persist_work_plan(self) -> None:
        if self._execution_plans is None or self._goal_id is None:
            return
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
                        owner_agent_id=(
                            str(item["owner_agent_id"])
                            if item.get("owner_agent_id")
                            else None
                        ),
                        dependencies=list(item.get("dependencies") or ()),
                        metadata={"session_work_plan": True},
                    )
                    for item in self._toolkit.todos
                    if item.get("id")
                ]
            )
        except DomainError as exc:
            if exc.code != ErrorCode.INVALID_STATE:
                raise

    def _should_ask_plan_approve(self, plan: dict[str, Any]) -> bool:
        from regent.config import get_settings
        from regent.application.work_plan import is_trivial_work

        if not bool(getattr(get_settings(), "agent_plan_approve_on_first", True)):
            return False
        if plan.get("work_plan_approved") or plan.get("skip_plan_approve"):
            return False
        if is_trivial_work(plan):
            return False
        # Resume / same Session small continue: already has approved stamp or prior items done.
        if plan.get("authorized_session_resume") and plan.get("work_plan_seen"):
            return False
        return True

    async def _run_delegate_plan_item(
        self,
        *,
        plan: dict[str, Any],
        item_id: str,
        acceptance_notes: str,
        prior_gaps: list[VerificationGap] | None,
    ) -> str:
        from regent.agent.subagent import SubagentBrief, SubagentRunner

        match = next(
            (t for t in self._toolkit.todos if str(t.get("id") or "") == item_id),
            None,
        )
        if match is None:
            return json.dumps({"ok": False, "error": f"plan item not found: {item_id}"})
        if self._subagent_depth >= self._max_subagent_depth:
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        f"subagent depth exceeded ({self._subagent_depth} >= "
                        f"{self._max_subagent_depth})"
                    ),
                }
            )
        self._raise_if_aborted(plan)
        owner = f"subagent-{item_id}"
        match["status"] = "in_progress"
        match["owner_agent_id"] = owner
        await self._persist_work_plan()
        brief = SubagentBrief(
            milestone_key=str(item_id),
            milestone_title=str(match.get("content") or item_id)[:120],
            milestone_ordinal=max(
                1,
                next(
                    (
                        i + 1
                        for i, t in enumerate(self._toolkit.todos)
                        if str(t.get("id") or "") == item_id
                    ),
                    1,
                ),
            ),
            acceptance={
                "acceptance_notes": acceptance_notes,
                "plan_item_key": item_id,
            },
            planned_paths=list(plan.get("planned_paths") or []),
            plan_item_key=str(item_id),
        )
        runner = SubagentRunner(
            self._provider,
            workspace_root=self._toolkit.root,
            budget=AgentBudget(
                max_turns=min(20, self._budget.max_turns),
                max_tokens=min(80_000, self._budget.max_tokens),
                max_wall_seconds=min(600, self._budget.max_wall_seconds),
            ),
            regent_md=self._regent_md,
            goal_id=str(self._goal_id) if self._goal_id else None,
            execution_plans=self._execution_plans,
            run_id=self._run_id,
            parent_depth=self._subagent_depth,
            max_subagent_depth=self._max_subagent_depth,
        )
        result = await runner.run_milestone(
            goal_anchor_text=str(plan.get("goal_anchor_text") or ""),
            success_criteria=(plan.get("acceptance_contract") or {}).get("success_criteria"),
            brief=brief,
            prior_gaps=prior_gaps,
            verify=True,
        )
        passed = result.verification_passed
        for t in self._toolkit.todos:
            if str(t.get("id") or "") == item_id:
                t["status"] = "completed" if passed is not False else "pending"
                t["owner_agent_id"] = owner
        await self._persist_work_plan()
        return json.dumps(
            {
                "ok": True,
                "id": item_id,
                "verification_passed": passed,
                "turns": result.turns,
                "summary": result.summary,
            },
            ensure_ascii=False,
            default=str,
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
        await self._seed_work_plan(plan)
        # I-C: seed conversation from Session checkpoint / prior transcript
        # (same AgentRunner — not a third loop).
        conversation: list[ChatMessage] = _seed_session_conversation(
            plan, toolkit_root=self._toolkit.root
        )
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
        # P0-4 anti-loop state (single trajectory — never recursive self.run).
        repair_phase_turns_left: int | None = None
        chat_temperature = 0.0
        last_gap_fingerprint: str | None = None

        while turn < max_turns:
            self._raise_if_aborted(plan)
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
            await _emit(
                on_event,
                {
                    "type": "turn_start",
                    "turn": turn,
                    "summary": f"第 {turn + 1}/{max_turns} 轮",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_tokens": ledger.cached_tokens if hasattr(ledger, "cached_tokens") else None,
                },
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
                await _emit(
                    on_event,
                    {
                        "type": "compaction",
                        "turn": turn,
                        "summary": "上下文已压缩",
                        "detail": f"autoCompact chars={len(auto.summary)}",
                    },
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
                    temperature=chat_temperature,
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
            # W4-P0: calibrate local token estimate from real prompt_tokens.
            if turn_in > 0:
                from regent.agent.compact import estimate_tokens as _est_tok

                est = _est_tok(messages)
                if est > 0:
                    self._compactor.observe_provider_prompt_tokens(
                        estimated=est, actual_prompt_tokens=turn_in
                    )
            await _emit(
                on_event,
                {
                    "type": "budget_tick",
                    "turn": turn,
                    "summary": f"本轮 tokens in={turn_in} out={turn_out}",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_tokens": turn_cached_i,
                },
            )

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
            if assistant.content:
                await _emit(
                    on_event,
                    {
                        "type": "assistant_text",
                        "turn": turn,
                        "summary": _preview(assistant.content, 160),
                        "detail": _preview(assistant.content, 400),
                    },
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
                await _emit(
                    on_event,
                    {"type": "turn_end", "turn": turn, "summary": "本轮结束（无工具调用）"},
                )
                break

            submitted_this_turn = False
            for call in assistant.tool_calls:
                ledger.add_tool_invocation(1)
                self._raise_if_aborted(plan)
                step0_block = self._step0_blocks_write(plan, call.name)
                if step0_block:
                    result_text = f"ERROR: WorkPlanRequired: {step0_block}"
                elif call.name == "delegate_plan_item":
                    if self._subagent_depth >= self._max_subagent_depth:
                        result_text = (
                            "ERROR: SubagentDepthExceeded: nested delegate_plan_item "
                            f"forbidden (depth={self._subagent_depth}, "
                            f"max={self._max_subagent_depth})"
                        )
                    else:
                        # Toolkit validates; runner executes isolated subagent.
                        probe = await self._toolkit.execute(call)
                        if probe.startswith("ERROR:"):
                            result_text = probe
                        else:
                            self._maybe_require_tool_permission(
                                plan, call.name, dict(call.arguments or {})
                            )
                            result_text = await self._run_delegate_plan_item(
                                plan=plan,
                                item_id=str(call.arguments.get("id") or ""),
                                acceptance_notes=str(
                                    call.arguments.get("acceptance_notes") or ""
                                ),
                                prior_gaps=prior_gaps,
                            )
                else:
                    if not str(call.name).startswith("ask_"):
                        self._maybe_require_tool_permission(
                            plan, call.name, dict(call.arguments or {})
                        )
                    result_text = await self._toolkit.execute(call)
                message_result = result_text
                if call.name == "submit":
                    submitted_this_turn = True
                    await _emit(
                        on_event,
                        {
                            "type": "submit",
                            "turn": turn,
                            "tool": "submit",
                            "summary": "已提交产物",
                            "args_preview": _preview(call.arguments),
                            "result_preview": _preview(result_text),
                        },
                    )
                await _emit(
                    on_event,
                    {
                        "type": "tool_call",
                        "turn": turn,
                        "tool": call.name,
                        "summary": f"执行工具 {call.name}",
                        "args_preview": _preview(call.arguments),
                        "result_preview": _preview(result_text),
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    },
                )
                if call.name in {"todo_write", "plan_update"}:
                    await _emit(
                        on_event,
                        {
                            "type": "plan_updated",
                            "turn": turn,
                            "tool": call.name,
                            "summary": f"计划已更新（{len(self._toolkit.todos)} 项）",
                            "detail": _preview(self._toolkit.todos, 400),
                        },
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
                if call.name in {"todo_write", "plan_update", "delegate_plan_item"}:
                    await self._persist_work_plan()
                if (
                    call.name == "todo_write"
                    and not result_text.startswith("ERROR:")
                    and self._should_ask_plan_approve(plan)
                    and len(self._toolkit.todos) >= 1
                ):
                    raise PlanApproveRequiredError(
                        "PLAN_APPROVE_REQUIRED: work plan awaiting human approve",
                        items=list(self._toolkit.todos),
                    )
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

            # Repair-phase turn budget: each model turn after a gap message counts.
            if (
                repair_phase_turns_left is not None
                and not submitted_this_turn
                and not self._toolkit.submitted
            ):
                repair_phase_turns_left -= 1
                if repair_phase_turns_left <= 0:
                    ledger.notes.append("repair_phase_turns_exhausted_without_submit")
                    self._raise_budget_exhausted(
                        "repair phase max_extra_turns exhausted without submit",
                        transcript=transcript,
                        ledger=ledger,
                        compact_events=compact_events,
                    )

            if not submitted_this_turn and not self._toolkit.submitted:
                continue

            # Explicit submit → verify on same trajectory (M1-3 / M3-1).
            repair_phase_turns_left = None
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
                chat_temperature = 0.0
                break

            if repair_rounds_left <= 0 or not verification.gaps:
                ledger.notes.append("repair_rounds_exhausted_or_no_gaps")
                break

            primary = verification.gaps[0].code
            fingerprint = gap_fingerprint([g.code for g in verification.gaps])
            gap_repeat[primary] = gap_repeat.get(primary, 0) + 1
            # Identical gap set after a prior repair attempt → thrashing; stop.
            if (
                last_gap_fingerprint is not None
                and fingerprint == last_gap_fingerprint
                and gap_repeat[primary] >= IDENTICAL_GAP_STOP_AFTER
            ):
                ledger.notes.append(
                    f"identical_gap_fingerprint_stop:{fingerprint}:n={gap_repeat[primary]}"
                )
                ledger.primary_failure_code = primary
                break
            last_gap_fingerprint = fingerprint

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
                ledger.notes.append(f"repair_fail_closed:{repair.strategy}")
                break

            repair_rounds_left -= 1
            ledger.repair_rounds += 1
            repair_phase_turns_left = int(repair.max_extra_turns)
            chat_temperature = float(repair.temperature)
            if on_turn is not None:
                await on_turn(-1, f"验证失败，同轨迹修正（{repair.strategy}）…")
            await _emit(
                on_event,
                {
                    "type": "repair_phase_start",
                    "turn": turn,
                    "summary": f"进入修正（{repair.strategy}）",
                    "detail": ",".join(g.code for g in verification.gaps[:8]),
                },
            )
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
            branch_note = ""
            if repair.allow_candidate_branch:
                branch_note = (
                    " Candidate branch authorized once: if the prior repair path is stuck, "
                    "try one alternate minimal approach in the same workspace; keep diffs small."
                )
                ledger.notes.append("candidate_branch_authorized")
            conversation.append(
                ChatMessage(
                    role="user",
                    content=(
                        "Verification failed. Repair with minimal edits "
                        f"(strategy={repair.strategy}, temperature={chat_temperature}). "
                        f"Gaps:\n{gap_blob}\n"
                        "Use edit_file/grep/glob when possible. Call submit when ready."
                        f"{branch_note}"
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


def _seed_session_conversation(
    plan: dict[str, Any], *, toolkit_root: Path
) -> list[ChatMessage]:
    """Load prior Session turns so resume is not a cold start (I-C).

    Prefer explicit ``session_prior_messages`` on the plan; else hydrate a short
    tail from ``.regent_agent_transcript.json`` in the Session workspace.
    """
    acceptance = dict(plan.get("acceptance_contract") or {})
    seeded: list[ChatMessage] = []
    raw_prior = plan.get("session_prior_messages") or acceptance.get(
        "session_prior_messages"
    )
    if isinstance(raw_prior, list) and raw_prior:
        for item in raw_prior[-12:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            if role not in {"user", "assistant", "system", "tool"}:
                continue
            content = item.get("content")
            if content is None and role != "assistant":
                continue
            seeded.append(
                ChatMessage(
                    role=role,  # type: ignore[arg-type]
                    content=str(content) if content is not None else None,
                    tool_call_id=str(item["tool_call_id"])
                    if item.get("tool_call_id")
                    else None,
                    name=str(item["name"]) if item.get("name") else None,
                )
            )
    if not seeded:
        path = toolkit_root / ".regent_agent_transcript.json"
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = []
            if isinstance(payload, list):
                # Keep only user/assistant text turns (skip tool noise for resume).
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    role = str(item.get("role") or "")
                    if role not in {"user", "assistant"}:
                        continue
                    content = item.get("content")
                    if not content:
                        continue
                    seeded.append(
                        ChatMessage(role=role, content=str(content)[:4_000])  # type: ignore[arg-type]
                    )
                seeded = seeded[-8:]
    brief = str(
        plan.get("session_resume_brief") or acceptance.get("session_resume_brief") or ""
    ).strip()
    session_id = str(
        plan.get("project_agent_session_id")
        or acceptance.get("project_agent_session_id")
        or ""
    ).strip()
    if brief or session_id:
        note = brief or f"Continue ProjectAgentSession {session_id} in the same workspace."
        seeded.insert(
            0,
            ChatMessage(
                role="user",
                content=(
                    f"[Session resume]\n{note}\n"
                    "Reuse existing files; fix gaps with tools. Do not scaffold from scratch."
                ),
            ),
        )
    return seeded
