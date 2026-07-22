import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.p1_contracts import canonical_hash
from regent.application.p1_ports import DeploymentProvider, DeploymentRequest
from regent.application.permit_service import PermitService
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AppBuildModel,
    DeploymentModel,
    HumanTaskModel,
    ReleaseCandidateModel,
    VerificationReportModel,
)


@dataclass(frozen=True, slots=True)
class CreateReleaseCandidate:
    app_build_id: uuid.UUID
    actor: str
    correlation_id: str
    human_task_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class RequestDeployment:
    release_candidate_id: uuid.UUID
    permit_id: uuid.UUID
    environment: str
    idempotency_key: str
    correlation_id: str


class ReleaseService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: DeploymentProvider,
    ) -> None:
        self._sessions = sessions
        self._provider = provider
        self._permits = PermitService(sessions)

    async def get_candidate(self, candidate_id: uuid.UUID) -> ReleaseCandidateModel:
        async with self._sessions() as session:
            model = await session.get(ReleaseCandidateModel, candidate_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "release candidate not found")
            return model

    async def get_deployment(self, deployment_id: uuid.UUID) -> DeploymentModel:
        async with self._sessions() as session:
            model = await session.get(DeploymentModel, deployment_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "deployment not found")
            return model

    async def create_candidate(self, command: CreateReleaseCandidate) -> ReleaseCandidateModel:
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(ReleaseCandidateModel).where(
                    ReleaseCandidateModel.app_build_id == command.app_build_id
                )
            )
            if existing is not None:
                return existing
            build = await session.get(AppBuildModel, command.app_build_id)
            report = await session.scalar(
                select(VerificationReportModel).where(
                    VerificationReportModel.app_build_id == command.app_build_id
                )
            )
            if (
                build is None
                or build.status != "PASSED"
                or not build.build_artifact_uri
                or not build.build_artifact_hash
                or report is None
                or not report.passed
            ):
                raise DomainError(
                    ErrorCode.INVALID_STATE, "passed build and verification report are required"
                )
            content_hash = canonical_hash(
                {
                    "build_artifact_hash": build.build_artifact_hash,
                    "verification_evidence_hash": report.evidence_hash,
                    "runtime_profile_hash": report.runtime_profile_hash,
                }
            )
            candidate = ReleaseCandidateModel(
                id=uuid.uuid4(),
                app_build_id=build.id,
                status="READY",
                version=1,
                content_hash=content_hash,
                human_task_id=command.human_task_id,
                created_by=command.actor,
                correlation_id=command.correlation_id,
            )
            session.add(candidate)
            await session.flush()
            return candidate

    async def approve(
        self, candidate_id: uuid.UUID, *, actor: str, reason: str
    ) -> ReleaseCandidateModel:
        async with self._sessions() as session, session.begin():
            candidate = await session.scalar(
                select(ReleaseCandidateModel)
                .where(ReleaseCandidateModel.id == candidate_id)
                .with_for_update()
            )
            if candidate is None:
                raise DomainError(ErrorCode.NOT_FOUND, "release candidate not found")
            # Idempotent: if already approved, return as-is
            if candidate.status == "APPROVED":
                return candidate
            if candidate.status != "READY":
                raise DomainError(ErrorCode.INVALID_STATE, "release candidate is not ready")
            if candidate.human_task_id is not None:
                task = await session.get(HumanTaskModel, candidate.human_task_id)
                if (
                    task is None
                    or task.status != "COMPLETED"
                    or not task.response
                    or task.response.get("decision") != "APPROVE"
                ):
                    raise DomainError(
                        ErrorCode.POLICY_DENIED, "release approval task is incomplete"
                    )
            candidate.status = "APPROVED"
            candidate.version += 1
            candidate.approved_by = actor
            candidate.decision_reason = reason
            return candidate

    async def reject(
        self, candidate_id: uuid.UUID, *, actor: str, reason: str
    ) -> ReleaseCandidateModel:
        async with self._sessions() as session, session.begin():
            candidate = await session.scalar(
                select(ReleaseCandidateModel)
                .where(ReleaseCandidateModel.id == candidate_id)
                .with_for_update()
            )
            if candidate is None or candidate.status != "READY":
                raise DomainError(ErrorCode.INVALID_STATE, "release candidate is not ready")
            candidate.status = "REJECTED"
            candidate.version += 1
            candidate.approved_by = actor
            candidate.decision_reason = reason
            return candidate

    async def request_deployment(self, command: RequestDeployment) -> DeploymentModel:
        if command.environment != "preview":
            raise DomainError(ErrorCode.POLICY_DENIED, "P1 only permits preview deployment")
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(DeploymentModel).where(
                    DeploymentModel.idempotency_key == command.idempotency_key
                )
            )
            if existing is not None:
                if existing.release_candidate_id != command.release_candidate_id:
                    raise DomainError(ErrorCode.INVALID_STATE, "idempotency key scope mismatch")
                return existing
            candidate = await session.get(ReleaseCandidateModel, command.release_candidate_id)
            if candidate is None or candidate.status != "APPROVED":
                raise DomainError(ErrorCode.POLICY_DENIED, "approved release candidate is required")
            deployment = DeploymentModel(
                id=uuid.uuid4(),
                release_candidate_id=candidate.id,
                permit_id=command.permit_id,
                environment=command.environment,
                status="REQUESTED",
                version=0,
                idempotency_key=command.idempotency_key,
                evidence={},
                reconciliation_required=False,
                correlation_id=command.correlation_id,
            )
            session.add(deployment)
            await session.flush()
            return deployment

    async def execute(self, deployment_id: uuid.UUID) -> DeploymentModel:
        # Idempotent: check if deployment already completed
        async with self._sessions() as session, session.begin():
            existing = await session.get(DeploymentModel, deployment_id)
            if existing is not None and existing.status in ("SUCCEEDED", "FAILED"):
                return existing
        deployment, artifact_uri = await self._claim(deployment_id)
        permit = await self._permits.claim(
            deployment.permit_id, actor_id="preview-deployment-provider"
        )
        if permit.binding.action != "preview-deploy":
            raise DomainError(ErrorCode.POLICY_DENIED, "permit action mismatch")
        await self._permits.consume(permit.id, nonce=permit.nonce)
        try:
            result = await self._provider.deploy(
                DeploymentRequest(
                    build_artifact_uri=artifact_uri,
                    environment="preview",
                    idempotency_key=deployment.idempotency_key,
                    correlation_id=deployment.correlation_id,
                )
            )
        except Exception:
            await self._mark_unknown(deployment_id)
            raise
        return await self._commit_result(deployment_id, result)

    async def reconcile(self, deployment_id: uuid.UUID) -> DeploymentModel:
        async with self._sessions() as session:
            deployment = await session.get(DeploymentModel, deployment_id)
            if (
                deployment is None
                or deployment.status != "UNKNOWN"
                or deployment.external_deployment_id is None
            ):
                raise DomainError(ErrorCode.INVALID_STATE, "deployment is not reconcilable")
            external_id = deployment.external_deployment_id
        result = await self._provider.query(external_id)
        if result.status == "UNKNOWN":
            return deployment
        return await self._commit_result(deployment_id, result, expected="UNKNOWN")

    async def rollback(self, deployment_id: uuid.UUID, *, permit_id: uuid.UUID) -> DeploymentModel:
        async with self._sessions() as session:
            deployment = await session.get(DeploymentModel, deployment_id)
            if (
                deployment is None
                or deployment.status != "SUCCEEDED"
                or deployment.external_deployment_id is None
            ):
                raise DomainError(ErrorCode.INVALID_STATE, "deployment cannot be rolled back")
            external_id = deployment.external_deployment_id
            correlation_id = deployment.correlation_id
        permit = await self._permits.claim(permit_id, actor_id="preview-deployment-provider")
        if permit.binding.action != "preview-rollback":
            raise DomainError(ErrorCode.POLICY_DENIED, "rollback permit action mismatch")
        await self._permits.consume(permit.id, nonce=permit.nonce)
        result = await self._provider.rollback(external_id, correlation_id)
        if result.status != "SUCCEEDED":
            raise DomainError(ErrorCode.RECONCILIATION_REQUIRED, "rollback result is not confirmed")
        async with self._sessions() as session, session.begin():
            model = await session.get(DeploymentModel, deployment_id)
            assert model is not None
            model.status = "ROLLED_BACK"
            model.version += 1
            model.rollback_permit_id = permit_id
            model.evidence = result.evidence
            return model

    async def _claim(self, deployment_id: uuid.UUID) -> tuple[DeploymentModel, str]:
        async with self._sessions() as session, session.begin():
            deployment = await session.scalar(
                select(DeploymentModel).where(DeploymentModel.id == deployment_id).with_for_update()
            )
            if deployment is None:
                raise DomainError(ErrorCode.NOT_FOUND, "deployment not found")
            # Idempotent: if already deploying or terminal, return as-is
            if deployment.status in ("DEPLOYING", "SUCCEEDED", "FAILED"):
                candidate = await session.get(
                    ReleaseCandidateModel, deployment.release_candidate_id
                )
                assert candidate is not None
                build = await session.get(AppBuildModel, candidate.app_build_id)
                assert build is not None and build.build_artifact_uri is not None
                return deployment, build.build_artifact_uri
            if deployment.status != "REQUESTED":
                raise DomainError(ErrorCode.INVALID_STATE, "deployment is not requestable")
            candidate = await session.get(ReleaseCandidateModel, deployment.release_candidate_id)
            assert candidate is not None
            build = await session.get(AppBuildModel, candidate.app_build_id)
            assert build is not None and build.build_artifact_uri is not None
            deployment.status = "DEPLOYING"
            deployment.version += 1
            return deployment, build.build_artifact_uri

    async def _commit_result(
        self, deployment_id: uuid.UUID, result: object, *, expected: str = "DEPLOYING"
    ) -> DeploymentModel:
        from regent.application.p1_ports import DeploymentResult

        assert isinstance(result, DeploymentResult)
        async with self._sessions() as session, session.begin():
            model = await session.get(DeploymentModel, deployment_id)
            if model is None or model.status != expected:
                raise DomainError(ErrorCode.INVALID_STATE, "deployment result cannot be committed")
            model.status = result.status
            model.version += 1
            model.external_deployment_id = result.external_request_id
            model.endpoint = result.endpoint
            model.evidence = result.evidence
            model.reconciliation_required = result.status == "UNKNOWN"
            model.failure_code = "UNKNOWN_RESULT" if result.status == "UNKNOWN" else None
            return model

    async def _mark_unknown(self, deployment_id: uuid.UUID) -> None:
        async with self._sessions() as session, session.begin():
            model = await session.get(DeploymentModel, deployment_id)
            if model is not None and model.status == "DEPLOYING":
                model.status = "UNKNOWN"
                model.version += 1
                model.failure_code = "UNKNOWN_RESULT"
                model.reconciliation_required = True
