import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from regent import __version__
from regent.api.app_delivery import router as app_delivery_router
from regent.api.app_guidance import router as app_guidance_router
from regent.api.app_previews import router as app_previews_router
from regent.api.app_projects import router as app_projects_router
from regent.api.baselines import router as baselines_router
from regent.api.conversations import router as conversations_router
from regent.api.experiments import router as experiments_router
from regent.api.feedback import router as feedback_router
from regent.api.goals import router as goals_router
from regent.api.governance import router as governance_router
from regent.api.observations import router as observations_router
from regent.api.product_creation import router as product_creation_router
from regent.api.self_improvement import router as self_improvement_router
from regent.api.side_effects import router as side_effects_router
from regent.api.tools import router as tools_router
from regent.api.works import router as works_router
from regent.config import get_settings
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.database import create_engine, create_session_factory
from regent.model import ModelConfigurationError, ModelOutputError


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.sessions = create_session_factory(engine)
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
    async def readiness() -> dict[str, str]:
        try:
            async with app.state.sessions() as session:
                value = await session.scalar(text("SELECT 1"))
                failed_events = await session.scalar(
                    text("SELECT count(*) FROM outbox_events WHERE status = 'FAILED'")
                )
                dead_letters = await session.scalar(
                    text("SELECT count(*) FROM outbox_events WHERE status = 'DEAD_LETTER'")
                )
            if value != 1:
                raise RuntimeError("database probe returned an unexpected value")
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {
            "status": "ok",
            "environment": settings.environment,
            "database": "ok",
            "outbox_failed": str(failed_events or 0),
            "outbox_dead_letter": str(dead_letters or 0),
        }

    console_path = Path("/app/apps/regent-console")
    if console_path.exists():
        app.mount("/console", StaticFiles(directory=console_path, html=True), name="console")

    def preview_file(project_id: uuid.UUID, release_id: uuid.UUID, filename: str) -> FileResponse:
        allowed = {"index.html": "text/html", "styles.css": "text/css", "app.js": "text/javascript"}
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
    return app


app = create_app()


def run() -> None:
    uvicorn.run("regent.api.main:app", host="0.0.0.0", port=8000)
