"""Repair strategy router by primary failure code (M3-4).

No default temperature ladder. Branches are budget-bounded and recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from regent.agent.primary_failure import PrimaryFailureCode, normalize_primary_failure_code


@dataclass(frozen=True, slots=True)
class RepairPlan:
    strategy: str
    max_extra_turns: int
    allow_candidate_branch: bool
    notes: str
    temperature: float = 0.0


def plan_repair(
    failure_code: str | PrimaryFailureCode | None,
    *,
    repeat_count: int = 1,
    remaining_token_budget: int = 50_000,
) -> RepairPlan:
    code = (
        failure_code
        if isinstance(failure_code, PrimaryFailureCode)
        else normalize_primary_failure_code(str(failure_code) if failure_code else None)
    )
    # Hard stop for budget / truncation — save diagnostics, don't thrash.
    if code in {
        PrimaryFailureCode.BUDGET_EXHAUSTED,
        PrimaryFailureCode.MODEL_TRUNCATED,
        PrimaryFailureCode.TOOL_CALL_INVALID,
    }:
        return RepairPlan(
            strategy="fail_closed",
            max_extra_turns=0,
            allow_candidate_branch=False,
            notes=f"{code}: do not repair-loop; fix provider/tooling first",
        )
    if code in {
        PrimaryFailureCode.STATIC_FAILED,
        PrimaryFailureCode.ARTIFACT_INCOMPLETE,
    }:
        return RepairPlan(
            strategy="targeted_edit",
            max_extra_turns=6,
            allow_candidate_branch=False,
            notes="edit scaffolds / forbidden patterns via replace",
        )
    if code in {
        PrimaryFailureCode.TEST_FAILED,
        PrimaryFailureCode.START_FAILED,
        PrimaryFailureCode.SMOKE_FAILED,
    }:
        branch = repeat_count >= 2 and remaining_token_budget > 20_000
        return RepairPlan(
            strategy="verify_feedback",
            max_extra_turns=8,
            allow_candidate_branch=branch,
            notes="feed structured gaps; optional single candidate branch on repeat",
        )
    if code is PrimaryFailureCode.PREVIEW_FAILED:
        return RepairPlan(
            strategy="preview_align",
            max_extra_turns=4,
            allow_candidate_branch=False,
            notes="align preview type with Runtime Profile",
        )
    return RepairPlan(
        strategy="generic_gap_turn",
        max_extra_turns=4,
        allow_candidate_branch=False,
        notes="append gaps as user turn; temperature stays 0",
    )


def record_branch_cost(plan: RepairPlan, *, tokens_used: int) -> dict[str, Any]:
    return {
        "strategy": plan.strategy,
        "temperature": plan.temperature,
        "allow_candidate_branch": plan.allow_candidate_branch,
        "tokens_used": tokens_used,
        "notes": plan.notes,
    }
