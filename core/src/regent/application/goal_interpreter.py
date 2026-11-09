from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from regent.model import ModelProvider, StructuredModelResponse


class Unknown(BaseModel):
    question: str = Field(min_length=1)
    blocking: bool = False


class GoalInterpretation(BaseModel):
    objective: str | None = None
    explicit_constraints: dict[str, str | int | float | bool] = Field(default_factory=dict)
    system_inferences: dict[str, str | int | float | bool] = Field(default_factory=dict)
    unknowns: list[Unknown] = Field(default_factory=list)
    success_criteria: dict[str, str | int | float | bool] = Field(default_factory=dict)


_SYSTEM_PROMPT = """You are Regent Goal Interpreter. Return one JSON object matching the supplied
schema. Never invent an explicit constraint. Put assumptions under system_inferences and missing
information under unknowns. Success criteria must be externally verifiable. Do not propose or
execute tools, permissions, credentials, or side effects."""


class GoalInterpreter:
    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    async def interpret(self, original_input: str) -> StructuredModelResponse[GoalInterpretation]:
        if not original_input.strip():
            raise ValueError("goal input must not be empty")
        return await self._provider.generate_structured(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=original_input,
            response_model=GoalInterpretation,
        )

    # ------------------------------------------------------------------
    # V3 Goal Decomposition (sub-goals + dependency graph)
    # ------------------------------------------------------------------

    async def decompose(
        self, interpretation: GoalInterpretation
    ) -> list[SubGoal]:
        """Decompose a GoalInterpretation into ordered sub-goals.

        Uses the LLM to break the objective into smaller, independently
        verifiable sub-goals with dependency information.  Falls back to
        a single sub-goal wrapping the original objective.
        """
        objective = interpretation.objective or "(unnamed goal)"
        prompt = (
            f"Break the following goal into 2-5 ordered sub-goals. "
            f"Return a JSON array of objects with keys: "
            f"id (short slug), label, depends_on (array of id strings).\n\n"
            f"Goal: {objective}\n"
            f"Constraints: {interpretation.explicit_constraints}\n"
            f"Success criteria: {interpretation.success_criteria}"
        )
        try:
            resp = await self._provider.generate_structured(
                system_prompt=(
                    "You are a goal decomposition engine. Return a JSON array "
                    "of sub-goal objects. Each sub-goal must be independently "
                    "verifiable. Keep depends_on minimal."
                ),
                user_prompt=prompt,
                response_model=_SubGoalList,
            )
            sub_goals = [
                SubGoal(
                    id=s.id, label=s.label, depends_on=list(s.depends_on),
                    acceptance_criteria={},
                )
                for s in resp.data.items
            ]
            if sub_goals:
                return sub_goals
        except Exception:
            pass  # fall through to single sub-goal

        return [
            SubGoal(
                id="root",
                label=objective,
                depends_on=[],
                acceptance_criteria=dict(interpretation.success_criteria),
            )
        ]

    # ------------------------------------------------------------------
    # P1-B: SubGoal -> Work item creation
    # ------------------------------------------------------------------

    @staticmethod
    def create_work_items(
        sub_goals: list[SubGoal],
        *,
        goal_id: Any,
        correlation_id: Any,
    ) -> list[dict[str, Any]]:
        """Map SubGoal list to WorkModel creation commands.

        Returns a list of dicts suitable for creating WorkModel instances.
        Each work item is linked to its SubGoal via ``sub_goal_id`` and
        dependency edges are mapped from SubGoal ``depends_on`` to
        ``depends_on_work_ids`` (using the sub-goal id slug as placeholder
        until real work IDs are assigned).
        """
        # Build slug -> index mapping for dependency resolution
        slug_to_idx: dict[str, int] = {}
        items: list[dict[str, Any]] = []
        for idx, sg in enumerate(sub_goals):
            slug_to_idx[sg.id] = idx

        for idx, sg in enumerate(sub_goals):
            # Resolve depends_on slugs to sibling indices
            dep_work_ids: list[str] = []
            for dep_slug in sg.depends_on:
                if dep_slug in slug_to_idx:
                    dep_work_ids.append(f"__pending__:{sub_goals[slug_to_idx[dep_slug]].id}")

            items.append({
                "purpose": f"sub-goal:{sg.id}: {sg.label}",
                "sub_goal_id": sg.id,
                "acceptance_criteria": dict(sg.acceptance_criteria),
                "dependency_ids": list(sg.depends_on),
                "depends_on_work_ids": dep_work_ids,
                "input_refs": [],
                "priority": idx,
                "budget": {},
                "status": "PLANNED",
                "version": 0,
                "goal_id": goal_id,
                "correlation_id": correlation_id,
                "metadata_json": {
                    "sub_goal_label": sg.label,
                    "sub_goal_deps": list(sg.depends_on),
                },
            })
        return items


class _SubGoalItem(BaseModel):
    id: str
    label: str
    depends_on: list[str] = Field(default_factory=list)


class _SubGoalList(BaseModel):
    items: list[_SubGoalItem]


@dataclass(frozen=True, slots=True)
class SubGoal:
    """A decomposed sub-goal with dependency information."""

    id: str
    label: str
    depends_on: list[str] = field(default_factory=list)
    acceptance_criteria: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# KPI Extractor
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KPI:
    """A key performance indicator extracted from a goal."""

    name: str
    metric: str  # e.g. "count", "percentage", "latency_ms"
    target: float
    direction: str  # "higher_is_better" | "lower_is_better"


class KPIExtractor:
    """Extract verifiable success metrics from a GoalInterpretation."""

    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    async def extract(self, interpretation: GoalInterpretation) -> list[KPI]:
        """Extract KPIs from the goal interpretation's success criteria."""
        criteria = interpretation.success_criteria
        if not criteria:
            return []

        # Direct extraction from structured criteria
        kpis: list[KPI] = []
        for key, value in criteria.items():
            if isinstance(value, (int, float)):
                kpis.append(KPI(
                    name=key, metric="numeric", target=float(value),
                    direction="higher_is_better",
                ))
            elif isinstance(value, bool):
                kpis.append(KPI(
                    name=key, metric="boolean", target=1.0 if value else 0.0,
                    direction="higher_is_better",
                ))
        return kpis
