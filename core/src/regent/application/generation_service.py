import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from regent.application.p1_contracts import FileChangeSet, GenerationPlanContract, canonical_hash
from regent.application.p1_ports import FileChangeSetGenerator
from regent.application.generator_metadata import assert_generator_consistency
from regent.application.generation_strategy_policy import resolve_effective_generation_strategy
from regent.application.planned_path_policy import expand_planned_paths
from regent.config import get_settings
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    CapabilityResolutionPlanModel,
    FileChangeSetModel,
    GenerationPlanModel,
    GenerationRunModel,
    GoalModel,
    RequirementRevisionModel,
    WorkspaceSnapshotModel,
)
from regent.infrastructure.workspace_writer import WorkspaceCommit, WorkspaceWriter

# Only reopen GENERATING after this idle window (worker crash / lost lease).
_STALE_GENERATING = timedelta(minutes=20)


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
                # Digest hit on a terminal plan: reopen so DeliveryGapRecovery /
                # GenerationRunRequested can request a new run instead of
                # INVALID_STATE "frozen generation plan is required".
                if existing.status in {"COMPLETED", "FAILED"}:
                    existing.status = "FROZEN"
                    existing.version += 1
                self._expand_plan_contract(existing)
                await session.flush()
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

    @staticmethod
    def _reopen_plan_for_run(
        plan: GenerationPlanModel, *, allow_executing: bool = False
    ) -> None:
        """Re-FROZEN terminal plans so a new / retried run can start.

        ``allow_executing`` is only for same-idempotency crash reclaim of a
        FAILED/stale GENERATING run whose plan was left EXECUTING.
        """
        reopenable = {"COMPLETED", "FAILED"}
        if allow_executing:
            reopenable = {*reopenable, "EXECUTING"}
        if plan.status in reopenable:
            plan.status = "FROZEN"
            plan.version += 1
            GenerationService._expand_plan_contract(plan)
        elif plan.status != "FROZEN":
            raise DomainError(ErrorCode.INVALID_STATE, "frozen generation plan is required")
        else:
            GenerationService._expand_plan_contract(plan)

    @staticmethod
    def _expand_plan_contract(plan: GenerationPlanModel) -> None:
        """Persist scaffold allowlist into frozen plan so LLM + validators share one set."""
        contract = dict(plan.contract_json or {})
        acceptance = contract.get("acceptance_contract") or {}
        scale = ""
        if isinstance(acceptance, dict):
            scale = str(acceptance.get("goal_scale") or "")
        expanded = expand_planned_paths(contract.get("planned_paths") or [], goal_scale=scale)
        if list(contract.get("planned_paths") or []) == expanded:
            return
        contract["planned_paths"] = expanded
        plan.contract_json = contract
        flag_modified(plan, "contract_json")
        plan.version += 1

    async def _next_attempt_for_plan(
        self, session: AsyncSession, plan_id: uuid.UUID
    ) -> int:
        return (
            int(
                await session.scalar(
                    select(func.coalesce(func.max(GenerationRunModel.attempt), 0)).where(
                        GenerationRunModel.plan_id == plan_id
                    )
                )
                or 0
            )
            + 1
        )

    async def request_run(self, command: RequestGenerationRun) -> GenerationRunModel:
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(GenerationRunModel).where(
                    GenerationRunModel.idempotency_key == command.idempotency_key
                )
            )
            if existing is not None:
                if existing.plan_id != command.plan_id:
                    # Same outbox/dispatch key, evolved plan digest (e.g. failure
                    # lessons / envelopes appended between retries). Rebind instead
                    # of INVALID_STATE — that used to feed DeliveryGapRecovery and
                    # create an infinite invalid_state → replan storm.
                    if existing.status == "GENERATING":
                        touched = existing.updated_at or existing.created_at
                        if touched.tzinfo is None:
                            touched = touched.replace(tzinfo=UTC)
                        if datetime.now(UTC) - touched < _STALE_GENERATING:
                            raise DomainError(
                                ErrorCode.LEASE_CONFLICT,
                                "generation run already in progress",
                            )
                    plan = await session.get(GenerationPlanModel, command.plan_id)
                    if plan is None:
                        raise DomainError(
                            ErrorCode.INVALID_STATE, "frozen generation plan is required"
                        )
                    self._reopen_plan_for_run(plan, allow_executing=False)
                    existing.plan_id = plan.id
                    existing.attempt = await self._next_attempt_for_plan(session, plan.id)
                    existing.status = "REQUESTED"
                    existing.version += 1
                    await session.flush()
                    return existing
                if existing.status == "COMPLETED":
                    return existing
                if existing.status == "GENERATING":
                    touched = existing.updated_at or existing.created_at
                    if touched.tzinfo is None:
                        touched = touched.replace(tzinfo=UTC)
                    if datetime.now(UTC) - touched < _STALE_GENERATING:
                        # In-flight under another dispatch lease — do not reopen.
                        raise DomainError(
                            ErrorCode.LEASE_CONFLICT,
                            "generation run already in progress",
                        )
                    # Stale GENERATING after worker crash: reopen.
                elif existing.status != "FAILED":
                    return existing
                # Crash/retry recovery for FAILED or stale GENERATING.
                plan = await session.get(GenerationPlanModel, existing.plan_id)
                if plan is None:
                    raise DomainError(ErrorCode.INVALID_STATE, "generation plan missing")
                self._reopen_plan_for_run(plan, allow_executing=True)
                existing.status = "REQUESTED"
                existing.version += 1
                await session.flush()
                return existing
            plan = await session.get(GenerationPlanModel, command.plan_id)
            if plan is None:
                raise DomainError(ErrorCode.INVALID_STATE, "frozen generation plan is required")
            # Parallel GenerationRunRequested (recovery storm / concurrency) can
            # digest-hit a plan still EXECUTING under another run. Treat as lease
            # conflict so outbox retries — do not INVALID_STATE → DeliveryGapRecovery.
            if plan.status == "EXECUTING":
                raise DomainError(
                    ErrorCode.LEASE_CONFLICT,
                    "generation plan already executing",
                )
            # New idempotency key (e.g. delivery-gap recovery): reopen COMPLETED
            # plans that create_plan reused via input_digest hit.
            self._reopen_plan_for_run(plan, allow_executing=False)
            attempt = await self._next_attempt_for_plan(session, plan.id)
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
        self,
        run_id: uuid.UUID,
        *,
        base_workspace: Path | None = None,
        on_progress: Any = None,
    ) -> WorkspaceSnapshotModel:
        try:
            plan_payload = await self._claim(run_id)
            await self._overlay_live_goal_steers(plan_payload)
            if base_workspace is not None:
                # Agentic generator reads base_workspace from the plan dict.
                plan_payload["base_workspace"] = str(base_workspace)
            # GQ-1/GQ-3: select the concrete generator for this goal before any
            # consistency check or generation call. A GeneratorSelector resolves
            # the per-goal effective strategy; a plain generator is used directly.
            settings = get_settings()
            goal_id = None
            acceptance = plan_payload.get("acceptance_contract") or {}
            if isinstance(acceptance, dict):
                goal_id = acceptance.get("goal_id")
            gen = self._generator
            if hasattr(gen, "select"):
                gen = gen.select(str(goal_id) if goal_id else None)
            strategy = resolve_effective_generation_strategy(
                settings, goal_id=str(goal_id) if goal_id else None
            )
            assert_generator_consistency(
                strategy=strategy,
                generator=gen,
                plan_id=str(plan_payload.get("plan_id") or plan_payload.get("id") or ""),
                run_id=str(run_id),
                contract_generator_ref=str(plan_payload.get("generator_ref") or "") or None,
                contract_prompt_version=str(plan_payload.get("prompt_version") or "") or None,
            )
            generate = gen.generate
            try:
                generated = await generate(plan_payload, on_progress=on_progress)
            except TypeError:
                # Older generators without on_progress kwarg.
                generated = await generate(plan_payload)
            changes = generated.output
            from regent.application.planned_path_policy import is_path_within_frozen_plan

            # planned_paths already expanded + persisted in _claim; re-expand is idempotent.
            planned_paths = set(
                expand_planned_paths(
                    plan_payload.get("planned_paths") or [],
                    goal_scale=str(
                        (plan_payload.get("acceptance_contract") or {}).get("goal_scale") or ""
                    ),
                )
            )
            if planned_paths and any(
                not is_path_within_frozen_plan(change.relative_path, planned_paths)
                for change in changes.changes
            ):
                raise DomainError(ErrorCode.POLICY_DENIED, "generated path is outside frozen plan")
            if not changes.changes:
                raise DomainError(
                    ErrorCode.POLICY_DENIED,
                    "generated changeset empty after planned-path filter",
                )
            commit = self._writer.apply(str(run_id), changes, base_workspace=base_workspace)
            accepted = getattr(generated, "accepted_workspace", None)
            if isinstance(accepted, dict) and accepted.get("uri"):
                await self._remember_accepted_workspace(
                    goal_id=str(goal_id) if goal_id else None,
                    accepted=accepted,
                )
            return await self._complete(
                run_id,
                changes,
                commit,
                generated.model_ref,
                generated.input_tokens,
                generated.output_tokens,
                str(plan_payload["runtime_profile_hash"]),
            )
        except DomainError as exc:
            await self._fail(run_id, failure_code=exc.code.value)
            raise
        except Exception as exc:
            await self._fail(
                run_id,
                failure_code=type(exc).__name__[:64] or "GENERATION_EXECUTION_FAILED",
            )
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
            self._expand_plan_contract(plan)
            payload = dict(plan.contract_json)
            # Bind attempt so agent transcript / review can attach to this run.
            payload["generation_run_id"] = str(run.id)
            payload["plan_id"] = str(plan.id)
            return payload

    async def _overlay_live_goal_steers(self, plan_payload: dict[str, Any]) -> None:
        """Merge Hive PM / human steer written after the plan was frozen."""
        acceptance = plan_payload.get("acceptance_contract")
        if not isinstance(acceptance, dict):
            return
        raw_gid = acceptance.get("goal_id") or plan_payload.get("goal_id")
        if not raw_gid:
            return
        try:
            gid = uuid.UUID(str(raw_gid))
        except ValueError:
            return
        async with self._sessions() as session:
            goal = await session.get(GoalModel, gid)
            if goal is None:
                return
            meta = dict(goal.metadata_json or {})
        steer = meta.get("session_steer_brief")
        if steer:
            acceptance["session_steer_brief"] = str(steer)
            plan_payload["session_steer_brief"] = str(steer)
        hive = meta.get("hive_generation")
        if isinstance(hive, dict) and hive.get("pm_plan"):
            plan_payload["hive_pm_plan"] = hive.get("pm_plan")
            acceptance["hive_pm_plan"] = hive.get("pm_plan")

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

    async def _remember_accepted_workspace(
        self,
        *,
        goal_id: str | None,
        accepted: dict[str, Any],
    ) -> None:
        """Persist accepted snapshot URI on the Goal for P0-5 REVISE cloning."""
        if not goal_id:
            return
        try:
            gid = uuid.UUID(str(goal_id))
        except ValueError:
            return
        uri = str(accepted.get("uri") or "").strip()
        if not uri:
            return
        async with self._sessions() as session, session.begin():
            goal = await session.get(GoalModel, gid)
            if goal is None:
                return
            meta = dict(goal.metadata_json or {})
            meta["last_accepted_workspace_uri"] = uri
            meta["last_accepted_workspace"] = {
                "uri": uri,
                "snapshot_id": accepted.get("snapshot_id"),
                "content_hash": accepted.get("content_hash"),
                "manifest_hash": accepted.get("manifest_hash"),
                "profile_hash": accepted.get("profile_hash"),
                "verification_hash": accepted.get("verification_hash"),
                "created_at": accepted.get("created_at"),
            }
            goal.metadata_json = meta
            flag_modified(goal, "metadata_json")

    async def _fail(self, run_id: uuid.UUID, *, failure_code: str | None = None) -> None:
        async with self._sessions() as session, session.begin():
            run = await session.get(GenerationRunModel, run_id)
            if run is not None and run.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
                run.status = "FAILED"
                run.failure_code = (failure_code or "GENERATION_EXECUTION_FAILED")[:128]
                run.version += 1
                plan = await session.get(GenerationPlanModel, run.plan_id)
                if plan is not None and plan.status == "EXECUTING":
                    plan.status = "FAILED"
                    plan.version += 1
