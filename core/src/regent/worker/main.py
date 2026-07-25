import asyncio
import logging
import os
import signal
import socket
import uuid
from contextlib import suppress
from pathlib import Path
from time import monotonic

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.execution_orchestrator import (
    ExecutionOrchestrator,
    get_p1_event_handlers,
)
from regent.application.human_task_service import HumanTaskService
from regent.application.permit_service import PermitService
from regent.application.run_advancement import reclaim_stale_created_runs
from regent.application.runtime_profile_service import RuntimeProfileService
from regent.application.scheduler_service import SchedulerService
from regent.config import get_settings
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.code_generator import ArtifactBackedCodeGenerator, ArtifactUriResolver
from regent.infrastructure.database import create_engine, create_session_factory
from regent.infrastructure.delivery_review_capability import ensure_delivery_review_capability
from regent.infrastructure.deployment import StaticPreviewDeploymentProvider
from regent.infrastructure.evidence_capability import ensure_allowlisted_http_capability
from regent.infrastructure.evidence_sources import (
    AllowlistedHttpEvidenceConnector,
    CompositeEvidenceSourceConnector,
    GoalIntentEvidenceConnector,
)
from regent.infrastructure.product_surface_capability import ensure_product_surface_capability
from regent.infrastructure.sandbox import (
    DockerDependencyMaterializer,
    DockerSandboxDriver,
    LocalSandboxDriver,
)
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
        sessions: async_sessionmaker[AsyncSession] | None = None,
        timers: DurableTimerService | None = None,
        permits: PermitService | None = None,
        human_tasks: HumanTaskService | None = None,
        scheduler: SchedulerService | None = None,
        scheduler_org_keys: list[str] | None = None,
        poll_seconds: float,
        heartbeat_seconds: float,
    ) -> None:
        self.worker_id = worker_id
        self.dispatcher = dispatcher
        self.leases = leases
        self.sessions = sessions
        self.timers = timers
        self.permits = permits
        self.human_tasks = human_tasks
        self.scheduler = scheduler
        self.scheduler_org_keys = list(scheduler_org_keys or [])
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
                if self.sessions is not None:
                    try:
                        n = await reclaim_stale_created_runs(
                            self.sessions,
                            actor=f"worker:{self.worker_id}",
                            limit=10,
                        )
                        if n:
                            logger.info("advanced CREATED runs", extra={"count": n})
                    except Exception:
                        logger.exception("CREATED run reclaim failed")
                if self.scheduler is not None:
                    await self._scheduler_tick()
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

    async def _scheduler_tick(self) -> None:
        assert self.scheduler is not None
        org_keys = self.scheduler_org_keys
        if not org_keys:
            org_keys = await self.scheduler.list_active_org_keys()
        for org_key in org_keys:
            try:
                result = await self.scheduler.tick(
                    org_key=org_key, actor=f"worker:{self.worker_id}"
                )
                if result.get("selected"):
                    logger.info(
                        "scheduler tick selected",
                        extra={"org_key": org_key, "result": result},
                    )
            except Exception:
                logger.exception("scheduler tick failed", extra={"org_key": org_key})

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
        lease_seconds=max(settings.worker_lease_seconds, 900),
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
    evidence_proxy = settings.evidence_egress_proxy or settings.dependency_egress_proxy
    allowed_domains = [
        item.strip() for item in settings.evidence_allowed_domains.split(",") if item.strip()
    ]
    evidence_connector = CompositeEvidenceSourceConnector(
        [
            GoalIntentEvidenceConnector(artifacts),
            AllowlistedHttpEvidenceConnector(
                artifacts,
                allowed_domains=allowed_domains,
                egress_proxy=evidence_proxy,
                max_bytes=settings.evidence_max_bytes,
            ),
        ]
    )
    preview_root = Path(settings.workspace_root) / "previews"
    public_base = (settings.public_base_url or "http://regent-api:8000").rstrip("/")
    deployment_provider = StaticPreviewDeploymentProvider(
        preview_root=preview_root,
        base_url=public_base,
    )

    generator = None
    workspace_writer = None
    if model_provider is not None:
        generator = ArtifactBackedCodeGenerator(model_provider, artifacts)
        resolver = ArtifactUriResolver(artifact_root)
        workspace_writer = WorkspaceWriter(Path(settings.workspace_root), resolver)

    if settings.sandbox_mode == "local":
        sandbox = LocalSandboxDriver(root=Path(settings.build_root) / "sandbox")
    else:
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
            # TimerFired handled by orchestrator (GAC-C2) via p1_handlers override.
        },
        # Generation/discovery LLM calls routinely exceed short leases; avoid mid-handler reclaim.
        lease_seconds=max(settings.worker_lease_seconds, 900),
    )
    scheduler = SchedulerService(sessions) if settings.scheduler_enabled else None
    scheduler_org_keys = [
        item.strip() for item in settings.scheduler_org_keys.split(",") if item.strip()
    ]
    worker = Worker(
        worker_id=worker_id,
        dispatcher=dispatcher,
        leases=leases,
        sessions=sessions,
        timers=timers,
        permits=permits,
        human_tasks=human_tasks,
        scheduler=scheduler,
        scheduler_org_keys=scheduler_org_keys,
        poll_seconds=settings.worker_poll_seconds,
        heartbeat_seconds=max(1.0, settings.worker_lease_seconds / 3),
    )
    return worker, engine


async def run_async() -> None:
    worker, engine = create_worker()
    sessions = create_session_factory(engine)
    try:
        await ensure_allowlisted_http_capability(sessions)
        logger.info("seeded allowlisted-http-source-v1 capability")
    except Exception:
        logger.exception("failed to seed allowlisted-http-source-v1 capability")
    try:
        await ensure_delivery_review_capability(sessions)
        logger.info("seeded delivery-review-v1 capability")
    except Exception:
        logger.exception("failed to seed delivery-review-v1 capability")
    try:
        await ensure_product_surface_capability(sessions)
        logger.info("seeded product-surface-v1 capability")
    except Exception:
        logger.exception("failed to seed product-surface-v1 capability")
    try:
        n = await RuntimeProfileService(sessions).seed_bootstrap()
        logger.info("seeded runtime profiles", extra={"count": n})
    except Exception:
        logger.exception("failed to seed runtime profiles")
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
