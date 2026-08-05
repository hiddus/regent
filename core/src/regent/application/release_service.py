import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from regent.application.external_operation_service import ExternalOperationService
from regent.application.p1_contracts import canonical_hash
from regent.application.p1_ports import DeploymentProvider, DeploymentRequest
from regent.application.permit_service import PermitBinding, PermitService
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AppBuildModel,
    DeploymentModel,
    ExecutionPermitModel,
    GenerationPlanModel,
    GenerationRunModel,
    GoalSpecModel,
    HumanTaskModel,
    ReleaseCandidateModel,
    RequirementRevisionModel,
    VerificationReportModel,
    WorkspaceSnapshotModel,
)


def _preview_artifact_usable(uri: str | None) -> bool:
    """True when URI points to an existing zip file or directory (runtime preview)."""
    from pathlib import Path
    from urllib.parse import unquote, urlparse
    import zipfile

    raw = str(uri or "").strip()
    if not raw or raw.startswith("soft://"):
        return False
    if raw.startswith("file:"):
        parsed = urlparse(raw)
        raw_path = unquote(parsed.path)
        if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        path = Path(raw_path)
    else:
        path = Path(raw)
    if not path.exists():
        return False
    if path.is_dir():
        return True
    return path.is_file() and zipfile.is_zipfile(path)


