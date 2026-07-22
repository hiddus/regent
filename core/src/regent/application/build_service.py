import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.p1_ports import (
    DependencyMaterializationRequest,
    DependencyMaterializer,
    SandboxBuildRequest,
    SandboxDriver,
)
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AppBuildModel,
    DependencyResolutionModel,
    VerificationReportModel,
    WorkspaceSnapshotModel,
)


@dataclass(frozen=True, slots=True)
class RequestDependencyResolution:
    workspace_snapshot_id: uuid.UUID
    dependency_intents: list[dict[str, object]]
    idempotency_key: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class RequestAppBuild:
    workspace_snapshot_id: uuid.UUID
    dependency_resolution_id: uuid.UUID
    idempotency_key: str
    correlation_id: str


class BuildService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        materializer: DependencyMaterializer,
        sandbox: SandboxDriver,
    ) -> None:
        self._sessions = sessions
        self._materializer = materializer
        self._sandbox = sandbox

    async def request_dependencies(
        self, command: RequestDependencyResolution
    ) -> DependencyResolutionModel:
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(DependencyResolutionModel).where(
                    DependencyResolutionModel.idempotency_key == command.idempotency_key
                )
            )
            if existing is not None:
                if existing.workspace_snapshot_id != command.workspace_snapshot_id:
                    raise DomainError(ErrorCode.INVALID_STATE, "idempotency key scope mismatch")
                return existing
            snapshot = await session.get(WorkspaceSnapshotModel, command.workspace_snapshot_id)
            if snapshot is None:
                raise DomainError(ErrorCode.NOT_FOUND, "workspace snapshot not found")
            model = DependencyResolutionModel(
                id=uuid.uuid4(),
                workspace_snapshot_id=snapshot.id,
                status="REQUESTED",
                version=0,
                idempotency_key=command.idempotency_key,
                dependency_intents=command.dependency_intents,
                evidence={},
                correlation_id=command.correlation_id,
            )
            session.add(model)
            await session.flush()
            return model

    async def materialize_dependencies(
        self, resolution_id: uuid.UUID, *, permit_id: str, runtime_profile_ref: str
    ) -> DependencyResolutionModel:
        resolution, snapshot = await self._claim_resolution(resolution_id)
        # Idempotent: if already materialized, return directly
        if resolution.status == "MATERIALIZED":
            return resolution
        # Allow retry if previously failed or unknown
        if resolution.status in ("FAILED", "UNKNOWN"):
            # Reset to RESOLVING for retry
            async with self._sessions() as session, session.begin():
                model = await session.get(DependencyResolutionModel, resolution_id)
                if model is not None:
                    model.status = "RESOLVING"
                    model.version += 1
                    resolution = model
        try:
            result = await self._materializer.materialize(
                DependencyMaterializationRequest(
                    source_hash=snapshot.source_hash,
                    dependency_intents=resolution.dependency_intents,
                    runtime_profile_ref=runtime_profile_ref,
                    permit_id=permit_id,
                    idempotency_key=resolution.idempotency_key,
                    correlation_id=resolution.correlation_id,
                )
            )
        except Exception:
            await self._mark_resolution_unknown(resolution_id)
            raise
        async with self._sessions() as session, session.begin():
            model = await session.get(DependencyResolutionModel, resolution_id)
            assert model is not None
            if model.status != "RESOLVING":
                raise DomainError(ErrorCode.INVALID_STATE, "dependency result cannot be committed")
            model.status = result.status
            model.version += 1
            model.lockfile_uri = result.lockfile_uri
            model.bundle_uri = result.bundle_uri
            model.bundle_hash = result.bundle_hash
            model.sbom_uri = result.sbom_uri
            model.evidence = result.evidence
            model.failure_code = result.failure_code
            return model

    async def request_build(self, command: RequestAppBuild) -> AppBuildModel:
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(AppBuildModel).where(
                    AppBuildModel.idempotency_key == command.idempotency_key
                )
            )
            if existing is not None:
                return existing
            resolution = await session.get(
                DependencyResolutionModel, command.dependency_resolution_id
            )
            if (
                resolution is None
                or resolution.workspace_snapshot_id != command.workspace_snapshot_id
                or resolution.status != "MATERIALIZED"
            ):
                raise DomainError(ErrorCode.INVALID_STATE, "materialized dependencies are required")
            build = AppBuildModel(
                id=uuid.uuid4(),
                workspace_snapshot_id=command.workspace_snapshot_id,
                dependency_resolution_id=resolution.id,
                status="QUEUED",
                version=0,
                idempotency_key=command.idempotency_key,
                reconciliation_required=False,
                correlation_id=command.correlation_id,
            )
            session.add(build)
            await session.flush()
            return build

    async def get_build(self, build_id: uuid.UUID) -> AppBuildModel:
        async with self._sessions() as session:
            model = await session.get(AppBuildModel, build_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "app build not found")
            return model

    async def execute_build(
        self, build_id: uuid.UUID, *, runtime_profile_ref: str
    ) -> AppBuildModel:
        # Idempotent: check if build already completed
        async with self._sessions() as session, session.begin():
            existing = await session.get(AppBuildModel, build_id)
            if existing is not None and existing.status in ("PASSED", "FAILED", "UNKNOWN"):
                return existing
        build, snapshot, resolution = await self._claim_build(build_id)
        assert resolution.bundle_uri is not None and resolution.bundle_hash is not None
        try:
            result = await self._sandbox.build(
                SandboxBuildRequest(
                    workspace_snapshot_uri=snapshot.source_archive_uri,
                    dependency_bundle_uri=resolution.bundle_uri,
                    dependency_bundle_hash=resolution.bundle_hash,
                    runtime_profile_ref=runtime_profile_ref,
                    runtime_profile_hash=snapshot.runtime_profile_hash,
                    idempotency_key=build.idempotency_key,
                    correlation_id=build.correlation_id,
                )
            )
        except Exception:
            await self._mark_build_unknown(build_id)
            raise
        async with self._sessions() as session, session.begin():
            model = await session.get(AppBuildModel, build_id)
            assert model is not None
            if model.status != "RUNNING":
                raise DomainError(ErrorCode.INVALID_STATE, "build result cannot be committed")
            model.status = result.status
            model.version += 1
            model.external_operation_id = result.external_request_id
            model.build_artifact_uri = result.build_artifact_uri
            model.build_artifact_hash = result.build_artifact_hash
            model.log_uri = result.evidence_artifact_uri
            model.reconciliation_required = result.status == "UNKNOWN"
            if result.status in {"PASSED", "FAILED"}:
                session.add(
                    VerificationReportModel(
                        id=uuid.uuid4(),
                        app_build_id=model.id,
                        passed=result.status == "PASSED",
                        checks=result.checks,
                        evidence_uri=result.evidence_artifact_uri,
                        evidence_hash=result.evidence_hash,
                        runtime_profile_hash=snapshot.runtime_profile_hash,
                    )
                )
            return model

    async def reconcile_build(self, build_id: uuid.UUID) -> AppBuildModel:
        async with self._sessions() as session:
            build = await session.get(AppBuildModel, build_id)
            if build is None or build.status != "UNKNOWN" or build.external_operation_id is None:
                raise DomainError(ErrorCode.INVALID_STATE, "build does not require reconciliation")
            external_id = build.external_operation_id
        result = await self._sandbox.query(external_id)
        async with self._sessions() as session, session.begin():
            model = await session.scalar(
                select(AppBuildModel).where(AppBuildModel.id == build_id).with_for_update()
            )
            assert model is not None
            if model.status != "UNKNOWN":
                raise DomainError(ErrorCode.INVALID_STATE, "build is no longer unknown")
            if result.status == "UNKNOWN":
                return model
            snapshot = await session.get(WorkspaceSnapshotModel, model.workspace_snapshot_id)
            assert snapshot is not None
            model.status = result.status
            model.version += 1
            model.build_artifact_uri = result.build_artifact_uri
            model.build_artifact_hash = result.build_artifact_hash
            model.log_uri = result.evidence_artifact_uri
            model.reconciliation_required = False
            model.failure_code = None if result.status == "PASSED" else "RECONCILED_FAILED"
            session.add(
                VerificationReportModel(
                    id=uuid.uuid4(),
                    app_build_id=model.id,
                    passed=result.status == "PASSED",
                    checks=result.checks,
                    evidence_uri=result.evidence_artifact_uri,
                    evidence_hash=result.evidence_hash,
                    runtime_profile_hash=snapshot.runtime_profile_hash,
                )
            )
            return model

    async def _mark_resolution_unknown(self, resolution_id: uuid.UUID) -> None:
        async with self._sessions() as session, session.begin():
            model = await session.get(DependencyResolutionModel, resolution_id)
            if model is not None and model.status == "RESOLVING":
                model.status = "UNKNOWN"
                model.failure_code = "UNKNOWN_RESULT"
                model.version += 1

    async def _mark_build_unknown(self, build_id: uuid.UUID) -> None:
        async with self._sessions() as session, session.begin():
            model = await session.get(AppBuildModel, build_id)
            if model is not None and model.status == "RUNNING":
                model.status = "UNKNOWN"
                model.failure_code = "UNKNOWN_RESULT"
                model.reconciliation_required = True
                model.version += 1

    async def _claim_resolution(
        self, resolution_id: uuid.UUID
    ) -> tuple[DependencyResolutionModel, WorkspaceSnapshotModel]:
        async with self._sessions() as session, session.begin():
            model = await session.scalar(
                select(DependencyResolutionModel)
                .where(DependencyResolutionModel.id == resolution_id)
                .with_for_update()
            )
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "dependency resolution not found")
            # Idempotent: if already resolved or resolving, return as-is
            if model.status in ("RESOLVING", "MATERIALIZED", "FAILED", "UNKNOWN"):
                snapshot = await session.get(WorkspaceSnapshotModel, model.workspace_snapshot_id)
                assert snapshot is not None
                return model, snapshot
            if model.status != "REQUESTED":
                raise DomainError(
                    ErrorCode.INVALID_STATE, "dependency resolution is not requestable"
                )
            snapshot = await session.get(WorkspaceSnapshotModel, model.workspace_snapshot_id)
            assert snapshot is not None
            model.status = "RESOLVING"
            model.version += 1
            return model, snapshot

    async def _claim_build(
        self, build_id: uuid.UUID
    ) -> tuple[AppBuildModel, WorkspaceSnapshotModel, DependencyResolutionModel]:
        async with self._sessions() as session, session.begin():
            model = await session.scalar(
                select(AppBuildModel).where(AppBuildModel.id == build_id).with_for_update()
            )
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "app build not found")
            # Idempotent: if already running or terminal, return as-is
            if model.status in ("RUNNING", "PASSED", "FAILED", "UNKNOWN"):
                snapshot = await session.get(WorkspaceSnapshotModel, model.workspace_snapshot_id)
                resolution = await session.get(
                    DependencyResolutionModel, model.dependency_resolution_id
                )
                assert snapshot is not None and resolution is not None
                return model, snapshot, resolution
            if model.status != "QUEUED":
                raise DomainError(ErrorCode.INVALID_STATE, "build is not queued")
            snapshot = await session.get(WorkspaceSnapshotModel, model.workspace_snapshot_id)
            resolution = await session.get(
                DependencyResolutionModel, model.dependency_resolution_id
            )
            assert snapshot is not None and resolution is not None
            model.status = "RUNNING"
            model.version += 1
            return model, snapshot, resolution
