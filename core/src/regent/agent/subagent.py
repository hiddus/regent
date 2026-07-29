"""Isolated sub-agent contexts for LARGE milestone work (P1-3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from regent.agent.agent_runner import AgentRunResult, AgentRunner, ChatProvider
from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import AgentBudget, VerificationGap


@dataclass(frozen=True, slots=True)
class SubagentBrief:
    milestone_key: str
    milestone_title: str
    milestone_ordinal: int
    acceptance: dict[str, Any] = field(default_factory=dict)
    planned_paths: list[str] = field(default_factory=list)


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
    ) -> None:
        self._provider = provider
        self._workspace_root = workspace_root.resolve()
        self._budget = budget or AgentBudget(max_turns=30, max_tokens=120_000)
        self._regent_md = regent_md

    async def run_milestone(
        self,
        *,
        goal_anchor_text: str,
        success_criteria: dict[str, Any] | None,
        brief: SubagentBrief,
        prior_gaps: list[VerificationGap] | None = None,
        verify: bool = True,
    ) -> SubagentResult:
        sandbox = (
            self._workspace_root
            / "subagents"
            / f"{brief.milestone_ordinal}-{brief.milestone_key}"
        )
        sandbox.mkdir(parents=True, exist_ok=True)
        toolkit = WorkspaceToolkit(sandbox)
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

        runner = AgentRunner(
            self._provider,
            toolkit,
            budget=self._budget,
            regent_md=self._regent_md,
        )
        result: AgentRunResult = await runner.run(
            plan,
            prior_gaps=prior_gaps,
            verify=verify,
        )
        summary = {
            "milestone_key": brief.milestone_key,
            "milestone_ordinal": brief.milestone_ordinal,
            "title": brief.milestone_title,
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
        return SubagentResult(
            brief=brief,
            summary=summary,
            files=result.files,
            verification_passed=(
                None if result.verification is None else result.verification.passed
            ),
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
