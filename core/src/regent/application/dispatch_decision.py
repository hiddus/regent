"""Process-level DispatchDecision records (Spec §18.2).

Immutable per-step dispatch audit: who was chosen, why, on what evidence.
May be persisted as SchedulingDecision child records or standalone rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.multiagent_metrics import (
    DispatchEntropyStep,
    compute_dispatch_entropy,
    compute_step_entropy,
)
from regent.application.p1_contracts import canonical_hash
from regent.infrastructure.models import DispatchDecisionModel

DISPATCH_POLICY_VERSION = "dispatch-decision/v1"


@dataclass(frozen=True, slots=True)
class DispatchDecisionInput:
    goal_id: uuid.UUID
    run_id: uuid.UUID | None
    step_id: str
    organization_version_id: uuid.UUID | None
    source_agent_id: str | None
    selected_agent_id: str
    candidate_agent_ids: Sequence[str]
    candidate_weights: Mapping[str, float]
    evidence_refs: Sequence[str] = ()
    reason_code: str = "UTILITY_ARGMAX"
    policy_version: str = DISPATCH_POLICY_VERSION
    capability_scope: Mapping[str, Any] | Sequence[str] | None = None
    permit_refs: Sequence[str] = ()
    input_payload: Mapping[str, Any] | None = None
    output_summary: Mapping[str, Any] | None = None
    scheduling_decision_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class DispatchDecisionView:
    id: uuid.UUID
    goal_id: uuid.UUID
    run_id: uuid.UUID | None
    step_id: str
    selected_agent_id: str
    candidate_agent_ids: list[str]
    reason_code: str
    policy_version: str
    evidence_refs: list[str]
    capability_scope: dict[str, Any]
    permit_refs: list[str]
    input_digest: str
    output_digest: str
    entropy: float | None
    candidate_weights: dict[str, float]
    created_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "goal_id": str(self.goal_id),
            "run_id": str(self.run_id) if self.run_id else None,
            "step_id": self.step_id,
            "selected_agent_id": self.selected_agent_id,
            "candidate_agent_ids": list(self.candidate_agent_ids),
            "reason_code": self.reason_code,
            "policy_version": self.policy_version,
            "evidence_refs": list(self.evidence_refs),
            "capability_scope": dict(self.capability_scope),
            "permit_refs": list(self.permit_refs),
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
            "entropy": self.entropy,
            "candidate_weights": dict(self.candidate_weights),
            "created_at": self.created_at.isoformat(),
        }


def build_dispatch_record(command: DispatchDecisionInput) -> dict[str, Any]:
    weights = {str(k): float(v) for k, v in dict(command.candidate_weights).items()}
    if command.selected_agent_id not in weights and command.candidate_agent_ids:
        # Ensure selected is present for entropy accounting.
        weights.setdefault(command.selected_agent_id, 1.0)
    entropy = compute_step_entropy(weights)
    scope = command.capability_scope
    if scope is None:
        scope_dict: dict[str, Any] = {}
    elif isinstance(scope, Mapping):
        scope_dict = dict(scope)
    else:
        scope_dict = {"allow": list(scope)}
    input_digest = canonical_hash(dict(command.input_payload or {"step_id": command.step_id}))
    output_digest = canonical_hash(
        dict(command.output_summary or {"selected": command.selected_agent_id})
    )
    return {
        "id": uuid.uuid4(),
        "goal_id": command.goal_id,
        "run_id": command.run_id,
        "step_id": command.step_id,
        "organization_version_id": command.organization_version_id,
        "scheduling_decision_id": command.scheduling_decision_id,
        "source_agent_id": command.source_agent_id,
        "selected_agent_id": command.selected_agent_id,
        "candidate_agent_ids": list(command.candidate_agent_ids),
        "candidate_weights": weights,
        "evidence_refs": list(command.evidence_refs),
        "reason_code": command.reason_code,
        "policy_version": command.policy_version,
        "capability_scope": scope_dict,
        "permit_refs": list(command.permit_refs),
        "input_digest": input_digest,
        "output_digest": output_digest,
        "entropy": None if entropy is None else round(entropy, 6),
        "created_at": datetime.now(UTC),
    }


class DispatchDecisionService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(self, command: DispatchDecisionInput) -> DispatchDecisionView:
        payload = build_dispatch_record(command)
        async with self._sessions() as session, session.begin():
            model = DispatchDecisionModel(**payload)
            session.add(model)
            await session.flush()
            return _to_view(model)

    async def list_for_goal(self, goal_id: uuid.UUID) -> list[DispatchDecisionView]:
        from sqlalchemy import select

        async with self._sessions() as session:
            rows = await session.scalars(
                select(DispatchDecisionModel)
                .where(DispatchDecisionModel.goal_id == goal_id)
                .order_by(DispatchDecisionModel.created_at.asc())
            )
            return [_to_view(m) for m in rows]

    async def entropy_report(self, goal_id: uuid.UUID) -> dict[str, Any]:
        views = await self.list_for_goal(goal_id)
        steps = [
            DispatchEntropyStep(
                step_id=v.step_id,
                candidate_weights=v.candidate_weights,
                entropy=v.entropy,
            )
            for v in views
        ]
        metric = compute_dispatch_entropy(steps)
        return {
            "goal_id": str(goal_id),
            "dispatch_count": len(views),
            "metric": metric.as_dict(),
            "replay": [v.as_dict() for v in views],
        }


def _to_view(model: DispatchDecisionModel) -> DispatchDecisionView:
    return DispatchDecisionView(
        id=model.id,
        goal_id=model.goal_id,
        run_id=model.run_id,
        step_id=model.step_id,
        selected_agent_id=model.selected_agent_id,
        candidate_agent_ids=list(model.candidate_agent_ids or []),
        reason_code=model.reason_code,
        policy_version=model.policy_version,
        evidence_refs=list(model.evidence_refs or []),
        capability_scope=dict(model.capability_scope or {}),
        permit_refs=list(model.permit_refs or []),
        input_digest=model.input_digest,
        output_digest=model.output_digest,
        entropy=model.entropy,
        candidate_weights=dict(model.candidate_weights or {}),
        created_at=model.created_at,
    )
