import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.p1_contracts import canonical_hash
from regent.application.p1_ports import EvidenceSourceRequest
from regent.application.product_discovery_service import DiscoveryOutcome, ProductDiscoveryService
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    DiscoveryRoundModel,
    HypothesisDecisionModel,
    HypothesisEvidenceRefModel,
    ProductHypothesisModel,
)


class DiscoveryWorker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        discovery: ProductDiscoveryService,
    ) -> None:
        self._sessions = sessions
        self._discovery = discovery

    async def run(
        self,
        round_id: uuid.UUID,
        *,
        goal: str,
        constraints: dict[str, object],
        requests: list[EvidenceSourceRequest],
        evidence_ids_by_hash: dict[str, uuid.UUID],
    ) -> DiscoveryOutcome:
        await self._mark_researching(round_id)
        try:
            outcome = await self._discovery.discover(
                goal=goal,
                constraints=constraints,
                requests=requests,
                evidence_ids_by_hash=evidence_ids_by_hash,
            )
            await self._commit_outcome(round_id, outcome)
            return outcome
        except Exception:
            await self._mark_failed(round_id)
            raise

    async def _mark_researching(self, round_id: uuid.UUID) -> None:
        async with self._sessions() as session, session.begin():
            model = await session.scalar(
                select(DiscoveryRoundModel)
                .where(DiscoveryRoundModel.id == round_id)
                .with_for_update()
            )
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "discovery round not found")
            if model.status != "REQUESTED":
                raise DomainError(ErrorCode.INVALID_STATE, "discovery round is not requestable")
            model.status = "RESEARCHING"
            model.version += 1

    async def _commit_outcome(self, round_id: uuid.UUID, outcome: DiscoveryOutcome) -> None:
        async with self._sessions() as session, session.begin():
            round_model = await session.scalar(
                select(DiscoveryRoundModel)
                .where(DiscoveryRoundModel.id == round_id)
                .with_for_update()
            )
            if round_model is None or round_model.status != "RESEARCHING":
                raise DomainError(ErrorCode.INVALID_STATE, "discovery round cannot accept outcome")
            hypothesis_models: dict[str, ProductHypothesisModel] = {}
            for proposal in outcome.hypotheses:
                content = proposal.model_dump(mode="json")
                model = ProductHypothesisModel(
                    id=uuid.uuid4(),
                    round_id=round_id,
                    candidate_key=proposal.candidate_key,
                    content_json=content,
                    content_hash=canonical_hash(content),
                    eligibility="ELIGIBLE",
                    invalid_reasons=[],
                    generator_ref=outcome.model_refs[0],
                )
                session.add(model)
                hypothesis_models[proposal.candidate_key] = model
            # Flush hypotheses before evidence refs (FK order)
            await session.flush()
            for proposal in outcome.hypotheses:
                model = hypothesis_models[proposal.candidate_key]
                for claim in proposal.claims:
                    for evidence_id in claim.evidence_ids:
                        session.add(
                            HypothesisEvidenceRefModel(
                                id=uuid.uuid4(),
                                hypothesis_id=model.id,
                                evidence_id=evidence_id,
                                claim_key=claim.claim_key,
                                relation=claim.classification.value.upper(),
                            )
                        )
            selected = (
                hypothesis_models.get(outcome.decision.selected_candidate_key)
                if outcome.decision.selected_candidate_key
                else None
            )
            # Safety fallback: if SELECT but lookup failed, use first hypothesis
            decision_value = outcome.decision.decision.value
            if decision_value == "SELECT" and selected is None and hypothesis_models:
                selected = next(iter(hypothesis_models.values()))
                outcome.decision.selected_candidate_key = selected.candidate_key
            session.add(
                HypothesisDecisionModel(
                    id=uuid.uuid4(),
                    round_id=round_id,
                    decision=decision_value,
                    selected_hypothesis_id=selected.id if selected else None,
                    rationale=outcome.decision.rationale,
                    evidence_digest=outcome.evidence_digest,
                    policy_version=outcome.decision.policy_version,
                    created_by="discovery-worker",
                )
            )
            round_model.status = "DECIDED"
            round_model.version += 2

    async def _mark_failed(self, round_id: uuid.UUID) -> None:
        async with self._sessions() as session, session.begin():
            model = await session.get(DiscoveryRoundModel, round_id)
            if model is not None and model.status == "RESEARCHING":
                model.status = "FAILED"
                model.failure_code = "DISCOVERY_EXECUTION_FAILED"
                model.version += 1
