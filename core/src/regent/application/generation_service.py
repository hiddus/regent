import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.p1_contracts import FileChangeSet, GenerationPlanContract, canonical_hash
from regent.application.p1_ports import FileChangeSetGenerator
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    CapabilityResolutionPlanModel,
    FileChangeSetModel,
    GenerationPlanModel,
    GenerationRunModel,
    RequirementRevisionModel,
    WorkspaceSnapshotModel,
)
from regent.infrastructure.workspace_writer import WorkspaceCommit, WorkspaceWriter


@dataclass(frozen=True, slots=True)
class CreateGenerationPlan:
    requirement_revision_id: uuid.UUID
    capability_resolution_plan_id: uuid.UUID
    contract: GenerationPlanContract
    architecture_summary: str
    component_plan: list[dict[str, Any]]
    actor: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class RequestGenerationRun:
    plan_id: uuid.UUID
    idempotency_key: str
    correlation_id: str


class GenerationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        generator: FileChangeSetGenerator,
        writer: WorkspaceWriter,
    ) -> None:
        self._sessions = sessions
        self._generator = generator
        self._writer = writer

    async def create_plan(self, command: CreateGenerationPlan) -> GenerationPlanModel:
        digest = canonical_hash(command.contract)
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(GenerationPlanModel).where(GenerationPlanModel.input_digest == digest)
            )
            if existing is not None:
                return existing
            requirement = await session.get(
                RequirementRevisionModel, command.requirement_revision_id
            )
            resolution = await session.get(
                CapabilityResolutionPlanModel, command.capability_resolution_plan_id
            )
            if requirement is None or requirement.status != "VALIDATED":
                raise DomainError(ErrorCode.INVALID_STATE, "validated requirement is required")
            if (
                resolution is None
                or resolution.requirement_revision_id != requirement.id
                or resolution.status != "SATISFIED"
            ):
                raise DomainError(ErrorCode.INVALID_STATE, "satisfied resolution plan is required")
            model = GenerationPlanModel(
                id=uuid.uuid4(),
                requirement_revision_id=requirement.id,
                capability_resolution_plan_id=resolution.id,
                status="FROZEN",
                version=1,
                input_digest=digest,
                contract_json=command.contract.model_dump(mode="json"),
                architecture_summary=command.architecture_summary,
                component_plan=command.component_plan,
                created_by=command.actor,
                correlation_id=command.correlation_id,
            )
            session.add(model)
            await session.flush()
            return model

    async def request_run(self, command: RequestGenerationRun) -> GenerationRunModel:
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(GenerationRunModel).where(
                    GenerationRunModel.idempotency_key == command.idempotency_key
                )
            )
            if existing is not None:
                if existing.plan_id != command.plan_id:
                    raise DomainError(ErrorCode.INVALID_STATE, "idempotency key scope mismatch")
                return existing
            plan = await session.get(GenerationPlanModel, command.plan_id)
            if plan is None or plan.status != "FROZEN":
                raise DomainError(ErrorCode.INVALID_STATE, "frozen generation plan is required")
            attempt = (
                int(
                    await session.scalar(
                        select(func.coalesce(func.max(GenerationRunModel.attempt), 0)).where(
                            GenerationRunModel.plan_id == plan.id
                        )
                    )
                    or 0
                )
                + 1
            )
            run = GenerationRunModel(
                id=uuid.uuid4(),
                plan_id=plan.id,
                attempt=attempt,
                status="REQUESTED",
                version=0,
                idempotency_key=command.idempotency_key,
                correlation_id=command.correlation_id,
            )
            session.add(run)
            await session.flush()
            return run

    async def get_run(self, run_id: uuid.UUID) -> GenerationRunModel:
        async with self._sessions() as session:
            model = await session.get(GenerationRunModel, run_id)
            if model is None:
                raise DomainError(ErrorCode.NOT_FOUND, "generation run not found")
            return model

    async def execute(
        self, run_id: uuid.UUID, *, base_workspace: Path | None = None
    ) -> WorkspaceSnapshotModel:
        plan_payload = await self._claim(run_id)
        try:
            generated = await self._generator.generate(plan_payload)
            changes = generated.output
            planned_paths = set(plan_payload.get("planned_paths", []))
            if planned_paths and any(
                change.relative_path not in planned_paths for change in changes.changes
            ):
                raise DomainError(ErrorCode.POLICY_DENIED, "generated path is outside frozen plan")
            commit = self._writer.apply(str(run_id), changes, base_workspace=base_workspace)
            return await self._complete(
                run_id,
                changes,
                commit,
                generated.model_ref,
                generated.input_tokens,
                generated.output_tokens,
                str(plan_payload["runtime_profile_hash"]),
            )
        except Exception:
            await self._fail(run_id)
            raise

    async def _claim(self, run_id: uuid.UUID) -> dict[str, Any]:
        async with self._sessions() as session, session.begin():
            run = await session.scalar(
                select(GenerationRunModel).where(GenerationRunModel.id == run_id).with_for_update()
            )
            if run is None or run.status != "REQUESTED":
                raise DomainError(ErrorCode.INVALID_STATE, "generation run is not requestable")
            plan = await session.get(GenerationPlanModel, run.plan_id)
            if plan is None or plan.status != "FROZEN":
                raise DomainError(ErrorCode.INVALID_STATE, "generation plan is not executable")
            run.status = "GENERATING"
            run.version += 2
            plan.status = "EXECUTING"
            plan.version += 1
            return dict(plan.contract_json)

    async def _complete(
        self,
        run_id: uuid.UUID,
        changes: FileChangeSet,
        commit: WorkspaceCommit,
        model_ref: str,
        input_tokens: int,
        output_tokens: int,
        runtime_profile_hash: str,
    ) -> WorkspaceSnapshotModel:
        content = changes.model_dump(mode="json")
        digest = canonical_hash(content)
        async with self._sessions() as session, session.begin():
            run = await session.scalar(
                select(GenerationRunModel).where(GenerationRunModel.id == run_id).with_for_update()
            )
            if run is None or run.status != "GENERATING":
                raise DomainError(ErrorCode.INVALID_STATE, "generation run cannot commit")
            plan = await session.get(GenerationPlanModel, run.plan_id)
            run.status = "COMPLETED"
            run.version += 3
            run.model_ref = model_ref
            run.input_tokens = input_tokens
            run.output_tokens = output_tokens
            run.change_set_digest = digest
            assert plan is not None
            plan.status = "COMPLETED"
            plan.version += 1
            session.add(
                FileChangeSetModel(
                    id=uuid.uuid4(),
                    generation_run_id=run.id,
                    schema_version=changes.schema_version,
                    content_json=content,
                    content_hash=digest,
                    generator_ref=changes.generator_ref,
                    prompt_version=changes.prompt_version,
                )
            )
            snapshot = WorkspaceSnapshotModel(
                id=uuid.uuid4(),
                generation_run_id=run.id,
                manifest_uri=commit.manifest_path.as_uri(),
                manifest_hash=commit.manifest_hash,
                source_archive_uri=commit.source_archive_path.as_uri(),
                source_hash=commit.source_hash,
                workspace_locator=str(commit.workspace_path),
                file_count=commit.file_count,
                total_bytes=commit.total_bytes,
                runtime_profile_hash=runtime_profile_hash,
            )
            session.add(snapshot)
            await session.flush()
            return snapshot

    async def _fail(self, run_id: uuid.UUID) -> None:
        async with self._sessions() as session, session.begin():
            run = await session.get(GenerationRunModel, run_id)
            if run is not None and run.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
                run.status = "FAILED"
                run.failure_code = "GENERATION_EXECUTION_FAILED"
                run.version += 1
                plan = await session.get(GenerationPlanModel, run.plan_id)
                if plan is not None and plan.status == "EXECUTING":
                    plan.status = "FAILED"
                    plan.version += 1