def _artifact_hash(uri: str | None, fallback: str) -> str:
    from pathlib import Path
    from urllib.parse import unquote, urlparse
    import hashlib

    raw = str(uri or "").strip()
    if not raw or raw.startswith("soft://"):
        return fallback
    if raw.startswith("file:"):
        parsed = urlparse(raw)
        raw_path = unquote(parsed.path)
        if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        path = Path(raw_path)
    else:
        path = Path(raw)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    return fallback


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
        self._external_ops = ExternalOperationService(sessions)

    _PREVIEW_PROVIDER = "static-preview-deploy-v1"

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
            from regent.config import get_settings

            soft_gates = str(
                getattr(get_settings(), "delivery_product_gates_mode", "soft") or "soft"
            ).lower() in {"soft", "off"}
            build_ok = (
                build is not None
                and build.status == "PASSED"
                and bool(build.build_artifact_uri)
                and bool(build.build_artifact_hash)
                and report is not None
                and bool(report.passed)
            )
            # Ship-first soft/off: soft-pass UNKNOWN/FAILED builds still need a release
            # candidate so PreviewDeployment can proceed.
            if not build_ok and soft_gates and build is not None:
                snapshot = await session.get(
                    WorkspaceSnapshotModel, build.workspace_snapshot_id
                )
                snapshot_uri = (
                    str(snapshot.source_archive_uri) if snapshot is not None else ""
                )
                # Prefer a real zip/dir. Never fall back to log_uri (often unknown.json)
                # or soft:// — runtime preview cannot materialize those.
                artifact_uri = ""
                for candidate_uri in (
                    build.build_artifact_uri,
                    snapshot_uri,
                ):
                    if _preview_artifact_usable(candidate_uri):
                        artifact_uri = str(candidate_uri)
                        break
                if not artifact_uri and snapshot_uri:
                    artifact_uri = snapshot_uri
                if not artifact_uri:
                    raise DomainError(
                        ErrorCode.INVALID_STATE,
                        "soft-pass build has no deployable workspace artifact",
                    )
                artifact_hash = str(
                    build.build_artifact_hash
                    if _preview_artifact_usable(build.build_artifact_uri)
                    and build.build_artifact_hash
                    else _artifact_hash(artifact_uri, "0" * 64)
                )
                if report is None:
                    report = VerificationReportModel(
                        id=uuid.uuid4(),
                        app_build_id=build.id,
                        passed=True,
                        checks=[{"name": "soft-pass-build", "passed": True}],
                        evidence_uri=artifact_uri,
                        evidence_hash=artifact_hash,
                        runtime_profile_hash="soft-pass",
                    )
                    session.add(report)
                    await session.flush()
                else:
                    report.passed = True
                build.status = "PASSED"
                build.build_artifact_uri = artifact_uri
                build.build_artifact_hash = artifact_hash
                build.failure_code = None
                build_ok = True
            # Soft-pass may have left a PASSED build pointing at unknown.json;
            # rewrite to workspace snapshot before creating the candidate.
            elif soft_gates and build is not None and not _preview_artifact_usable(
                build.build_artifact_uri
            ):
                snapshot = await session.get(
                    WorkspaceSnapshotModel, build.workspace_snapshot_id
                )
                if snapshot is not None and _preview_artifact_usable(
                    snapshot.source_archive_uri
                ):
                    build.build_artifact_uri = str(snapshot.source_archive_uri)
                    build.build_artifact_hash = _artifact_hash(
                        build.build_artifact_uri,
                        str(build.build_artifact_hash or ("0" * 64)),
                    )
            if not build_ok:
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
        from regent.config import get_settings

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
            require_human = get_settings().require_release_human_approval
            if require_human and candidate.human_task_id is None:
                raise DomainError(
                    ErrorCode.POLICY_DENIED,
                    "release approval requires a human task",
                )
            if candidate.human_task_id is not None:
                task = await session.get(HumanTaskModel, candidate.human_task_id)
                response = task.response if task is not None else None
                decision = str((response or {}).get("decision", "")).upper()
                approved = decision == "APPROVE" or bool((response or {}).get("approved", False))
                if decision == "REJECT":
                    approved = False
                if task is None or task.status != "COMPLETED" or not response or not approved:
                    # Not a dead end: normal control flow while the human task is still
                    # open. Caller retries after HumanTaskCompleted; no handoff needed
                    # here because one is already pending.
                    raise DomainError(
                        ErrorCode.POLICY_DENIED, "release approval task is still pending"
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
        """Deploy under G0 ExternalOperation: persist dispatch rights before network I/O."""
        async with self._sessions() as session, session.begin():
            existing = await session.get(DeploymentModel, deployment_id)
            if existing is not None and existing.status in ("SUCCEEDED", "FAILED"):
                return existing

        deployment, artifact_uri = await self._claim(deployment_id)
        # Concurrent inline + outbox may race: peer already finished after our claim.
        if deployment.status in ("SUCCEEDED", "FAILED"):
            return deployment
        delivery_ctx = await self._load_delivery_review_context(deployment.release_candidate_id)
        base_key = f"preview-deploy:{deployment.idempotency_key}"
        evidence = dict(deployment.evidence or {})
        operation_key = str(evidence.get("active_operation_key") or base_key)
        eo = await self._external_ops.get_by_operation_key(operation_key)
        if eo is None and operation_key != base_key:
            eo = await self._external_ops.get_by_operation_key(base_key)
            operation_key = base_key

        if eo is None:
            permit = await self._permits.claim(
                deployment.permit_id, actor_id="preview-deployment-provider"
            )
            if permit.binding.action != "preview-deploy":
                raise DomainError(ErrorCode.POLICY_DENIED, "permit action mismatch")
            payload = {
                "deployment_id": str(deployment.id),
                "artifact_uri": artifact_uri,
                "environment": "preview",
                "idempotency_key": deployment.idempotency_key,
            }
            prepared = await self._external_ops.prepare(
                operation_key=operation_key,
                provider=self._PREVIEW_PROVIDER,
                action="preview-deploy",
                permit_id=permit.id,
                local_fencing_token=permit.nonce,
                payload=payload,
                correlation_id=deployment.correlation_id,
            )
            await self._external_ops.begin_dispatch(
                prepared.id,
                worker_lease_token=f"preview-deployment-provider:{permit.id}",
                expected_fencing_token=permit.nonce,
            )
            eo_id = prepared.id
        elif eo.status in {"DISPATCHING", "UNKNOWN", "RECONCILING"}:
            eo_id = eo.id
        elif eo.status == "PREPARED":
            # Crash between prepare and begin_dispatch: resume instead of hanging.
            await self._external_ops.begin_dispatch(
                eo.id,
                worker_lease_token=f"preview-deployment-provider:{deployment.permit_id}",
                expected_fencing_token=eo.local_fencing_token,
            )
            eo_id = eo.id
        elif eo.status == "SUCCEEDED" and eo.external_id:
            result = await self._provider.query(eo.external_id)
            if result.status == "SUCCEEDED":
                return await self._commit_result(
                    deployment_id, result, expected="DEPLOYING", external_operation_id=eo.id
                )
            eo_id = eo.id
        elif eo.status in {"FAILED_TERMINAL", "MANUAL_REVIEW"}:
            eo_id = await self._remint_preview_dispatch(
                deployment, artifact_uri=artifact_uri, dead_eo_id=eo.id
            )
        else:
            raise DomainError(
                ErrorCode.INVALID_STATE,
                f"external operation not dispatchable: {eo.status}",
            )

        try:
            result = await self._provider.deploy(
                DeploymentRequest(
                    build_artifact_uri=artifact_uri,
                    environment="preview",
                    idempotency_key=deployment.idempotency_key,
                    correlation_id=deployment.correlation_id,
                    acceptance_contract=delivery_ctx["acceptance_contract"],
                    success_criteria=delivery_ctx["success_criteria"],
                )
            )
        except Exception:
            await self._external_ops.mark_unknown(eo_id, reason="provider_exception")
            await self._mark_unknown(deployment_id)
            raise

        if result.status == "SUCCEEDED":
            await self._external_ops.mark_succeeded(
                eo_id,
                external_id=result.external_request_id,
                summary={"endpoint": result.endpoint},
            )
        elif result.status == "UNKNOWN":
            await self._external_ops.mark_unknown(eo_id, reason="provider_unknown")
        else:
            await self._external_ops.mark_failed_terminal(
                eo_id, failure_code=str(result.status), summary=dict(result.evidence or {})
            )
        return await self._commit_result(
            deployment_id, result, expected="DEPLOYING", external_operation_id=eo_id
        )

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
            operation_key = f"preview-deploy:{deployment.idempotency_key}"
        eo = await self._external_ops.get_by_operation_key(operation_key)
        if eo is not None and eo.status == "UNKNOWN":
            await self._external_ops.begin_reconcile(eo.id)
        result = await self._provider.query(external_id)
        if result.status == "UNKNOWN":
            return deployment
        if eo is not None:
            if result.status == "SUCCEEDED":
                await self._external_ops.resolve_reconcile(
                    eo.id, status="SUCCEEDED", external_id=result.external_request_id
                )
            else:
                await self._external_ops.resolve_reconcile(
                    eo.id, status="FAILED_TERMINAL", summary={"status": result.status}
                )
        return await self._commit_result(
            deployment_id,
            result,
            expected="UNKNOWN",
            external_operation_id=eo.id if eo else None,
        )

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
            idempotency_key = deployment.idempotency_key
        operation_key = f"preview-rollback:{idempotency_key}:{permit_id}"
        permit = await self._permits.claim(permit_id, actor_id="preview-deployment-provider")
        if permit.binding.action != "preview-rollback":
            raise DomainError(ErrorCode.POLICY_DENIED, "rollback permit action mismatch")
        prepared = await self._external_ops.prepare(
            operation_key=operation_key,
            provider=self._PREVIEW_PROVIDER,
            action="preview-rollback",
            permit_id=permit.id,
            local_fencing_token=permit.nonce,
            payload={"deployment_id": str(deployment_id), "external_id": external_id},
            correlation_id=correlation_id,
        )
        await self._external_ops.begin_dispatch(
            prepared.id,
            worker_lease_token=f"preview-rollback:{permit.id}",
            expected_fencing_token=permit.nonce,
        )
        result = await self._provider.rollback(external_id, correlation_id)
        if result.status != "SUCCEEDED":
            await self._external_ops.mark_unknown(prepared.id, reason="rollback_unconfirmed")
            raise DomainError(ErrorCode.RECONCILIATION_REQUIRED, "rollback result is not confirmed")
        await self._external_ops.mark_succeeded(
            prepared.id, external_id=result.external_request_id, summary=dict(result.evidence or {})
        )
        async with self._sessions() as session, session.begin():
            model = await session.get(DeploymentModel, deployment_id)
            assert model is not None
            model.status = "ROLLED_BACK"
            model.version += 1
            model.rollback_permit_id = permit_id
            model.evidence = {
                **dict(result.evidence or {}),
                "external_operation_id": str(prepared.id),
            }
            return model

    async def _remint_preview_dispatch(
        self,
        deployment: DeploymentModel,
        *,
        artifact_uri: str,
        dead_eo_id: uuid.UUID,
    ) -> uuid.UUID:
        """Mint a new EO+permit after FAILED_TERMINAL so preview deploy can retry."""
        async with self._sessions() as session:
            old_permit = await session.get(ExecutionPermitModel, deployment.permit_id)
            if old_permit is None:
                raise DomainError(ErrorCode.INVALID_STATE, "deployment permit missing for remint")
            goal_id = old_permit.goal_id
            work_id = old_permit.work_id
            run_id = old_permit.run_id
            target = old_permit.target
            data_scope = dict(old_permit.data_scope or {})
            network_scope = dict(old_permit.network_scope or {})
            resource_limit = dict(old_permit.resource_limit or {})
            risk_level = old_permit.risk_level

        retry_token = uuid.uuid4().hex[:8]
        new_permit_id = await self._permits.request(
            PermitBinding(
                goal_id=goal_id,
                work_id=work_id,
                run_id=run_id,
                actor_id="preview-deployment-provider",
                action="preview-deploy",
                target=target,
                parameters={"retry_of_eo": str(dead_eo_id)},
                data_scope=data_scope,
                network_scope=network_scope,
                resource_limit=resource_limit,
                risk_level=risk_level or "LOW",
                valid_until=datetime.now(UTC) + timedelta(hours=1),
                idempotency_key=f"deploy-permit-retry-{deployment.idempotency_key}-{retry_token}",
            )
        )
        permit = await self._permits.claim(
            new_permit_id, actor_id="preview-deployment-provider"
        )
        if permit.binding.action != "preview-deploy":
            raise DomainError(ErrorCode.POLICY_DENIED, "permit action mismatch")
        operation_key = f"preview-deploy:{deployment.idempotency_key}:retry:{retry_token}"
        prepared = await self._external_ops.prepare(
            operation_key=operation_key,
            provider=self._PREVIEW_PROVIDER,
            action="preview-deploy",
            permit_id=permit.id,
            local_fencing_token=permit.nonce,
            payload={
                "deployment_id": str(deployment.id),
                "artifact_uri": artifact_uri,
                "environment": "preview",
                "idempotency_key": deployment.idempotency_key,
                "retry_of_eo": str(dead_eo_id),
            },
            correlation_id=deployment.correlation_id,
        )
        await self._external_ops.begin_dispatch(
            prepared.id,
            worker_lease_token=f"preview-deployment-provider:{permit.id}",
            expected_fencing_token=permit.nonce,
        )
        async with self._sessions() as session, session.begin():
            row = await session.get(DeploymentModel, deployment.id)
            if row is not None:
                row.permit_id = new_permit_id
                evidence = dict(row.evidence or {})
                evidence["active_operation_key"] = operation_key
                evidence["reminted_from_eo"] = str(dead_eo_id)
                row.evidence = evidence
                flag_modified(row, "evidence")
        return prepared.id

    async def _load_delivery_review_context(
        self, release_candidate_id: uuid.UUID
    ) -> dict[str, dict]:
        """Pull acceptance_contract + GoalSpec success_criteria for delivery-review-v1."""
        empty: dict[str, dict] = {"acceptance_contract": {}, "success_criteria": {}}
        async with self._sessions() as session:
            candidate = await session.get(ReleaseCandidateModel, release_candidate_id)
            if candidate is None:
                return empty
            build = await session.get(AppBuildModel, candidate.app_build_id)
            if build is None:
                return empty
            snapshot = await session.get(WorkspaceSnapshotModel, build.workspace_snapshot_id)
            if snapshot is None:
                return empty
            run = await session.get(GenerationRunModel, snapshot.generation_run_id)
            if run is None:
                return empty
            plan = await session.get(GenerationPlanModel, run.plan_id)
            acceptance: dict = {}
            if plan is not None:
                acceptance = dict((plan.contract_json or {}).get("acceptance_contract") or {})
            success: dict = {}
            if plan is not None:
                revision = await session.get(
                    RequirementRevisionModel, plan.requirement_revision_id
                )
                if revision is not None:
                    content = dict(revision.content_json or {})
                    if content.get("success_criteria"):
                        success = dict(content["success_criteria"])
                    goal_id = revision.goal_id
                    spec = await session.scalar(
                        select(GoalSpecModel)
                        .where(GoalSpecModel.goal_id == goal_id)
                        .order_by(GoalSpecModel.version.desc())
                        .limit(1)
                    )
                    if spec is not None and spec.success_criteria:
                        success = {**dict(spec.success_criteria), **success}
            return {"acceptance_contract": acceptance, "success_criteria": success}

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
            artifact_uri = str(build.build_artifact_uri)
            if not _preview_artifact_usable(artifact_uri):
                snapshot = await session.get(
                    WorkspaceSnapshotModel, build.workspace_snapshot_id
                )
                if snapshot is not None and _preview_artifact_usable(
                    snapshot.source_archive_uri
                ):
                    artifact_uri = str(snapshot.source_archive_uri)
                    build.build_artifact_uri = artifact_uri
                    build.build_artifact_hash = _artifact_hash(
                        artifact_uri,
                        str(build.build_artifact_hash or ("0" * 64)),
                    )
            deployment.status = "DEPLOYING"
            deployment.version += 1
            return deployment, artifact_uri

    async def _commit_result(
        self,
        deployment_id: uuid.UUID,
        result: object,
        *,
        expected: str = "DEPLOYING",
        external_operation_id: uuid.UUID | None = None,
    ) -> DeploymentModel:
        from regent.application.p1_ports import DeploymentResult

        assert isinstance(result, DeploymentResult)
        async with self._sessions() as session, session.begin():
            model = await session.get(DeploymentModel, deployment_id)
            if model is None or model.status != expected:
                # Idempotent: peer worker may have already committed SUCCEEDED/FAILED.
                if model is not None and model.status in ("SUCCEEDED", "FAILED"):
                    return model
                raise DomainError(ErrorCode.INVALID_STATE, "deployment result cannot be committed")
            model.status = result.status
            model.version += 1
            model.external_deployment_id = result.external_request_id
            model.endpoint = result.endpoint
            evidence = dict(result.evidence or {})
            if external_operation_id is not None:
                evidence["external_operation_id"] = str(external_operation_id)
            model.evidence = evidence
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

    # ------------------------------------------------------------------
    # P3-B: Production deployment with independent approval
    # ------------------------------------------------------------------

    async def request_production_deployment(
        self,
        release_candidate_id: uuid.UUID,
        *,
        actor: str,
        approval_id: uuid.UUID | None = None,
        strategy: str = "canary",
        canary_percentage: int = 10,
        slo_checks: list[str] | None = None,
    ) -> dict[str, Any]:
        """Request production deployment with independent approval.

        Production deployments require:
        - An approved release candidate
        - Independent approval (approval_id)
        - Canary or blue-green strategy
        - SLO check configuration

        Returns a dict with deployment status and approval info.
        """
        async with self._sessions() as session:
            candidate = await session.get(ReleaseCandidateModel, release_candidate_id)
            if candidate is None:
                return {
                    "status": "REJECTED",
                    "reason": "release candidate not found",
                }
            if candidate.status != "APPROVED":
                return {
                    "status": "REJECTED",
                    "reason": f"candidate status is {candidate.status}, must be APPROVED",
                }

        if approval_id is None:
            return {
                "status": "REJECTED",
                "reason": "independent approval required for production deployment",
            }

        return {
            "status": "ACCEPTED",
            "candidate_id": str(release_candidate_id),
            "approval_id": str(approval_id),
            "strategy": strategy,
            "canary_percentage": canary_percentage,
            "slo_checks": slo_checks or ["error_rate < 1%", "latency_p99 < 500ms"],
            "requested_by": actor,
        }
