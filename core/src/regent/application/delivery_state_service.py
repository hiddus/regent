"""Persistence and policy helpers for observable delivery-state transitions."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from regent.application.delivery_gap_recovery import DeliveryGapRecoveryResult
from regent.application.delivery_state import DeliveryState
from regent.application.execution_events import (
    DELIVERY_STATE_CHANGED,
    EventEnvelope,
    make_outbox_event,
)
from regent.infrastructure.models import GoalModel


def verdict_inputs_for_recovery(recovery: DeliveryGapRecoveryResult) -> dict[str, Any]:
    if recovery.recovered:
        return dict(success=False, needs_human=False, recoverable=True, budget_left=True)
    if recovery.terminal_exhaust:
        return dict(
            success=False,
            needs_human=True,
            recoverable=True,
            budget_left=False,
            review_prompt=recovery.message,
        )
    return dict(success=False, needs_human=False, recoverable=False, budget_left=False)


async def record_delivery_state(
    sessions: async_sessionmaker[AsyncSession],
    goal_id: uuid.UUID,
    *,
    state: DeliveryState,
    gap_kind: str,
    attempts: int,
) -> None:
    async with sessions() as session, session.begin():
        goal = await session.get(GoalModel, goal_id, with_for_update=True)
        if goal is None:
            return
        metadata = dict(goal.metadata_json or {})
        metadata["delivery_state"] = state.value
        goal.metadata_json = metadata
        flag_modified(goal, "metadata_json")
        session.add(
            make_outbox_event(
                EventEnvelope(
                    event_type=DELIVERY_STATE_CHANGED,
                    aggregate_type="goal",
                    aggregate_id=goal.id,
                    aggregate_version=goal.version,
                    payload={
                        "goal_id": str(goal.id),
                        "delivery_state": state.value,
                        "gap_kind": gap_kind,
                        "attempts": attempts,
                    },
                    correlation_id=goal.correlation_id,
                )
            )
        )
