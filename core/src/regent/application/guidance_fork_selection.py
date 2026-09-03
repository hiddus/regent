"""State transition for applying a user-selected guidance fork."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from regent.application.goal_readiness import (
    assess_goal_readiness,
    confirmation_gate_key,
    effective_feasibility_verdict,
)
from regent.application.p1_contracts import canonical_hash
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import GoalModel, GoalSpecModel


async def apply_fork_selection(
    session: AsyncSession,
    goal_id: uuid.UUID,
    *,
    chosen: dict[str, Any],
    actor: str,
    feasibility_verdict: str | None,
) -> tuple[GoalModel, dict[str, Any], GoalSpecModel | None]:
    goal = await session.get(GoalModel, goal_id, with_for_update=True)
    if goal is None:
        raise DomainError(ErrorCode.NOT_FOUND, "goal not found")
    metadata = dict(goal.metadata_json or {})
    metadata["needs_user_fork"] = False
    metadata["pending_fork_options"] = []
    metadata["selected_fork"] = {
        "id": str(chosen.get("id")),
        "label": str(chosen.get("label") or ""),
        "description": str(chosen.get("description") or ""),
        "actor": actor,
        "at": datetime.now(UTC).isoformat(),
    }
    metadata["goal_clarity_state"] = "FORK_RESOLVED"
    plan = dict(metadata.get("runtime_plan") or {})
    plan["needs_user_fork"] = False
    plan["selected_fork"] = metadata["selected_fork"]
    metadata["runtime_plan"] = plan
    if goal.status == "DRAFT":
        metadata["clarification_rounds"] = int(metadata.get("clarification_rounds") or 0) + 1

    latest_spec = await session.scalar(
        select(GoalSpecModel)
        .where(GoalSpecModel.goal_id == goal_id)
        .order_by(GoalSpecModel.version.desc())
        .with_for_update()
    )
    if latest_spec is not None:
        constraints = dict(latest_spec.explicit_constraints or {})
        constraints["selected_fork_id"] = str(chosen.get("id"))
        constraints["selected_fork_label"] = str(chosen.get("label") or "")
        inferences = dict(latest_spec.system_inferences or {})
        inferences["selected_fork"] = metadata["selected_fork"]
        spec_content = {
            "explicit_constraints": constraints,
            "system_inferences": inferences,
            "unknowns": list(latest_spec.unknowns or []),
            "success_criteria": dict(latest_spec.success_criteria or {}),
            "source_refs": [
                *list(latest_spec.source_refs or []),
                {"type": "fork_selection", "id": str(chosen.get("id"))},
            ],
        }
        latest_spec.status = "SUPERSEDED"
        latest_spec = GoalSpecModel(
            id=uuid.uuid4(),
            goal_id=goal_id,
            version=latest_spec.version + 1,
            status="DRAFT" if goal.status == "DRAFT" else "FROZEN",
            content_hash=canonical_hash(spec_content),
            confirmed_by=None if goal.status == "DRAFT" else "regent-core:fork-selection",
            confirmed_at=None if goal.status == "DRAFT" else datetime.now(UTC),
            **spec_content,
        )
        session.add(latest_spec)
        metadata["latest_goal_spec_version"] = latest_spec.version
        metadata["goal_spec_hash"] = latest_spec.content_hash
        metadata["unknowns"] = list(latest_spec.unknowns or [])
        if goal.status == "DRAFT":
            if feasibility_verdict is not None:
                metadata["feasibility_verdict"] = effective_feasibility_verdict(
                    feasibility_verdict,
                    rounds=int(metadata.get("clarification_rounds") or 0),
                    unknowns=metadata["unknowns"],
                )
            readiness = assess_goal_readiness(
                verdict=metadata.get("feasibility_verdict"),
                rounds=int(metadata.get("clarification_rounds") or 0),
                unknowns=metadata["unknowns"],
            )
            metadata["goal_clarity_state"] = (
                "WAITING_CONFIRMATION" if readiness.ready else "FORK_RESOLVED"
            )
            metadata["goal_phase"] = readiness.phase
            metadata["confirmation_state"] = "PENDING" if readiness.ready else "NONE"
            metadata["confirmation_gate_key"] = (
                confirmation_gate_key(goal_id, latest_spec.version) if readiness.ready else None
            )

    goal.metadata_json = metadata
    flag_modified(goal, "metadata_json")
    return goal, metadata, latest_spec
