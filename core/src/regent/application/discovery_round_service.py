import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.goal_eligibility_service import GoalEligibilityService
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    DiscoveryRoundModel,
    GoalModel,
    GoalSpecModel,
    HypothesisDecisionModel,
    ProductHypothesisModel,
)


@dataclass(frozen=True, slots=True)
class RequestDiscoveryRound:
    goal_id: uuid.UUID
    budget: dict[str, int | float]
    policy_version: str
    idempotency_key: str
    actor: str
    correlation_id: str


class DiscoveryRoundService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def request(self, command: RequestDiscoveryRound) -> DiscoveryRoundModel:
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(DiscoveryRoundModel).where(
                    DiscoveryRoundModel.idempotency_key == command.idempotency_key
                )
            )
            if existing is not None:
                if existing.goal_id != command.goal_id:
                    raise DomainError(ErrorCode.INVALID_STATE, "idempotency key scope mismatch")
                return existing
            goal = await session.get(GoalModel, command.goal_id)
            if goal is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"goal {command.goal_id} not found")
            spec = await session.scalar(
                select(GoalSpecModel)
                .where(GoalSpecModel.goal_id == command.goal_id)
                .order_by(GoalSpecModel.version.desc())
                .limit(1)
            )
            if spec is None:
                raise DomainError(ErrorCode.INVALID_STATE, "goal has no specification")
            eligibility = GoalEligibilityService().evaluate(
                goal.metadata_json, spec.explicit_constraints
            )
            if not eligibility.eligible:
                raise DomainError(ErrorCode.POLICY_DENIED, eligibility.reason)
            next_round = (
                int(
                    await session.scalar(
                        select(func.coalesce(func.max(DiscoveryRoundModel.round), 0)).where(
                            DiscoveryRoundModel.goal_id == command.goal_id
                        )
                    )
                    or 0
                )
                + 1
            )
            snapshot = {
                "goal_id": str(goal.id),
                "goal_version": goal.version,
                "spec_version": spec.version,
                "constraints": spec.explicit_constraints,
                "success_criteria": spec.success_criteria,
            }
            model = DiscoveryRoundModel(
                id=uuid.uuid4(),
                goal_id=goal.id,
                round=next_round,
                status="REQUESTED",
                version=0,
                input_snapshot_hash=hashlib.sha256(
                    json.dumps(
                        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode()
                ).hexdigest(),
                budget=command.budget,
                policy_version=command.policy_version,
                idempotency_key=command.idempotency_key,
                created_by=command.actor,
                correlation_id=command.correlation_id,
            )
            session.add(model)
            await session.flush()
            return model

    async def get(self, round_id: uuid.UUID) -> DiscoveryRoundModel:
        async with self._sessions() as session:
            model = await session.get(DiscoveryRoundModel, round_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"discovery round {round_id} not found")
            return model

    async def hypotheses(self, round_id: uuid.UUID) -> list[ProductHypothesisModel]:
        async with self._sessions() as session:
            if await session.get(DiscoveryRoundModel, round_id) is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"discovery round {round_id} not found")
            result = await session.scalars(
                select(ProductHypothesisModel)
                .where(ProductHypothesisModel.round_id == round_id)
                .order_by(ProductHypothesisModel.candidate_key)
            )
            return list(result)

    async def decision(self, round_id: uuid.UUID) -> HypothesisDecisionModel:
        async with self._sessions() as session:
            model = await session.scalar(
                select(HypothesisDecisionModel).where(HypothesisDecisionModel.round_id == round_id)
            )
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, f"decision for round {round_id} not found")
            return model
