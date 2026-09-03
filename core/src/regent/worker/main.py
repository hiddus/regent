import asyncio
import logging
import os
import signal
import socket
import sys
import uuid
from contextlib import suppress
from pathlib import Path
from time import monotonic

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.application.budget_ledger import BudgetLedger
from regent.application.event_engine import EventEngine
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
from regent.application.generator_factory import build_generator_selector
from regent.infrastructure.code_generator import ArtifactUriResolver
from regent.infrastructure.database import create_engine, create_session_factory
from regent.infrastructure.delivery_review_capability import ensure_delivery_review_capability
from regent.infrastructure.deployment import StaticPreviewDeploymentProvider
from regent.infrastructure.runtime_preview import RuntimePreviewDeploymentProvider
from regent.infrastructure.evidence_capability import ensure_allowlisted_http_capability
from regent.infrastructure.evidence_sources import (
    AllowlistedHttpEvidenceConnector,
    CompositeEvidenceSourceConnector,
    GoalIntentEvidenceConnector,
)
from regent.infrastructure.product_surface_capability import ensure_product_surface_capability
from regent.infrastructure.environment_heal_capability import (
    ensure_environment_heal_capability,
)
from regent.infrastructure.sandbox import (
    DockerDependencyMaterializer,
    DockerSandboxDriver,
    LocalSandboxDriver,
)
from regent.infrastructure.workspace_writer import WorkspaceWriter
from regent.model import ModelConfigurationError
from regent.model import ModelProvider
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
        event_engine: EventEngine | None = None,
        novel_provider: ModelProvider | None = None,
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
        self.event_engine = event_engine
        self.novel_provider = novel_provider
        self._stopping = asyncio.Event()
        self._reconciliation = None
        self._reconciliation_interval = 300.0
        self._next_reconciliation = 0.0
        self._privacy_retention = None
        self._privacy_retention_interval = 3600.0
        self._next_privacy_retention = 0.0
        self._host_guard_interval = 60.0
        self._next_host_guard = 0.0
        self._host_guard_enabled = True
        self._behavior_monitor_interval = 600.0
        self._next_behavior_monitor = 0.0
        self._behavior_monitor_enabled = True
        if sessions is not None:
            from regent.application.reconciliation_worker import ReconciliationWorker
            from regent.config import get_settings

            self._reconciliation = ReconciliationWorker(sessions)
            settings = get_settings()
            self._reconciliation_interval = settings.reconciliation_interval_seconds
            self._privacy_retention = sessions
            self._privacy_retention_interval = settings.privacy_retention_interval_seconds
            self._host_guard_enabled = bool(settings.host_guard_enabled)
            self._host_guard_interval = float(settings.host_guard_interval_seconds)
            self._behavior_monitor_enabled = bool(
                getattr(settings, "behavior_monitor_enabled", True)
            )
            self._behavior_monitor_interval = float(
                getattr(settings, "behavior_monitor_interval_seconds", 600.0)
            )

    async def serve(self) -> None:
        lease = await self.leases.acquire(
            self.worker_id,
            metadata={"hostname": socket.gethostname(), "pid": os.getpid()},
        )
        next_heartbeat = monotonic() + self.heartbeat_seconds
        logger.info("worker lease acquired", extra={"worker_id": self.worker_id})
        if self.event_engine is not None:
            await self.event_engine.start()
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
                if self.sessions is not None and self.novel_provider is not None:
                    try:
                        from regent.novel.application.works import advance_background_run

                        async with self.sessions() as novel_session:
                            progressed = await advance_background_run(
                                novel_session, provider=self.novel_provider
                            )
                            if progressed is not None:
                                await novel_session.commit()
                    except Exception:
                        logger.exception("novel Agent loop tick failed")
                if self.scheduler is not None:
                    await self._scheduler_tick()
                if self._reconciliation is not None and monotonic() >= self._next_reconciliation:
                    try:
                        reconciled = await self._reconciliation.tick()
                        if reconciled:
                            logger.info(
                                "reconciliation worker tick",
                                extra={"count": len(reconciled)},
                            )
                    except Exception:
                        logger.exception("reconciliation worker tick failed")
                    if self.sessions is not None:
                        try:
                            from regent.application.delivery_progress_watchdog import (
                                tick_stale_delivery_progress,
                            )

                            stale_stats = await tick_stale_delivery_progress(self.sessions)
                            if stale_stats.get("warned") or stale_stats.get("handed_off"):
                                logger.info("stale delivery progress", extra=stale_stats)
                        except Exception:
                            logger.exception("stale delivery progress tick failed")
                        try:
                            from regent.application.delivery_progress_watchdog import (
                                reclaim_generating_zombies,
                            )

                            zombie_stats = await reclaim_generating_zombies(self.sessions)
                            if zombie_stats.get("stale_runs_failed") or zombie_stats.get(
                                "zombie_goals_failed"
                            ):
                                logger.info(
                                    "zombie reclaim",
                                    extra=zombie_stats,
                                )
                        except Exception:
                            logger.exception("zombie reclaim tick failed")
                    self._next_reconciliation = monotonic() + self._reconciliation_interval
                if (
                    self._privacy_retention is not None
                    and monotonic() >= self._next_privacy_retention
                ):
                    try:
                        from regent.application.privacy_service import PrivacyService

                        result = await PrivacyService(self._privacy_retention).anonymize_expired()
                        if result.get("observations_anonymized"):
                            logger.info(
                                "privacy retention anonymize",
                                extra=result,
                            )
                    except Exception:
                        logger.exception("privacy retention anonymize failed")
                    self._next_privacy_retention = (
                        monotonic() + self._privacy_retention_interval
                    )
                if self._host_guard_enabled and monotonic() >= self._next_host_guard:
                    try:
                        from regent.application.host_guard import tick_host_resource_guard
                        from regent.config import get_settings

                        hs = get_settings()
                        host_stats = await tick_host_resource_guard(
                            self.sessions,
                            workspace_root=hs.workspace_root,
                            disk_percent_max=hs.host_disk_percent_max,
                            mem_percent_max=hs.host_mem_percent_max,
                            load1_per_cpu_max=hs.host_load1_per_cpu_max,
                            prune_keep_newest=hs.host_prune_preview_keep,
                            prune_disk_percent=hs.host_prune_disk_percent,
                            prune_mem_percent=hs.host_prune_mem_percent,
                            reap_processes=hs.host_reap_preview_processes,
                        )
                        decision = (host_stats.get("decision") or {})
                        if decision.get("unhealthy") or (decision.get("pruned") or {}).get(
                            "removed_count"
                        ):
                            logger.warning("host resource guard tick", extra=host_stats)
                    except Exception:
                        logger.exception("host resource guard tick failed")
                    self._next_host_guard = monotonic() + self._host_guard_interval
                if (
                    self._behavior_monitor_enabled
                    and self.sessions is not None
                    and monotonic() >= self._next_behavior_monitor
                ):
                    try:
                        from regent.application.behavior_monitor_tick import (
                            tick_behavior_monitoring,
                        )

                        bm_stats = await tick_behavior_monitoring(
                            self.sessions,
                            budget_ledger=BudgetLedger(self.sessions),
                        )
                        if bm_stats.get("observed") or bm_stats.get("monitored"):
                            logger.info(
                                "behavior monitor tick",
                                extra=bm_stats,
                            )
                    except Exception:
                        logger.exception("behavior monitor tick failed")
                    self._next_behavior_monitor = (
                        monotonic() + self._behavior_monitor_interval
                    )
                await self.dispatcher.dispatch_once(self.worker_id)
                if monotonic() >= next_heartbeat:
                    lease = await self.leases.heartbeat(lease)
                    next_heartbeat = monotonic() + self.heartbeat_seconds
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    continue
        finally:
            if self.event_engine is not None:
                with suppress(Exception):
                    await self.event_engine.stop()
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
    static_preview = StaticPreviewDeploymentProvider(
        preview_root=preview_root,
        base_url=public_base,
    )
    deployment_provider = RuntimePreviewDeploymentProvider(
        preview_root=preview_root,
        static_provider=static_preview,
        base_url=public_base,
    )

    generator = None
    workspace_writer = None
    if model_provider is not None:
        # GQ-1/GQ-3: build a per-goal GeneratorSelector (fail-closed on mismatch).
        # A single injected generator would cap canary at the startup default.
        generator = build_generator_selector(
            settings,
            model_provider,
            artifacts,
            sessions=sessions,
            enforce_consistency=True,
        )
        resolver = ArtifactUriResolver(artifact_root)
        workspace_writer = WorkspaceWriter(Path(settings.workspace_root), resolver)

    from regent.infrastructure.sandbox import (
        parse_host_path_map,
        resolve_agent_sandbox_user,
    )

    path_map = parse_host_path_map(getattr(settings, "host_path_map", None))
    sandbox_user = resolve_agent_sandbox_user(settings)
    if settings.sandbox_mode == "local":
        sandbox = LocalSandboxDriver(root=Path(settings.build_root) / "sandbox")
    else:
        sandbox = DockerSandboxDriver(
            root=Path(settings.build_root) / "sandbox",
            image=settings.sandbox_image,
            host_path_map=path_map,
            run_as_user=sandbox_user,
            require_host_path_map_in_container=True,
        )
    materializer = DockerDependencyMaterializer(
        root=Path(settings.build_root) / "deps",
        image=settings.dependency_resolver_image,
        egress_proxy=settings.dependency_egress_proxy,
        permit_validator=validate_permit,
        host_path_map=path_map,
        run_as_user=sandbox_user,
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
        budget_ledger=BudgetLedger(sessions),
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
            # Observability-only: delivery state is already on goal.metadata.
            "DeliveryStateChanged": log_state_change,
            # TimerFired handled by orchestrator (GAC-C2) via p1_handlers override.
        },
        # Generation/discovery LLM calls routinely exceed short leases; avoid mid-handler reclaim.
        lease_seconds=max(settings.worker_lease_seconds, 900),
        dispatch_concurrency=settings.worker_dispatch_concurrency,
    )
    scheduler = SchedulerService(sessions) if settings.scheduler_enabled else None
    scheduler_org_keys = [
        item.strip() for item in settings.scheduler_org_keys.split(",") if item.strip()
    ]
    # Phase 3.3: EventEngine wraps OutboxDispatcher for unified event routing
    event_engine = EventEngine(sessions)
    event_engine.register_handlers(p1_handlers)
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
        event_engine=event_engine,
        novel_provider=model_provider,
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
        await ensure_environment_heal_capability(sessions)
        logger.info("seeded environment-heal-v1 capability")
    except Exception:
        logger.exception("failed to seed environment-heal-v1 capability")
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
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_async())
