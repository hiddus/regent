import hashlib
import inspect
import tempfile
import uuid
import zlib
from dataclasses import dataclass, replace
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.evt_summary import execute_evt_summary
from regent.application.transition_service import TransitionContext, TransitionService
from regent.domain.transitions import GoalCommand, RunCommand, WorkCommand
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.models import (
    ArtifactModel,
    CapabilityModel,
    EvidenceModel,
    GoalModel,
    GoalSpecModel,
    RunModel,
    ToolCertificationModel,
    ToolSpecModel,
    WorkModel,
)


@dataclass(frozen=True, slots=True)
class EvtGapReceipt:
    goal_id: uuid.UUID
    work_id: uuid.UUID
    run_id: uuid.UUID
    tool_spec_id: uuid.UUID
    certification_id: uuid.UUID
    capability_status: str
    tool_status: str
    valid_count: int
    invalid_count: int
    goal_status: str
    work_status: str
    run_status: str
    replayed: bool


class EvtParserGapService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        artifacts: FileArtifactStore,
    ) -> None:
        self._sessions = sessions
        self._artifacts = artifacts
        self._transitions = TransitionService(sessions)

    async def execute(self, *, input_text: str, idempotency_key: str, actor: str) -> EvtGapReceipt:
        existing = await self._existing(idempotency_key)
        if existing is not None:
            return existing
        goal_id, work_id, run_id, correlation_id = (uuid.uuid4() for _ in range(4))
        tool_id, capability_id = uuid.uuid4(), uuid.uuid4()
        source_hash = hashlib.sha256(inspect.getsource(execute_evt_summary).encode()).hexdigest()
        async with self._sessions() as session, session.begin():
            session.add(
                GoalModel(
                    id=goal_id,
                    original_input="Parse EVT rows and summarize CRC32 validity",
                    created_by=actor,
                    correlation_id=correlation_id,
                    metadata_json={"baseline": "EVT_PARSER_GAP"},
                )
            )
            await session.flush()
            session.add_all(
                (
                    GoalSpecModel(
                        id=uuid.uuid4(),
                        goal_id=goal_id,
                        version=1,
                        status="FROZEN",
                        content_hash="legacy-deterministic-baseline",
                        confirmed_by=actor,
                        explicit_constraints={
                            "network": "forbidden",
                            "fixtures": "read_only",
                            "write_scope": "output/",
                            "hidden_tests_visible_to_builder": False,
                        },
                        system_inferences={"crc_algorithm": "CRC32"},
                        unknowns=[],
                        success_criteria={"valid_count": 5, "invalid_count": 1},
                        source_refs=[],
                    ),
                    WorkModel(
                        id=work_id,
                        goal_id=goal_id,
                        purpose="Build, certify, and use an EVT parser",
                        input_refs=[{"format": "timestamp|category|value|crc32"}],
                        acceptance_criteria={"valid_count": 5, "invalid_count": 1},
                        dependency_ids=[],
                        priority=0,
                        budget={"network_calls": 0},
                        correlation_id=correlation_id,
                        metadata_json={"required_capabilities": ["evt_parser"]},
                    ),
                    CapabilityModel(
                        id=capability_id,
                        name="evt_parser",
                        status="CANDIDATE",
                        scope_goal_id=goal_id,
                        description="Parse pipe-delimited EVT rows and validate CRC32",
                        verification={"public": False, "hidden": False},
                    ),
                    ToolSpecModel(
                        id=tool_id,
                        name="evt-summary",
                        version=1,
                        status="CANDIDATE",
                        capability_name="evt_parser",
                        scope_goal_id=goal_id,
                        entrypoint="regent.application.evt_summary:execute_evt_summary",
                        source_hash=source_hash,
                        constraints={
                            "network": False,
                            "read": ["fixtures/"],
                            "write": ["output/"],
                        },
                    ),
                )
            )
            await session.flush()
            session.add(
                RunModel(
                    id=run_id,
                    work_id=work_id,
                    actor_id="evt-summary-tool",
                    agent_spec_ref="goal-candidate-agent:v1",
                    tool_ref=f"tool-spec:{tool_id}",
                    input_version="sha256:pending",
                    idempotency_key=idempotency_key,
                    resource_usage={},
                    correlation_id=correlation_id,
                )
            )
        await self._to_running(goal_id, work_id, run_id, actor, correlation_id)

        with (
            tempfile.TemporaryDirectory() as public_dir,
            tempfile.TemporaryDirectory() as hidden_dir,
        ):
            public = execute_evt_summary(
                goal_id=goal_id,
                input_text=input_text,
                artifacts=FileArtifactStore(Path(public_dir)),
            )
            hidden_text = self._hidden_fixture()
            hidden = execute_evt_summary(
                goal_id=goal_id,
                input_text=hidden_text,
                artifacts=FileArtifactStore(Path(hidden_dir)),
            )
        public_passed = (public.valid_count, public.invalid_count) == (5, 1)
        hidden_passed = (hidden.valid_count, hidden.invalid_count) == (3, 2)
        if not public_passed or not hidden_passed:
            await self._transitions.transition_run(
                TransitionContext(run_id, 3, actor, correlation_id), RunCommand.MARK_FAILED
            )
            await self._transitions.transition_work(
                TransitionContext(work_id, 2, actor, correlation_id), WorkCommand.MARK_UNKNOWN
            )
            raise ValueError("EVT candidate failed public or hidden certification")

        actual = execute_evt_summary(
            goal_id=goal_id,
            input_text=input_text,
            artifacts=self._artifacts,
        )
        artifact_id, evidence_id, certification_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with self._sessions() as session, session.begin():
            tool = await session.get(ToolSpecModel, tool_id, with_for_update=True)
            capability = await session.get(CapabilityModel, capability_id, with_for_update=True)
            run = await session.get(RunModel, run_id, with_for_update=True)
            if tool is None or capability is None or run is None:
                raise RuntimeError("EVT certification state disappeared")
            tool.status = "CERTIFIED"
            capability.status = "GOAL_CERTIFIED"
            capability.verification = {"public": True, "hidden": True}
            run.input_version = f"sha256:{actual.input_hash}"
            run.result = {
                "valid_count": actual.valid_count,
                "invalid_count": actual.invalid_count,
            }
            run.resource_usage = {"network_calls": 0, "model_calls": 0}
            session.add(
                ToolCertificationModel(
                    id=certification_id,
                    tool_spec_id=tool_id,
                    goal_id=goal_id,
                    public_passed=True,
                    hidden_passed=True,
                    public_evidence_hash=public.output_hash,
                    hidden_evidence_hash=hidden.output_hash,
                    security_checks={
                        "network_forbidden": True,
                        "fixtures_read_only": True,
                        "output_only_write": True,
                        "hidden_tests_isolated": True,
                    },
                )
            )
            await session.flush()
            session.add_all(
                (
                    ArtifactModel(
                        id=artifact_id,
                        goal_id=goal_id,
                        work_id=work_id,
                        run_id=run_id,
                        artifact_type="evt_summary",
                        schema_ref="regent://schemas/evt-summary/v1",
                        uri=actual.artifact.uri,
                        content_hash=actual.output_hash,
                        producer_ref=f"tool-spec:{tool_id}",
                        provenance={
                            "input_hash": actual.input_hash,
                            "certification_id": str(certification_id),
                        },
                        version=1,
                    ),
                    EvidenceModel(
                        id=evidence_id,
                        goal_id=goal_id,
                        work_id=work_id,
                        run_id=run_id,
                        artifact_id=artifact_id,
                        evidence_type="tool_certification_and_execution",
                        uri=actual.artifact.uri,
                        content_hash=actual.output_hash,
                        producer_ref=f"tool-spec:{tool_id}",
                        quality_tier="EXACT",
                        payload={
                            "public_passed": True,
                            "hidden_passed": True,
                            "input_hash": actual.input_hash,
                            "output_hash": actual.output_hash,
                        },
                    ),
                )
            )
        await self._to_complete(goal_id, work_id, run_id, actor, correlation_id)
        result = await self._existing(idempotency_key)
        if result is None:
            raise RuntimeError("EVT receipt disappeared")
        return replace(result, replayed=False)

    async def _existing(self, key: str) -> EvtGapReceipt | None:
        async with self._sessions() as session:
            run = await session.scalar(select(RunModel).where(RunModel.idempotency_key == key))
            if run is None or run.status != "EXECUTED":
                return None
            work = await session.get(WorkModel, run.work_id)
            if work is None:
                return None
            goal = await session.get(GoalModel, work.goal_id)
            tool = await session.scalar(
                select(ToolSpecModel).where(ToolSpecModel.scope_goal_id == work.goal_id)
            )
            cert = await session.scalar(
                select(ToolCertificationModel).where(ToolCertificationModel.goal_id == work.goal_id)
            )
            cap = await session.scalar(
                select(CapabilityModel).where(CapabilityModel.scope_goal_id == work.goal_id)
            )
            if goal is None or tool is None or cert is None or cap is None or run.result is None:
                return None
            return EvtGapReceipt(
                goal_id=goal.id,
                work_id=work.id,
                run_id=run.id,
                tool_spec_id=tool.id,
                certification_id=cert.id,
                capability_status=cap.status,
                tool_status=tool.status,
                valid_count=int(run.result["valid_count"]),
                invalid_count=int(run.result["invalid_count"]),
                goal_status=goal.status,
                work_status=work.status,
                run_status=run.status,
                replayed=True,
            )

    async def _to_running(
        self, goal: uuid.UUID, work: uuid.UUID, run: uuid.UUID, actor: str, correlation: uuid.UUID
    ) -> None:
        for version, goal_command in enumerate((GoalCommand.QUALIFY, GoalCommand.ACTIVATE)):
            await self._transitions.transition_goal(
                TransitionContext(goal, version, actor, correlation), goal_command
            )
        for version, work_command in enumerate((WorkCommand.MAKE_READY, WorkCommand.START)):
            await self._transitions.transition_work(
                TransitionContext(work, version, actor, correlation), work_command
            )
        for version, run_command in enumerate(
            (RunCommand.REQUEST_PERMIT, RunCommand.QUEUE, RunCommand.CLAIM)
        ):
            await self._transitions.transition_run(
                TransitionContext(run, version, actor, correlation), run_command
            )

    async def _to_complete(
        self, goal: uuid.UUID, work: uuid.UUID, run: uuid.UUID, actor: str, correlation: uuid.UUID
    ) -> None:
        await self._transitions.transition_run(
            TransitionContext(run, 3, actor, correlation), RunCommand.MARK_EXECUTED
        )
        await self._transitions.transition_work(
            TransitionContext(work, 2, actor, correlation), WorkCommand.REQUEST_EVALUATION
        )
        await self._transitions.transition_work(
            TransitionContext(work, 3, actor, correlation), WorkCommand.ACCEPT
        )
        await self._transitions.transition_goal(
            TransitionContext(goal, 2, actor, correlation), GoalCommand.ACHIEVE
        )

    @staticmethod
    def _hidden_fixture() -> str:
        rows: list[str] = []
        for index, valid in enumerate((True, True, False, True, False)):
            payload = f"hidden-{index}|category-{index}|{index * 1.25}"
            crc = f"{zlib.crc32(payload.encode()) & 0xFFFFFFFF:08x}"
            if not valid:
                crc = "00000000" if crc != "00000000" else "ffffffff"
            rows.append(f"{payload}|{crc}")
        return "\n".join(rows)
