import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
import asyncio
import os
import sys

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from regent import __version__
from regent.api.aar1_v2 import router as aar1_v2_router
from regent.api.app_delivery import router as app_delivery_router
from regent.api.app_guidance import router as app_guidance_router
from regent.api.app_previews import router as app_previews_router
from regent.api.app_projects import router as app_projects_router
from regent.api.baselines import router as baselines_router
from regent.api.conversations import router as conversations_router
from regent.api.events import router as events_router
from regent.api.eval_runs import router as eval_runs_router
from regent.api.experiments import router as experiments_router
from regent.api.feedback import router as feedback_router
from regent.api.goals import router as goals_router
from regent.api.governance import router as governance_router
from regent.api.human_tasks import router as human_tasks_router
from regent.api.memories import router as memories_router
from regent.api.observations import router as observations_router
from regent.api.product_creation import router as product_creation_router
from regent.api.public_deploy import router as public_deploy_router
from regent.api.reports import router as reports_router
from regent.api.runtime_profiles import router as runtime_profiles_router
from regent.api.scheduler import router as scheduler_router
from regent.api.self_improvement import router as self_improvement_router
from regent.api.harness_evolution import router as harness_evolution_router
from regent.api.side_effects import router as side_effects_router
from regent.api.tools import router as tools_router
from regent.api.uploads import router as uploads_router
from regent.api.webhooks import router as webhooks_router
from regent.api.works import router as works_router
from regent.api.preview_security import PREVIEW_CONTENT_SECURITY_POLICY
from regent.application.runtime_profile_service import RuntimeProfileService
from regent.config import effective_runtime_profile, get_settings
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.database import create_engine, create_session_factory
from regent.infrastructure.delivery_review_capability import ensure_delivery_review_capability
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
    from regent.api.transient_progress import TransientProgressRegistry

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.sessions = create_session_factory(engine)
        app.state.transient_progress = TransientProgressRegistry()
        # Migration may not have applied yet; fail open at boot, fail-closed at use.
        with suppress(Exception):
            await RuntimeProfileService(app.state.sessions).seed_bootstrap()
        with suppress(Exception):
            await ensure_delivery_review_capability(app.state.sessions)
        with suppress(Exception):
            from regent.infrastructure.product_surface_capability import (
                ensure_product_surface_capability,
            )

            await ensure_product_surface_capability(app.state.sessions)
        with suppress(Exception):
            from regent.infrastructure.environment_heal_capability import (
                ensure_environment_heal_capability,
            )

            await ensure_environment_heal_capability(app.state.sessions)

        host_guard_task: asyncio.Task[None] | None = None
        if settings.host_guard_enabled:

            async def _api_host_guard_loop() -> None:
                """API-side heal loop so prune/soft-pause still run if workers thrash."""
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
        return {"status": "ok"}

    @app.get("/health/ready", tags=["operations"])
    async def readiness() -> dict[str, Any]:
        try:
            async with app.state.sessions() as session:
                value = await session.scalar(text("SELECT 1"))
                failed_events = await session.scalar(
                    text("SELECT count(*) FROM outbox_events WHERE status = 'FAILED'")
                )
                dead_letters = await session.scalar(
                    text("SELECT count(*) FROM outbox_events WHERE status = 'DEAD_LETTER'")
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
            if value != 1:
                raise RuntimeError("database probe returned an unexpected value")
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        host = _host_health_payload(settings.workspace_root)
        status = "ok"
        if host.get("unhealthy"):
            status = "degraded"
        return {
            "status": status,
            "environment": settings.environment,
            "runtime_profile": effective_runtime_profile(settings),
            "database": "ok",
            "host": host,
            "outbox_pending": str(pending_events or 0),
            "outbox_failed": str(failed_events or 0),
            "outbox_dead_letter": str(dead_letters or 0),
            "runs_running": str(running_runs or 0),
            "runs_leaked": str(leaked_runs or 0),
            "goals_active": str(active_goals or 0),
            "goals_achieved": str(achieved_goals or 0),
        }

    @app.get("/v1/health", tags=["operations"])
    async def system_health() -> dict[str, Any]:
        """Comprehensive system health including stage distribution and leak detection."""
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
                delivery_seeded = bool(
                    await session.scalar(
                        text(
                            "SELECT 1 FROM capabilities WHERE name = 'delivery-review-v1' LIMIT 1"
                        )
                    )
                )
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
                # One-shot measure for doctor visibility without mutating unless heal.
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
                "delivery_product_gates_mode": getattr(
                    cfg, "delivery_product_gates_mode", None
                ),
                "workspace_root_set": bool(getattr(cfg, "workspace_root", None)),
                "host_guard_enabled": bool(getattr(cfg, "host_guard_enabled", True)),
            },
            host_summary=host_summary,
        )
        report["workflow_presets"] = list_workflow_presets()
        report["extension_readiness"] = build_extension_readiness(
            [
                {
                    "name": "delivery-review-v1",
                    "certified": bool(delivery_seeded),
                    "available": bool(delivery_seeded),
                },
                {
                    "name": "environment-heal-v1",
                    "certified": True,
                    "available": bool(getattr(cfg, "host_guard_enabled", True)),
                    "reason": "allowlisted host detect/repair + evolving LESSONS",
                },
            ]
        )
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
        result = await tick_host_resource_guard(
            app.state.sessions,
            workspace_root=cfg.workspace_root,
            disk_percent_max=cfg.host_disk_percent_max,
            mem_percent_max=cfg.host_mem_percent_max,
            load1_per_cpu_max=cfg.host_load1_per_cpu_max,
            prune_keep_newest=cfg.host_prune_preview_keep,
            prune_disk_percent=cfg.host_prune_disk_percent,
            prune_mem_percent=cfg.host_prune_mem_percent,
            reap_processes=cfg.host_reap_preview_processes,
        )
        memory = load_heal_memory(Path(cfg.workspace_root))
        return {
            "ok": True,
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
        from regent.application.workflow_presets import list_workflow_presets

        return {"ok": True, "presets": list_workflow_presets()}

    console_path = Path("/app/apps/regent-console/dist")
    if not console_path.exists():
        console_path = Path("/app/apps/regent-console")
    if console_path.exists() and (console_path / "index.html").exists():
        app.mount("/console", StaticFiles(directory=console_path, html=True), name="console")

    def preview_file(project_id: uuid.UUID, release_id: uuid.UUID, filename: str) -> FileResponse:
        allowed_types = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "text/javascript",
            ".mjs": "text/javascript",
            ".json": "application/json",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".ico": "image/x-icon",
            ".txt": "text/plain",
            ".map": "application/json",
        }
        if (
            "\\" in filename
            or "/" in filename
            or filename.startswith(".")
            or ".." in filename
        ):
            raise HTTPException(status_code=404, detail="preview file not found")
        media_type = allowed_types.get(Path(filename).suffix.lower())
        if media_type is None:
            raise HTTPException(status_code=404, detail="preview file not found")
        root = (Path(settings.workspace_root) / "previews").resolve()
        path = (root / str(project_id) / str(release_id) / filename).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="preview file not found")
        return FileResponse(
            path,
            media_type=media_type,
            headers={
                "Content-Security-Policy": PREVIEW_CONTENT_SECURITY_POLICY,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    # Runtime proxy MUST be registered before /preview/{project_id}/... or
    # FastAPI treats "runtime" as a UUID path param and returns 422.
    @app.api_route(
        "/preview/runtime/{deployment_id}/",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
        response_model=None,
    )
    @app.api_route(
        "/preview/runtime/{deployment_id}/{path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
        response_model=None,
    )
    async def preview_runtime_proxy(
        request: Request, deployment_id: str, path: str = ""
    ) -> Any:
        """Reverse-proxy live runtime previews; rewrite root-absolute URLs."""
        import httpx
        from fastapi.responses import Response as FastAPIResponse

        from regent.infrastructure.preview_path_rewrite import (
            rewrite_location_header,
            rewrite_preview_css,
            rewrite_preview_html,
        )

        root = (Path(settings.workspace_root) / "previews" / "runtime" / deployment_id).resolve()
        base = (Path(settings.workspace_root) / "previews" / "runtime").resolve()
        if base not in root.parents and root != base:
            raise HTTPException(status_code=404, detail="preview not found")
        port_file = root / ".regent-preview-port"
        if not port_file.is_file():
            raise HTTPException(status_code=404, detail="preview process not registered")
        try:
            port = int(port_file.read_text(encoding="utf-8").strip())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="invalid preview port") from exc
        suffix = path.lstrip("/") if path else ""
        hosts = [
            os.environ.get("REGENT_PREVIEW_ADVERTISE_HOST", "").strip(),
            "regent-worker",
            "regent-worker-2",
            "regent-worker-3",
            "127.0.0.1",
        ]
        body = await request.body()
        forward_headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower()
            not in {
                "host",
                "content-length",
                "connection",
                "transfer-encoding",
            }
        }
        last_err = "unreachable"
        # Delivery fix: forward query string (delta?from=&to=, search, filters).
        qs = request.url.query
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
            for host in [h for h in hosts if h]:
                url = f"http://{host}:{port}/{suffix}"
                if qs:
                    url = f"{url}?{qs}"
                try:
                    upstream = await client.request(
                        request.method,
                        url,
                        content=body if body else None,
                        headers=forward_headers,
                    )
                    media = (upstream.headers.get("content-type") or "").lower()
                    content = upstream.content
                    if "text/html" in media and content:
                        try:
                            text_html = content.decode(
                                upstream.charset_encoding or "utf-8", errors="replace"
                            )
                        except Exception:  # noqa: BLE001
                            text_html = content.decode("utf-8", errors="replace")
                        text_html = rewrite_preview_html(
                            text_html, deployment_id=deployment_id
                        )
                        content = text_html.encode("utf-8")
                    elif "css" in media and content:
                        try:
                            text_css = content.decode(
                                upstream.charset_encoding or "utf-8", errors="replace"
                            )
                        except Exception:  # noqa: BLE001
                            text_css = content.decode("utf-8", errors="replace")
                        text_css = rewrite_preview_css(
                            text_css, deployment_id=deployment_id
                        )
                        content = text_css.encode("utf-8")
                    out_headers = {
                        "Content-Security-Policy": PREVIEW_CONTENT_SECURITY_POLICY,
                        "X-Content-Type-Options": "nosniff",
                        "Referrer-Policy": "no-referrer",
                        "X-Regent-Preview-Upstream": host,
                        "X-Regent-Preview-Path-Rewrite": "1",
                    }
                    location = upstream.headers.get("location")
                    if location:
                        out_headers["Location"] = rewrite_location_header(
                            location, deployment_id=deployment_id
                        )
                    return FastAPIResponse(
                        content=content,
                        status_code=upstream.status_code,
                        media_type=upstream.headers.get("content-type"),
                        headers=out_headers,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_err = str(exc)
                    continue
        raise HTTPException(status_code=502, detail=f"preview upstream failed: {last_err}")

    @app.get("/preview/{project_id}/{release_id}/", include_in_schema=False)
    async def preview_index(project_id: uuid.UUID, release_id: uuid.UUID) -> FileResponse:
        return preview_file(project_id, release_id, "index.html")

    @app.get("/preview/{project_id}/{release_id}/{filename}", include_in_schema=False)
    async def preview_asset(
        project_id: uuid.UUID, release_id: uuid.UUID, filename: str
    ) -> FileResponse:
        return preview_file(project_id, release_id, filename)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/console/")

    app.include_router(goals_router)
    app.include_router(aar1_v2_router)
    app.include_router(baselines_router)
    app.include_router(conversations_router)
    app.include_router(events_router)
    app.include_router(app_delivery_router)
    app.include_router(app_guidance_router)
    app.include_router(app_projects_router)
    app.include_router(app_previews_router)
    app.include_router(governance_router)
    app.include_router(works_router)
    app.include_router(tools_router)
    app.include_router(observations_router)
    app.include_router(product_creation_router)
    app.include_router(side_effects_router)
    app.include_router(self_improvement_router)
    app.include_router(harness_evolution_router)
    app.include_router(experiments_router)
    app.include_router(feedback_router)
    app.include_router(scheduler_router)
    app.include_router(runtime_profiles_router)
    app.include_router(eval_runs_router)
    app.include_router(memories_router)
    # F-1 (2026-07-31): previously defined but unmounted — Console depends on
    # human-tasks + uploads; webhooks/reports/public-deploy complete the surface.
    app.include_router(human_tasks_router)
    app.include_router(uploads_router)
    app.include_router(webhooks_router)
    app.include_router(reports_router)
    app.include_router(public_deploy_router)
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
