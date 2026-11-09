from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
import asyncio
import os
import sys

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from regent import __version__
from regent.bootstrap.service_mode import resolve_service_mode
from regent.novel.api.novel import register_exception_handlers, router as novel_router
from regent.config import effective_runtime_profile, get_settings
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.database import create_engine, create_session_factory
from regent.model import ModelConfigurationError, ModelOutputError


def _host_health_payload(workspace_root: str) -> dict[str, Any]:
    """Attach host disk/mem/load snapshot for ops visibility (never raises)."""
    try:
        from regent.infrastructure.host_resources import (
            measure_host_resources,
            read_host_snapshot,
        )

        snap = read_host_snapshot(workspace_root)
        if snap is None:
            resources = measure_host_resources(workspace_root)
            return {
                "unhealthy": False,
                "reasons": [],
                "resources": resources.as_dict(),
                "note": "no_snapshot_yet",
            }
        return {
            "unhealthy": bool(snap.get("unhealthy")),
            "reasons": list(snap.get("reasons") or []),
            "resources": snap.get("resources") or {},
            "pruned": snap.get("pruned"),
            "measured_at": (snap.get("resources") or {}).get("measured_at"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "unhealthy": False}


def create_app() -> FastAPI:
    settings = get_settings()
    mode = resolve_service_mode(settings)
    from regent.api.transient_progress import TransientProgressRegistry

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.sessions = create_session_factory(engine)
        app.state.service_mode = mode.value
        if mode.includes_novel:
            from regent.novel.application.production import configure_session_factory

            configure_session_factory(app.state.sessions)
        app.state.transient_progress = TransientProgressRegistry()
        if mode.includes_legacy:
            from regent.application.runtime_profile_service import RuntimeProfileService
            from regent.infrastructure.delivery_review_capability import (
                ensure_delivery_review_capability,
            )
            from regent.infrastructure.product_surface_capability import (
                ensure_product_surface_capability,
            )

            with suppress(Exception):
                await RuntimeProfileService(app.state.sessions).seed_bootstrap()
            with suppress(Exception):
                await ensure_delivery_review_capability(app.state.sessions)
            with suppress(Exception):
                await ensure_product_surface_capability(app.state.sessions)
        with suppress(Exception):
            from regent.infrastructure.environment_heal_capability import (
                ensure_environment_heal_capability,
            )

            await ensure_environment_heal_capability(app.state.sessions)

        host_guard_task: asyncio.Task[None] | None = None
        # Preview prune/reap is legacy-owned; novel mode keeps host measure only via heal API.
        if settings.host_guard_enabled and mode.includes_legacy:

            async def _api_host_guard_loop() -> None:
                from regent.application.host_guard import tick_host_resource_guard

                while True:
                    try:
                        await tick_host_resource_guard(
                            app.state.sessions,
                            workspace_root=settings.workspace_root,
                            disk_percent_max=settings.host_disk_percent_max,
                            mem_percent_max=settings.host_mem_percent_max,
                            load1_per_cpu_max=settings.host_load1_per_cpu_max,
                            prune_keep_newest=settings.host_prune_preview_keep,
                            prune_disk_percent=settings.host_prune_disk_percent,
                            prune_mem_percent=settings.host_prune_mem_percent,
                            reap_processes=settings.host_reap_preview_processes,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    await asyncio.sleep(float(settings.host_guard_interval_seconds))

            host_guard_task = asyncio.create_task(
                _api_host_guard_loop(), name="regent-api-host-guard"
            )

        yield
        if host_guard_task is not None:
            host_guard_task.cancel()
            with suppress(asyncio.CancelledError):
                await host_guard_task
        await engine.dispose()

    app = FastAPI(
        title="Regent Core API",
        version=__version__,
        description="Reliable, governed goal execution core.",
        lifespan=lifespan,
    )

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, error: DomainError) -> JSONResponse:
        if error.code is ErrorCode.NOT_FOUND:
            status_code = 404
        elif error.code is ErrorCode.FORBIDDEN:
            status_code = 403
        else:
            status_code = 409
        body: dict[str, object] = {
            "code": error.code.value,
            "message": error.message,
        }
        if error.details:
            confirmation = error.details.get("confirmation")
            if confirmation is not None:
                body["confirmation"] = confirmation
            else:
                body["details"] = error.details
        return JSONResponse(status_code=status_code, content={"error": body})

    @app.exception_handler(ModelConfigurationError)
    async def model_configuration_handler(
        _request: Request, error: ModelConfigurationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "MODEL_NOT_CONFIGURED", "message": str(error)}},
        )

    @app.exception_handler(ModelOutputError)
    async def model_output_handler(_request: Request, error: ModelOutputError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"error": {"code": "MODEL_OUTPUT_INVALID", "message": str(error)}},
        )

    @app.get("/health/live", tags=["operations"])
    async def liveness() -> dict[str, str]:
        return {"status": "ok", "service_mode": mode.value}

    @app.get("/health/ready", tags=["operations"])
    async def readiness() -> dict[str, Any]:
        try:
            async with app.state.sessions() as session:
                value = await session.scalar(text("SELECT 1"))
                if mode.includes_legacy:
                    failed_events = await session.scalar(
                        text("SELECT count(*) FROM outbox_events WHERE status = 'FAILED'")
                    )
                    dead_letters = await session.scalar(
                        text(
                            "SELECT count(*) FROM outbox_events WHERE status = 'DEAD_LETTER'"
                        )
                    )
                    pending_events = await session.scalar(
                        text("SELECT count(*) FROM outbox_events WHERE status = 'PENDING'")
                    )
                    running_runs = await session.scalar(
                        text("SELECT count(*) FROM runs WHERE status = 'RUNNING'")
                    )
                    leaked_runs = await session.scalar(
                        text(
                            "SELECT count(*) FROM runs WHERE status = 'RUNNING' "
                            "AND (started_at IS NULL OR started_at < NOW() - INTERVAL '1 hour')"
                        )
                    )
                    active_goals = await session.scalar(
                        text("SELECT count(*) FROM goals WHERE status = 'ACTIVE'")
                    )
                    achieved_goals = await session.scalar(
                        text("SELECT count(*) FROM goals WHERE status = 'ACHIEVED'")
                    )
                else:
                    failed_events = dead_letters = pending_events = 0
                    running_runs = leaked_runs = active_goals = achieved_goals = 0
                    # Novel readiness: chapter-run backlog (fail-open if table absent).
                    novel_running = 0
                    with suppress(Exception):
                        novel_running = int(
                            await session.scalar(
                                text(
                                    "SELECT count(*) FROM novel_chapter_runs "
                                    "WHERE state IN ("
                                    "'RUNNING','QUEUED','PENDING_DECISION',"
                                    "'AWAITING_INPUT','RETRYABLE_FAILED'"
                                    ")"
                                )
                            )
                            or 0
                        )
            if value != 1:
                raise RuntimeError("database probe returned an unexpected value")
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        host = _host_health_payload(settings.workspace_root)
        status = "ok"
        if host.get("unhealthy"):
            status = "degraded"
        payload: dict[str, Any] = {
            "status": status,
            "service_mode": mode.value,
            "environment": settings.environment,
            "runtime_profile": effective_runtime_profile(settings),
            "database": "ok",
            "host": host,
        }
        if mode.includes_legacy:
            payload.update(
                {
                    "outbox_pending": str(pending_events or 0),
                    "outbox_failed": str(failed_events or 0),
                    "outbox_dead_letter": str(dead_letters or 0),
                    "runs_running": str(running_runs or 0),
                    "runs_leaked": str(leaked_runs or 0),
                    "goals_active": str(active_goals or 0),
                    "goals_achieved": str(achieved_goals or 0),
                }
            )
        else:
            payload["novel_chapter_runs_active"] = str(novel_running or 0)
        return payload

    @app.get("/v1/health", tags=["operations"])
    async def system_health() -> dict[str, Any]:
        """Comprehensive system health including stage distribution and leak detection."""
        if not mode.includes_legacy:
            ready = await readiness()
            return {
                "status": ready.get("status", "ok"),
                "service_mode": mode.value,
                "database": "ok",
                "runtime_profile": effective_runtime_profile(settings),
                "host": ready.get("host") or {},
                "metrics": {
                    "novel_chapter_runs_active": int(
                        ready.get("novel_chapter_runs_active") or 0
                    ),
                },
            }
        try:
            async with app.state.sessions() as session:
                stage_rows = (
                    await session.execute(
                        text(
                            "SELECT COALESCE(metadata->>'execution_stage', 'NULL') AS stage, "
                            "COUNT(*) AS cnt FROM goals WHERE status='ACTIVE' "
                            "GROUP BY 1 ORDER BY 2 DESC"
                        )
                    )
                ).all()
                stages = {row[0]: int(row[1]) for row in stage_rows}
                dead_letter_types = (
                    await session.execute(
                        text(
                            "SELECT event_type, COUNT(*) AS cnt FROM outbox_events "
                            "WHERE status='DEAD_LETTER' GROUP BY 1"
                        )
                    )
                ).all()
                dl_by_type = {row[0]: int(row[1]) for row in dead_letter_types}
                pending_events = await session.scalar(
                    text("SELECT count(*) FROM outbox_events WHERE status = 'PENDING'")
                )
                dead_letters = await session.scalar(
                    text("SELECT count(*) FROM outbox_events WHERE status = 'DEAD_LETTER'")
                )
                running_runs = await session.scalar(
                    text("SELECT count(*) FROM runs WHERE status = 'RUNNING'")
                )
                leaked_runs = await session.scalar(
                    text(
                        "SELECT count(*) FROM runs WHERE status = 'RUNNING' "
                        "AND (started_at IS NULL OR started_at < NOW() - INTERVAL '1 hour')"
                    )
                )
                active_goals = await session.scalar(
                    text("SELECT count(*) FROM goals WHERE status = 'ACTIVE'")
                )
                achieved_goals = await session.scalar(
                    text("SELECT count(*) FROM goals WHERE status = 'ACHIEVED'")
                )
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc
        host = _host_health_payload(settings.workspace_root)
        health = (
            leaked_runs == 0
            and dead_letters == 0
            and pending_events is not None
            and not host.get("unhealthy")
        )
        return {
            "status": "healthy" if health else "degraded",
            "service_mode": mode.value,
            "database": "ok",
            "runtime_profile": effective_runtime_profile(settings),
            "host": host,
            "metrics": {
                "goals_active": active_goals or 0,
                "goals_achieved": achieved_goals or 0,
                "runs_running": running_runs or 0,
                "runs_leaked": leaked_runs or 0,
                "outbox_pending": pending_events or 0,
                "outbox_dead_letters": dead_letters or 0,
            },
            "active_goal_stages": stages,
            "dead_letters_by_type": dl_by_type,
        }

    @app.get("/v1/doctor", tags=["operations"])
    async def doctor() -> dict[str, Any]:
        """O4: ops self-check (distinct from liveness). Never prints secrets."""
        from regent.application.doctor import run_doctor
        from regent.application.extension_readiness import build_extension_readiness
        from regent.application.workflow_presets import list_workflow_presets

        db_ok = False
        delivery_seeded: bool | None = None
        try:
            async with app.state.sessions() as session:
                value = await session.scalar(text("SELECT 1"))
                db_ok = value == 1
                if mode.includes_legacy:
                    delivery_seeded = bool(
                        await session.scalar(
                            text(
                                "SELECT 1 FROM capabilities WHERE name = 'delivery-review-v1' LIMIT 1"
                            )
                        )
                    )
                else:
                    delivery_seeded = None
        except Exception:
            db_ok = False
            delivery_seeded = False
        cfg = get_settings()
        host_summary: dict[str, Any] | None = None
        with suppress(Exception):
            from regent.infrastructure.host_resources import (
                measure_host_resources,
                read_host_snapshot,
            )

            snap = read_host_snapshot(cfg.workspace_root)
            if snap is None:
                host_summary = {
                    "unhealthy": False,
                    "reasons": [],
                    "resources": measure_host_resources(cfg.workspace_root).as_dict(),
                    "note": "no_snapshot_yet",
                }
            else:
                host_summary = {
                    "unhealthy": bool(snap.get("unhealthy")),
                    "reasons": list(snap.get("reasons") or []),
                    "resources": snap.get("resources") or {},
                    "actions": list(snap.get("actions") or []),
                    "pruned": snap.get("pruned"),
                    "reaped": snap.get("reaped"),
                }
        report = run_doctor(
            db_ok=db_ok,
            delivery_review_seeded=delivery_seeded,
            canary_percent=float(getattr(cfg, "gq_agentic_canary_percent", 0) or 0),
            settings_summary={
                "service_mode": mode.value,
                "delivery_product_gates_mode": getattr(
                    cfg, "delivery_product_gates_mode", None
                ),
                "workspace_root_set": bool(getattr(cfg, "workspace_root", None)),
                "host_guard_enabled": bool(getattr(cfg, "host_guard_enabled", True)),
            },
            host_summary=host_summary,
        )
        report["service_mode"] = mode.value
        report["workflow_presets"] = list_workflow_presets() if mode.includes_legacy else []
        extensions = [
            {
                "name": "environment-heal-v1",
                "certified": True,
                "available": bool(getattr(cfg, "host_guard_enabled", True)),
                "reason": "allowlisted host detect/repair + evolving LESSONS",
            },
        ]
        if mode.includes_legacy:
            extensions.insert(
                0,
                {
                    "name": "delivery-review-v1",
                    "certified": bool(delivery_seeded),
                    "available": bool(delivery_seeded),
                },
            )
        report["extension_readiness"] = build_extension_readiness(extensions)
        return report

    @app.post("/v1/ops/environment/heal", tags=["operations"])
    async def environment_heal() -> dict[str, Any]:
        """Detect host pressure and repair: prune venvs, reap stale previews, soft-pause."""
        from regent.application.environment_heal_memory import load_heal_memory
        from regent.application.host_guard import tick_host_resource_guard
        from regent.infrastructure.environment_heal_registry import list_heal_actions

        cfg = get_settings()
        if not cfg.host_guard_enabled:
            raise HTTPException(status_code=503, detail="host_guard_disabled")
        if not mode.includes_legacy:
            # Novel-only: observe host pressure; do not prune previews or soft-pause goals.
            from regent.infrastructure.host_resources import measure_host_resources

            resources = measure_host_resources(Path(cfg.workspace_root))
            result = {
                "decision": {
                    "unhealthy": False,
                    "resources": resources.as_dict(),
                    "note": "novel_mode_measure_only",
                }
            }
        else:
            result = await tick_host_resource_guard(
                app.state.sessions,
                workspace_root=cfg.workspace_root,
                disk_percent_max=cfg.host_disk_percent_max,
                mem_percent_max=cfg.host_mem_percent_max,
                load1_per_cpu_max=cfg.host_load1_per_cpu_max,
                prune_keep_newest=cfg.host_prune_preview_keep,
                prune_disk_percent=cfg.host_prune_disk_percent,
                prune_mem_percent=cfg.host_prune_mem_percent,
                reap_processes=bool(cfg.host_reap_preview_processes),
            )
        memory = load_heal_memory(Path(cfg.workspace_root))
        return {
            "ok": True,
            "service_mode": mode.value,
            "healed": result,
            "allowlisted_actions": list_heal_actions(),
            "learned_preferences": list(memory.get("preferences") or [])[:20],
            "framework": {
                "capability": "environment-heal-v1",
                "skill_id": "ops-environment",
                "evolves": "preferences + LESSONS (not arbitrary shell)",
            },
        }

    @app.get("/v1/ops/environment/heal", tags=["operations"])
    async def environment_heal_status() -> dict[str, Any]:
        """Inspect allowlisted heal actions and learned preferences (no mutation)."""
        from regent.application.environment_heal_memory import (
            load_heal_memory,
            read_ops_lessons,
        )
        from regent.infrastructure.environment_heal_registry import list_heal_actions
        from regent.infrastructure.host_resources import read_host_snapshot

        cfg = get_settings()
        memory = load_heal_memory(Path(cfg.workspace_root))
        return {
            "ok": True,
            "service_mode": mode.value,
            "allowlisted_actions": list_heal_actions(),
            "learned_preferences": list(memory.get("preferences") or [])[:20],
            "recent_incidents": list(memory.get("incidents") or [])[-10:],
            "lessons_preview": (read_ops_lessons(Path(cfg.workspace_root)) or "")[:1500],
            "host_snapshot": read_host_snapshot(cfg.workspace_root),
            "framework": {
                "capability": "environment-heal-v1",
                "skill_id": "ops-environment",
                "evolves": "preferences + LESSONS (not arbitrary shell)",
            },
        }

    @app.get("/v1/workflow-presets", tags=["operations"])
    async def workflow_presets() -> dict[str, Any]:
        """O4: admitted named workflow stage presets."""
        if not mode.includes_legacy:
            return {"ok": True, "service_mode": mode.value, "presets": []}
        from regent.application.workflow_presets import list_workflow_presets

        return {"ok": True, "service_mode": mode.value, "presets": list_workflow_presets()}

    if mode.includes_legacy:
        from regent.bootstrap.legacy_api import mount_legacy_surface

        mount_legacy_surface(app, settings)

    @app.get("/", include_in_schema=False)
    async def root() -> Response:
        if os.path.isfile("/app/static/index.html"):
            return FileResponse("/app/static/index.html")
        if mode.includes_legacy:
            return RedirectResponse("/console/")
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "NOVEL_FRONTEND_UNAVAILABLE",
                    "message": "novel-web static assets missing; service_mode=novel",
                    "service_mode": mode.value,
                }
            },
        )

    if mode.includes_novel:
        register_exception_handlers(app)
        app.include_router(novel_router)

    # ---- novel-web SPA (served from /app/static when present) ----
    _static = "/app/static"
    if mode.includes_novel and os.path.isdir(_static):
        app.mount("/", StaticFiles(directory=_static), name="novel-web")

        @app.middleware("http")
        async def _spa_fallback(request: Request, call_next):
            resp = await call_next(request)
            if resp.status_code == 404:
                path = request.url.path
                blocked = ("/v1/", "/health", "/console", "/docs", "/openapi", "/redoc", "/preview")
                if (
                    not path.startswith(blocked)
                    and os.path.isfile(f"{_static}/index.html")
                ):
                    return FileResponse(f"{_static}/index.html")
            return resp

    return app


app = create_app()


def run() -> None:
    # psycopg async connections are incompatible with Windows' default
    # ProactorEventLoop. Uvicorn's auto loop otherwise selects it before the
    # application can reach PostgreSQL.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    uvicorn.run(
        "regent.api.main:app",
        host="0.0.0.0",
        port=8000,
        loop="asyncio",
    )
