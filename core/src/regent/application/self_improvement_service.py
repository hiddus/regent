import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import SelfImprovementRunModel
from regent.infrastructure.self_improvement_sandbox import SelfImprovementSandbox
from regent.model import ModelProvider


class SelfImprovementCandidate(BaseModel):
    replacement_content: str = Field(min_length=20)
    expected_outcome: str = Field(min_length=1)
    risks: list[str] = Field(min_length=1)
    tests_to_run: list[str] = Field(min_length=1)


class IndependentCandidateReview(BaseModel):
    passed: bool
    reason: str = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    required_follow_up_tests: list[str] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SelfImprovementReceipt:
    id: uuid.UUID
    status: str
    primary_problem: str
    hypothesis: str
    target_file: str
    baseline_hash: str
    candidate_hash: str | None
    candidate_workspace: str | None
    expected_outcome: str
    verification: dict[str, object]
    risks: list[str]
    policy_version: str
    approved_by: str | None
    decision_reason: str | None


class SelfImprovementService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: ModelProvider,
        source_root: Path,
        workspace_root: Path,
    ) -> None:
        self._sessions = sessions
        self._provider = provider
        self._sandbox = SelfImprovementSandbox(source_root, workspace_root)

    async def propose(
        self,
        *,
        primary_problem: str,
        hypothesis: str,
        target_file: str,
        actor: str,
    ) -> SelfImprovementReceipt:
        run_id = uuid.uuid4()
        _relative, baseline, baseline_hash = self._sandbox.read_target(target_file)
        run = SelfImprovementRunModel(
            id=run_id,
            status="PROPOSED",
            primary_problem=primary_problem,
            hypothesis=hypothesis,
            target_file=target_file,
            baseline_hash=baseline_hash,
            expected_outcome="pending candidate generation",
            verification_json={},
            risk_json=[],
            policy_version=SelfImprovementSandbox.POLICY_VERSION,
            created_by=actor,
        )
        async with self._sessions() as session, session.begin():
            session.add(run)
        try:
            generated = await self._provider.generate_structured(
                system_prompt=(
                    "Produce one minimal replacement for the supplied Python file to test the "
                    "single hypothesis. Preserve public contracts unless the hypothesis explicitly "
                    "requires a change. Do not weaken tests, permissions, audit, secrets, metrics, "
                    "or approval. Do not add network or subprocess behavior. Return the complete "
                    "replacement file, expected outcome, risks, and tests."
                ),
                user_prompt=str(
                    {
                        "primary_problem": primary_problem,
                        "hypothesis": hypothesis,
                        "target_file": target_file,
                        "baseline_content": baseline,
                    }
                ),
                response_model=SelfImprovementCandidate,
            )
            candidate = generated.output
            verified = self._sandbox.materialize(run_id, target_file, candidate.replacement_content)
            review_response = await self._provider.generate_structured(
                system_prompt=(
                    "Act as an independent candidate reviewer. The candidate cannot change this "
                    "review policy. Reject scope expansion, governance weakening, unsupported "
                    "claims, missing regression coverage, or changes unrelated to the single "
                    "hypothesis. Compilation is only a baseline check, not proof of correctness."
                ),
                user_prompt=str(
                    {
                        "problem": primary_problem,
                        "hypothesis": hypothesis,
                        "target_file": target_file,
                        "baseline_hash": baseline_hash,
                        "candidate_hash": verified.candidate_hash,
                        "replacement_content": candidate.replacement_content,
                        "declared_tests": candidate.tests_to_run,
                        "static_checks": verified.checks,
                    }
                ),
                response_model=IndependentCandidateReview,
            )
            review = review_response.output
            verification: dict[str, object] = {
                "static_checks": verified.checks,
                "independent_review": review.model_dump(mode="json"),
                "declared_tests": candidate.tests_to_run,
                "production_modified": False,
                "automatic_publish_allowed": False,
            }
            async with self._sessions() as session, session.begin():
                locked = await session.get(SelfImprovementRunModel, run_id, with_for_update=True)
                assert locked is not None
                locked.status = "AWAITING_APPROVAL" if review.passed else "CANDIDATE_READY"
                locked.candidate_hash = verified.candidate_hash
                locked.candidate_workspace = str(verified.workspace)
                locked.expected_outcome = candidate.expected_outcome
                locked.verification_json = verification
                locked.risk_json = sorted(set(candidate.risks + review.risks))
                locked.model_ref = f"generator={generated.model};reviewer={review_response.model}"
                await session.flush()
                return self._receipt(locked)
        except Exception:
            async with self._sessions() as session, session.begin():
                failed = await session.get(SelfImprovementRunModel, run_id)
                if failed is not None and failed.status == "PROPOSED":
                    failed.status = "FAILED"
                    failed.failure_code = "SELF_IMPROVEMENT_CANDIDATE_FAILED"
            raise

    async def get(self, run_id: uuid.UUID) -> SelfImprovementReceipt:
        async with self._sessions() as session:
            model = await session.get(SelfImprovementRunModel, run_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "self improvement run not found")
            return self._receipt(model)

    async def decide(
        self, run_id: uuid.UUID, *, approve: bool, actor: str, reason: str
    ) -> SelfImprovementReceipt:
        async with self._sessions() as session, session.begin():
            model = await session.get(SelfImprovementRunModel, run_id, with_for_update=True)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "self improvement run not found")
            if model.status != "AWAITING_APPROVAL":
                raise DomainError(ErrorCode.INVALID_STATE, "candidate is not approval-ready")
            model.status = "APPROVED" if approve else "REJECTED"
            model.approved_by = actor
            model.decision_reason = reason
            model.approved_at = datetime.now(UTC)
            verification = dict(model.verification_json)
            verification["approval_scope"] = (
                "candidate accepted for a separately authorized implementation; "
                "production unchanged"
            )
            model.verification_json = verification
            await session.flush()
            return self._receipt(model)

    @staticmethod
    def _receipt(model: SelfImprovementRunModel) -> SelfImprovementReceipt:
        return SelfImprovementReceipt(
            model.id,
            model.status,
            model.primary_problem,
            model.hypothesis,
            model.target_file,
            model.baseline_hash,
            model.candidate_hash,
            model.candidate_workspace,
            model.expected_outcome,
            model.verification_json,
            model.risk_json,
            model.policy_version,
            model.approved_by,
            model.decision_reason,
        )
