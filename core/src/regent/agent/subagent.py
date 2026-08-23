"""Isolated sub-agent contexts for LARGE milestone work (P1-3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from regent.agent.agent_runner import AgentRunResult, AgentRunner, ChatProvider
from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import AgentBudget, VerificationGap
from regent.infrastructure.sandbox import build_agent_sandbox


@dataclass(frozen=True, slots=True)
class SubagentBrief:
    milestone_key: str
    milestone_title: str
    milestone_ordinal: int
    acceptance: dict[str, Any] = field(default_factory=dict)
    planned_paths: list[str] = field(default_factory=list)
    plan_item_key: str | None = None


@dataclass
class SubagentResult:
    brief: SubagentBrief
    summary: dict[str, Any]
    files: dict[str, str] = field(default_factory=dict)
    verification_passed: bool | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0


class SubagentRunner:
    """Spawn an isolated agent context per milestone; return structured summary only.

    Sidechain transcript stays inside the child sandbox — parent only receives
    the summary + file snapshot, never the child conversation history.
    """

    def __init__(
        self,
        provider: ChatProvider,
        *,
        workspace_root: Path,
        budget: AgentBudget | None = None,
        regent_md: str = "",
        goal_id: str | None = None,
        execution_plans: Any | None = None,
        run_id: Any | None = None,
        parent_depth: int = 0,
        max_subagent_depth: int = 1,
        budget_ledger: Any | None = None,
        model_max_output_tokens: int = 8192,
        model_input_cost_per_million: float = 0.0,
        model_output_cost_per_million: float = 0.0,
        price_book_version: str = "model-price-book-v1",
    ) -> None:
        self._provider = provider
        self._workspace_root = workspace_root.resolve()
        self._budget = budget or AgentBudget(max_turns=30, max_tokens=120_000)
        self._regent_md = regent_md
        self._goal_id = goal_id
        self._execution_plans = execution_plans
        self._run_id = run_id
        self._parent_depth = int(parent_depth)
        self._max_subagent_depth = int(max_subagent_depth)
        self._budget_ledger = budget_ledger
        self._model_max_output_tokens = model_max_output_tokens
        self._model_input_cost_per_million = model_input_cost_per_million
        self._model_output_cost_per_million = model_output_cost_per_million
        self._price_book_version = price_book_version

    async def _writeback_plan_item(self, brief: SubagentBrief, *, status: str) -> None:
        if self._execution_plans is None or not self._goal_id or not brief.plan_item_key:
            return
        import uuid

        from regent.application.execution_plan import UpsertPlanItem
        from regent.domain.errors import DomainError, ErrorCode

        goal_uuid = uuid.UUID(str(self._goal_id))
        run_uuid = None
        if self._run_id is not None:
            try:
                run_uuid = uuid.UUID(str(self._run_id))
            except (TypeError, ValueError):
                run_uuid = self._run_id if hasattr(self._run_id, "hex") else None
        run_scope = str(run_uuid) if run_uuid is not None else ""
        item_key = (
            f"{run_scope}:{brief.plan_item_key}" if run_scope else str(brief.plan_item_key)
        )
        try:
            await self._execution_plans.upsert_items(
                [
                    UpsertPlanItem(
                        goal_id=goal_uuid,
                        run_id=run_uuid,
                        item_key=item_key,
                        content=brief.milestone_title or brief.plan_item_key,
                        status=status,
                        owner_agent_id=f"subagent-{brief.milestone_ordinal}-{brief.milestone_key}",
                        metadata={"plan_item_key": brief.plan_item_key},
                    )
                ]
            )
        except DomainError as exc:
            if exc.code != ErrorCode.INVALID_STATE:
                raise

    async def run_milestone(
        self,
        *,
        goal_anchor_text: str,
        success_criteria: dict[str, Any] | None,
        brief: SubagentBrief,
        prior_gaps: list[VerificationGap] | None = None,
        verify: bool = True,
    ) -> SubagentResult:
        from regent.application.subagent_runtime import upsert_subagent_runtime

        sandbox = (
            self._workspace_root
            / "subagents"
            / f"{brief.milestone_ordinal}-{brief.milestone_key}"
        )
        sandbox.mkdir(parents=True, exist_ok=True)
        toolkit = WorkspaceToolkit(sandbox, command_sandbox=build_agent_sandbox())
        plan = {
            "goal_anchor_text": goal_anchor_text,
            "planned_paths": list(brief.planned_paths),
            "acceptance_contract": {
                **dict(brief.acceptance),
                "success_criteria": success_criteria or brief.acceptance.get("success_criteria"),
                "milestone_key": brief.milestone_key,
                "milestone_title": brief.milestone_title,
                "milestone_ordinal": brief.milestone_ordinal,
                "acceptance_scope": "milestone_subset",
                "full_goal_success_criteria": success_criteria,
            },
            "hypothesis_decision_id": brief.acceptance.get("hypothesis_decision_id"),
        }
        # Ensure hypothesis id exists for artifact scope if needed later.
        if not plan["hypothesis_decision_id"]:
            import uuid

            plan["hypothesis_decision_id"] = str(uuid.uuid4())

        agent_id = f"subagent-{brief.milestone_ordinal}-{brief.milestone_key}"
        upsert_subagent_runtime(
            self._goal_id,
            agent_id=agent_id,
            name=brief.milestone_title or brief.milestone_key,
            activity="active",
            detail=f"里程碑 {brief.milestone_ordinal}",
            milestone_key=brief.milestone_key,
        )
        runner = AgentRunner(
            self._provider,
            toolkit,
            budget=self._budget,
            regent_md=self._regent_md,
            execution_mode="act",
            producer_ref=agent_id,
            goal_id=uuid.UUID(self._goal_id) if self._goal_id else None,
            run_id=self._run_id,
            execution_plans=self._execution_plans,
            subagent_depth=self._parent_depth + 1,
            max_subagent_depth=self._max_subagent_depth,
            budget_ledger=self._budget_ledger,
            model_max_output_tokens=self._model_max_output_tokens,
            model_input_cost_per_million=self._model_input_cost_per_million,
            model_output_cost_per_million=self._model_output_cost_per_million,
            price_book_version=self._price_book_version,
            # Subagent inherits parent plan items for Step 0; skip re-approve.
        )
        # Mark as already approved / trivial so child does not ASK plan_approve again.
        plan["work_plan_approved"] = True
        plan["skip_plan_approve"] = True
        plan["work_plan_trivial"] = True
        if brief.plan_item_key:
            plan["work_plan_items"] = [
                {
                    "id": brief.plan_item_key,
                    "content": brief.milestone_title,
                    "status": "in_progress",
                    "owner_agent_id": agent_id,
                }
            ]
        await self._writeback_plan_item(brief, status="in_progress")

        async def _on_event(event: dict[str, Any]) -> None:
            if event.get("type") != "tool_call":
                return
            upsert_subagent_runtime(
                self._goal_id,
                agent_id=agent_id,
                name=brief.milestone_title or brief.milestone_key,
                activity="active",
                detail=str(event.get("summary") or event.get("tool") or ""),
                tool=str(event.get("tool") or "") or None,
                milestone_key=brief.milestone_key,
            )

        try:
            result: AgentRunResult = await runner.run(
                plan,
                prior_gaps=prior_gaps,
                verify=verify,
                on_event=_on_event,
            )
        except Exception:
            upsert_subagent_runtime(
                self._goal_id,
                agent_id=agent_id,
                name=brief.milestone_title or brief.milestone_key,
                activity="failed",
                detail="子代理执行失败",
                milestone_key=brief.milestone_key,
            )
            await self._writeback_plan_item(brief, status="failed")
            raise

        summary = {
            "milestone_key": brief.milestone_key,
            "milestone_ordinal": brief.milestone_ordinal,
            "title": brief.milestone_title,
            "plan_item_key": brief.plan_item_key,
            "files": sorted(result.files.keys()),
            "verification": (
                {
                    "verdict": result.verification.verdict,
                    "summary": result.verification.summary,
                    "gaps": [
                        {"code": g.code, "detail": g.detail}
                        for g in (result.verification.gaps if result.verification else [])
                    ],
                }
                if result.verification
                else None
            ),
            "turns": result.turns,
            "tokens": {
                "input": result.input_tokens,
                "output": result.output_tokens,
            },
            # Explicitly omit conversation / transcript from parent context.
            "sidechain_omitted": True,
        }
        passed = None if result.verification is None else result.verification.passed
        await self._writeback_plan_item(
            brief, status="completed" if passed is not False else "failed"
        )
        upsert_subagent_runtime(
            self._goal_id,
            agent_id=agent_id,
            name=brief.milestone_title or brief.milestone_key,
            activity="done",
            detail=f"完成 {result.turns} 轮",
            milestone_key=brief.milestone_key,
        )
        return SubagentResult(
            brief=brief,
            summary=summary,
            files=result.files,
            verification_passed=passed,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            turns=result.turns,
        )

    async def run_large_milestones(
        self,
        *,
        goal_anchor_text: str,
        success_criteria: dict[str, Any] | None,
        briefs: list[SubagentBrief],
        verify: bool = True,
    ) -> list[SubagentResult]:
        """Run milestone subagents sequentially (isolation without shared history).

        Parallelism can be added later; sequential keeps sandbox resource use bounded.
        """
        results: list[SubagentResult] = []
        for brief in briefs:
            results.append(
                await self.run_milestone(
                    goal_anchor_text=goal_anchor_text,
                    success_criteria=success_criteria,
                    brief=brief,
                    verify=verify,
                )
            )
        return results
