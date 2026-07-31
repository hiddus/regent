"""Read-only delivery review payload for Console artifact panel (CD-3.1)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.agent.transcript_store import AgentTranscriptStore
from regent.application.execution_plan import ExecutionPlanService
from regent.config import get_settings
from regent.infrastructure.models import GoalModel

_EMPTY_REVIEW: dict[str, Any] = {
    "plan": None,
    "transcript": None,
    "verification": None,
    "budget": None,
}

_GENERATION_RUN_ID_KEYS = (
    "last_generation_run_id",
    "generation_run_id",
)


def resolve_generation_run_id(metadata: dict[str, Any]) -> uuid.UUID | None:
    for key in _GENERATION_RUN_ID_KEYS:
        raw = metadata.get(key)
        if raw:
            try:
                return uuid.UUID(str(raw))
            except ValueError:
                continue
    halt = metadata.get("halt")
    if isinstance(halt, dict) and halt.get("generation_run_id"):
        try:
            return uuid.UUID(str(halt["generation_run_id"]))
        except ValueError:
            pass
    cap = metadata.get("capability_resolution")
    if isinstance(cap, dict) and cap.get("generation_run_id"):
        try:
            return uuid.UUID(str(cap["generation_run_id"]))
        except ValueError:
            pass
    return None


def resolve_verification(metadata: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("verification", "last_verification", "delivery_review", "delivery_verification"):
        val = metadata.get(key)
        if isinstance(val, dict) and val:
            return dict(val)
    return None


def assemble_delivery_review_payload(
    *,
    metadata: dict[str, Any],
    plan_items: list[dict[str, Any]] | None,
    transcript: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    settings = get_settings()
    budget_meta = (
        metadata.get("agent_budget") if isinstance(metadata.get("agent_budget"), dict) else {}
    )
    live = metadata.get("live_action") if isinstance(metadata.get("live_action"), dict) else {}

    turns = live.get("turn") if live.get("turn") is not None else budget_meta.get("turns")
    max_turns = budget_meta.get("max_turns") or settings.agent_max_turns
    input_tokens = budget_meta.get("input_tokens")
    output_tokens = budget_meta.get("output_tokens")
    max_tokens = budget_meta.get("max_tokens") or settings.agent_max_tokens

    budget: dict[str, Any] | None = None
    if any(v is not None for v in (turns, max_turns, input_tokens, output_tokens, max_tokens)):
        budget = {
            "turns": turns,
            "max_turns": max_turns,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "max_tokens": max_tokens,
        }

    return {
        "plan": {"items": plan_items} if plan_items else None,
        "transcript": transcript,
        "verification": resolve_verification(metadata),
        "budget": budget,
    }


class DeliveryReviewQueryService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_for_project(self, project_id: uuid.UUID) -> dict[str, Any]:
        async with self._sessions() as session:
            goal = await session.scalar(
                select(GoalModel)
                .where(GoalModel.app_project_id == project_id)
                .order_by(GoalModel.created_at.desc())
                .limit(1)
            )
        if goal is None:
            return dict(_EMPTY_REVIEW)

        metadata = dict(goal.metadata_json or {})
        items = await ExecutionPlanService(self._sessions).list_items(goal.id)
        plan_items = [item.as_dict() for item in items] if items else None

        transcript: list[dict[str, Any]] | None = None
        run_id = resolve_generation_run_id(metadata)
        if run_id is not None:
            rows = await AgentTranscriptStore(self._sessions).list_for_run(run_id)
            transcript = rows or None

        return assemble_delivery_review_payload(
            metadata=metadata,
            plan_items=plan_items,
            transcript=transcript,
        )
