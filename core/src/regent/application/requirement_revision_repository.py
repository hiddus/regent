import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.p1_contracts import AppRequirementProposal, canonical_hash
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    DiscoveryRoundModel,
    HypothesisDecisionModel,
    ProductHypothesisModel,
    RequirementRevisionModel,
)


@dataclass(frozen=True, slots=True)
class CreateRequirementRevision:
    hypothesis_id: uuid.UUID
    requirement_key: str
    proposal: AppRequirementProposal
    generator_ref: str
    actor: str


class RequirementRevisionRepositoryService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(self, command: CreateRequirementRevision) -> RequirementRevisionModel:
        async with self._sessions() as session, session.begin():
            hypothesis = await session.get(ProductHypothesisModel, command.hypothesis_id)
            if hypothesis is None:
                raise DomainError(ErrorCode.NOT_FOUND, "hypothesis not found")
            selected = await session.scalar(
                select(HypothesisDecisionModel).where(
                    HypothesisDecisionModel.round_id == hypothesis.round_id,
                    HypothesisDecisionModel.decision == "SELECT",
                    HypothesisDecisionModel.selected_hypothesis_id == hypothesis.id,
                )
            )
            if selected is None:
                raise DomainError(
                    ErrorCode.POLICY_DENIED,
                    "requirements may only derive from the selected hypothesis",
                )
            round_goal_id = hypothesis.round_id
            goal_id = await session.scalar(
                select(DiscoveryRoundModel.goal_id).where(DiscoveryRoundModel.id == round_goal_id)
            )
            if goal_id is None:
                raise DomainError(ErrorCode.INVALID_STATE, "hypothesis has no discovery goal")
            previous = await session.scalar(
                select(RequirementRevisionModel)
                .where(
                    RequirementRevisionModel.goal_id == goal_id,
                    RequirementRevisionModel.requirement_key == command.requirement_key,
                )
                .order_by(RequirementRevisionModel.revision.desc())
                .limit(1)
            )
            revision = (previous.revision if previous else 0) + 1
            content = command.proposal.model_dump(mode="json")
            model = RequirementRevisionModel(
                id=uuid.uuid4(),
                goal_id=goal_id,
                hypothesis_id=hypothesis.id,
                requirement_key=command.requirement_key,
                revision=revision,
                predecessor_id=previous.id if previous else None,
                status="DRAFT",
                version=0,
                content_json=content,
                content_hash=canonical_hash(content),
                generator_ref=command.generator_ref,
                created_by=command.actor,
            )
            session.add(model)
            await session.flush()
            return model

    async def get(self, revision_id: uuid.UUID) -> RequirementRevisionModel:
        async with self._sessions() as session:
            model = await session.get(RequirementRevisionModel, revision_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "requirement revision not found")
            return model
