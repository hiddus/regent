import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import ExperienceRecordModel, ObservationModel


@dataclass(frozen=True, slots=True)
class ObservationInput:
    event_id: str
    goal_id: uuid.UUID | None
    metric_name: str
    metric_value: dict[str, Any]
    source: str
    definition_version: str
    is_bot: bool
    is_internal: bool
    observed_at: datetime


class ObservationService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], signing_key: str) -> None:
        if not signing_key:
            raise ValueError("observation signing key is required")
        self._sessions = sessions
        self._key = signing_key.encode()

    def sign(self, item: ObservationInput) -> str:
        payload = self._canonical(item)
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    async def ingest(self, item: ObservationInput, signature: str) -> uuid.UUID:
        expected = self.sign(item)
        if not hmac.compare_digest(expected, signature):
            raise DomainError(ErrorCode.POLICY_DENIED, "observation signature is invalid")
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(ObservationModel).where(ObservationModel.event_id == item.event_id)
            )
            if existing is not None:
                return existing.id
            observation_id = uuid.uuid4()
            session.add(
                ObservationModel(
                    id=observation_id,
                    event_id=item.event_id,
                    goal_id=item.goal_id,
                    metric_name=item.metric_name,
                    metric_value=item.metric_value,
                    source=item.source,
                    definition_version=item.definition_version,
                    signature=signature,
                    is_bot=item.is_bot,
                    is_internal=item.is_internal,
                    observed_at=item.observed_at,
                )
            )
            return observation_id

    async def create_experience(
        self,
        *,
        goal_id: uuid.UUID,
        observation_ids: list[uuid.UUID],
        outcome: str,
        lesson: str,
        replan_triggered: bool,
        attribution: dict[str, Any],
    ) -> uuid.UUID:
        async with self._sessions() as session, session.begin():
            found = set(
                await session.scalars(
                    select(ObservationModel.id).where(ObservationModel.id.in_(observation_ids))
                )
            )
            if found != set(observation_ids):
                raise DomainError(ErrorCode.NOT_FOUND, "one or more observations do not exist")
            record_id = uuid.uuid4()
            session.add(
                ExperienceRecordModel(
                    id=record_id,
                    goal_id=goal_id,
                    observation_ids=[str(value) for value in observation_ids],
                    outcome=outcome,
                    lesson=lesson,
                    replan_triggered=replan_triggered,
                    attribution=attribution,
                )
            )
            return record_id

    @staticmethod
    def _canonical(item: ObservationInput) -> bytes:
        payload = {
            "event_id": item.event_id,
            "goal_id": str(item.goal_id) if item.goal_id else None,
            "metric_name": item.metric_name,
            "metric_value": item.metric_value,
            "source": item.source,
            "definition_version": item.definition_version,
            "is_bot": item.is_bot,
            "is_internal": item.is_internal,
            "observed_at": item.observed_at.isoformat(),
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
