import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from typing import Any

from regent import __version__
from regent.api.app_delivery import router as app_delivery_router
from regent.api.app_guidance import router as app_guidance_router
from regent.api.app_previews import router as app_previews_router
from regent.api.app_projects import router as app_projects_router
from regent.api.baselines import router as baselines_router
from regent.api.conversations import router as conversations_router
from regent.api.eval_runs import router as eval_runs_router
from regent.api.experiments import router as experiments_router
from regent.api.feedback import router as feedback_router
from regent.api.goals import router as goals_router
from regent.api.governance import router as governance_router
from regent.api.memories import router as memories_router
from regent.api.observations import router as observations_router
from regent.api.product_creation import router as product_creation_router
from regent.api.runtime_profiles import router as runtime_profiles_router
from regent.api.scheduler import router as scheduler_router
from regent.api.self_improvement import router as self_improvement_router
from regent.api.side_effects import router as side_effects_router
from regent.api.tools import router as tools_router
from regent.api.works import router as works_router
from regent.application.runtime_profile_service import RuntimeProfileService
from regent.config import get_settings
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.database import create_engine, create_session_factory
from regent.infrastructure.delivery_review_capability import ensure_delivery_review_capability
from regent.model import ModelConfigurationError, ModelOutputError


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.sessions = create_session_factory(engine)
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
        yield
        await engine.dispose()

    app = FastAPI(
        title="Regent Core API",
        version=__version__,
        description="Reliable, governed goal execution core.",
        lifespan=lifespan,
    )

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, error: DomainError) -> JSONResponse:
        status_code = 404 if error.code is ErrorCode.NOT_FOUND else 409
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": error.code.value, "message": error.message}},
        )

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
        return {
            "status": "ok",
            "environment": settings.environment,
            "database": "ok",
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
                stage_rows = await session.execute(
                    text(
                        "SELECT COALESCE(metadata->>'execution_stage', 'NULL') as stage, "
                        "COUNT(*) as cnt FROM goals WHERE status='ACTIVE' "
                        "GROUP BY stage ORDER BY cnt DESC"
                    )
                )
                stages = {row.stage: row.cnt for row in stage_rows}
                dead_letter_types = await session.execute(
                    text(
                        "SELECT event_type, COUNT(*) FROM outbox_events "
                        "WHERE status='DEAD_LETTER' GROUP BY event_type"
                    )
                )
                dl_by_type = {row.event_type: row.cnt for row in dead_letter_types}
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
        health = leaked_runs == 0 and dead_letters == 0 and pending_events is not None
        return {
            "status": "healthy" if health else "degraded",
            "database": "ok",
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

    console_path = Path("/app/apps/regent-console")
    if console_path.exists():
        app.mount("/console", StaticFiles(directory=console_path, html=True), name="console")

    def preview_file(project_id: uuid.UUID, release_id: uuid.UUID, filename: str) -> FileResponse:
        allowed = {
            "index.html": "text/html",
            "styles.css": "text/css",
            "app.js": "text/javascript",
            "regent-preview.js": "text/javascript",
        }
        if filename not in allowed:
            raise HTTPException(status_code=404, detail="preview file not found")
        root = (Path(settings.workspace_root) / "previews").resolve()
        path = (root / str(project_id) / str(release_id) / filename).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="preview file not found")
        return FileResponse(
            path,
            media_type=allowed[filename],
            headers={
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'"
                ),
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

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
    app.include_router(baselines_router)
    app.include_router(conversations_router)
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
    app.include_router(experiments_router)
    app.include_router(feedback_router)
    app.include_router(scheduler_router)
    app.include_router(runtime_profiles_router)
    app.include_router(eval_runs_router)
    app.include_router(memories_router)
    return app


app = create_app()


def run() -> None:
    uvicorn.run("regent.api.main:app", host="0.0.0.0", port=8000)
