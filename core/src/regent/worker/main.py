import asyncio
import logging
import os
import signal
import socket
import uuid
from contextlib import suppress
from pathlib import Path
from time import monotonic

from regent.application.execution_orchestrator import (
    ExecutionOrchestrator,
    get_p1_event_handlers,
)
from regent.application.human_task_service import HumanTaskService
from regent.application.permit_service import PermitService
from regent.config import get_settings
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.code_generator import ArtifactBackedCodeGenerator, ArtifactUriResolver
from regent.infrastructure.database import create_engine, create_session_factory
from regent.infrastructure.deployment import StaticPreviewDeploymentProvider
from regent.infrastructure.evidence_sources import GoalIntentEvidenceConnector
from regent.infrastructure.sandbox import DockerDependencyMaterializer, DockerSandboxDriver
from regent.infrastructure.workspace_writer import WorkspaceWriter
from regent.model import ModelConfigurationError
from regent.model.factory import build_model_provider
from regent.runtime.dispatcher import OutboxDispatcher
from regent.runtime.timers import DurableTimerService
from regent.runtime.worker_leases import WorkerLeaseService

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        *,
        worker_id: str,
        dispatcher: OutboxDispatcher,
        leases: WorkerLeaseService,
        timers: DurableTimerService | None = None,
        permits: PermitService | None = None,
        human_tasks: HumanTaskService | None = None,
        poll_seconds: float,
        heartbeat_seconds: float,
    ) -> None:
        self.worker_id = worker_id
        self.dispatcher = dispatcher
        self.leases = leases
        self.timers = timers
        self.permits = permits
        self.human_tasks = human_tasks
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._stopping = asyncio.Event()

    async def serve(self) -> None:
        lease = await self.leases.acquire(
            self.worker_id,
            metadata={"hostname": socket.gethostname(), "pid": os.getpid()},
        )
        next_heartbeat = monotonic() + self.heartbeat_seconds
        logger.info("worker lease acquired", extra={"worker_id": self.worker_id})
        try:
            while not self._stopping.is_set():
                if self.permits is not None:
                    await self.permits.expire_due()
                if self.human_tasks is not None:
                    await self.human_tasks.timeout_due()
                if self.timers is not None:
                    await self.timers.dispatch_due(self.worker_id)
                await self.dispatcher.dispatch_once(self.worker_id)
                if monotonic() >= next_heartbeat:
                    lease = await self.leases.heartbeat(lease)
                    next_heartbeat = monotonic() + self.heartbeat_seconds
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    continue
        finally:
            with suppress(Exception):
                await self.leases.release(lease)
            logger.info("worker stopped", extra={"worker_id": self.worker_id})

    def stop(self) -> None:
        self._stopping.set()


async def log_state_change(payload: dict[str, object]) -> None:
    logger.info("state change dispatched", extra={"event": payload})


def create_worker() -> tuple[Worker, object]:
    settings = get_settings()
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    leases = WorkerLeaseService(
        sessions,
        lease_seconds=max(settings.worker_lease_seconds, 300),
    )
    timers = DurableTimerService(sessions, lease_seconds=settings.worker_lease_seconds)
    permits = PermitService(sessions)
    human_tasks = HumanTaskService(sessions)

    async def validate_permit(permit_id: str, action: str) -> None:
        claimed = await permits.claim(uuid.UUID(permit_id), actor_id="regent-worker")
        if claimed.binding.action != action:
            raise ValueError("permit action mismatch")
        await permits.consume(claimed.id, nonce=claimed.nonce)

    # Build optional P1 main chain dependencies
    model_provider = None
    try:
        model_provider = build_model_provider(settings)
    except ModelConfigurationError:
        logger.warning("model provider not configured; P1 discovery/requirement disabled")

    artifact_root = Path(settings.artifact_root)
    artifacts = FileArtifactStore(artifact_root)
    evidence_connector = GoalIntentEvidenceConnector(artifacts)
    preview_root = Path(settings.workspace_root) / "previews"
    deployment_provider = StaticPreviewDeploymentProvider(
        preview_root=preview_root,
        base_url="http://regent-api:8000",
    )

    generator = None
    workspace_writer = None
    if model_provider is not None:
        generator = ArtifactBackedCodeGenerator(model_provider, artifacts)
        resolver = ArtifactUriResolver(artifact_root)
        workspace_writer = WorkspaceWriter(Path(settings.workspace_root), resolver)

    sandbox = DockerSandboxDriver(
        root=Path(settings.build_root) / "sandbox",
        image=settings.sandbox_image,
    )
    materializer = DockerDependencyMaterializer(
        root=Path(settings.build_root) / "deps",
        image=settings.dependency_resolver_image,
        egress_proxy=settings.dependency_egress_proxy,
        permit_validator=validate_permit,
    )

    orchestrator = ExecutionOrchestrator(
        sessions,
        evidence_connector=evidence_connector,
        model_provider=model_provider,
        generator=generator,
        workspace_writer=workspace_writer,
        sandbox=sandbox,
        materializer=materializer,
        deployment_provider=deployment_provider,
        permits=permits,
    )
    p1_handlers = get_p1_event_handlers(orchestrator)

    dispatcher = OutboxDispatcher(
        sessions,
        handlers={
            "GoalStateChanged": log_state_change,
            "GoalSpecFrozen": log_state_change,
            **p1_handlers,
            "WorkStateChanged": log_state_change,
            "RunStateChanged": log_state_change,
            "TimerFired": log_state_change,
        },
        lease_seconds=max(settings.worker_lease_seconds, 300),
    )
    worker = Worker(
        worker_id=worker_id,
        dispatcher=dispatcher,
        leases=leases,
        timers=timers,
        permits=permits,
        human_tasks=human_tasks,
        poll_seconds=settings.worker_poll_seconds,
        heartbeat_seconds=max(1.0, settings.worker_lease_seconds / 3),
    )
    return worker, engine


async def run_async() -> None:
    worker, engine = create_worker()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signum, worker.stop)
    try:
        await worker.serve()
    finally:
        await engine.dispose()  # type: ignore[attr-defined]


def run() -> None:
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(run_async())
