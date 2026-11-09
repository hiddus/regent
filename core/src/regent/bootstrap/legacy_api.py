"""Legacy Generated-App / preview / console API surface (temporary)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from regent.api.preview_security import PREVIEW_CONTENT_SECURITY_POLICY
from regent.config import Settings


def mount_legacy_routers(app: FastAPI) -> None:
    """Import and register old-product routers. Call only when mode includes legacy."""
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
    from regent.api.harness_evolution import router as harness_evolution_router
    from regent.api.human_tasks import router as human_tasks_router
    from regent.api.memories import router as memories_router
    from regent.api.observations import router as observations_router
    from regent.api.product_creation import router as product_creation_router
    from regent.api.public_deploy import router as public_deploy_router
    from regent.api.reports import router as reports_router
    from regent.api.runtime_profiles import router as runtime_profiles_router
    from regent.api.scheduler import router as scheduler_router
    from regent.api.self_improvement import router as self_improvement_router
    from regent.api.side_effects import router as side_effects_router
    from regent.api.tools import router as tools_router
    from regent.api.uploads import router as uploads_router
    from regent.api.webhooks import router as webhooks_router
    from regent.api.works import router as works_router

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
    app.include_router(human_tasks_router)
    app.include_router(uploads_router)
    app.include_router(webhooks_router)
    app.include_router(reports_router)
    app.include_router(public_deploy_router)


def mount_legacy_console(app: FastAPI) -> None:
    console_path = Path("/app/apps/regent-console/dist")
    if not console_path.exists():
        console_path = Path("/app/apps/regent-console")
    if console_path.exists() and (console_path / "index.html").exists():
        app.mount("/console", StaticFiles(directory=console_path, html=True), name="console")


def mount_legacy_preview_routes(app: FastAPI, settings: Settings) -> None:
    """Static preview files + runtime reverse proxy (was inlined in api.main)."""

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


def mount_legacy_create_guard(app: FastAPI, *, accept_new: bool) -> None:
    """When accept_new is False, block legacy create entrypoints with 410."""
    if accept_new:
        return

    blocked_exact = {
        ("POST", "/v1/goals"),
        ("POST", "/v1/goals/interpret"),
    }
    blocked_prefixes = (
        "/v1/goals/",  # only discovery-rounds create under product-creation uses this pattern carefully
    )

    @app.middleware("http")
    async def _legacy_stop_create(request: Request, call_next):  # type: ignore[no-untyped-def]
        method = request.method.upper()
        path = request.url.path.rstrip("/") or "/"
        # Normalize: FastAPI may expose /v1/goals without trailing slash
        key = (method, path)
        key_slash = (method, path if path.endswith("/") else path)
        if key in blocked_exact or (method, path + "/") in blocked_exact:
            return JSONResponse(
                status_code=410,
                content={
                    "error": {
                        "code": "LEGACY_CREATE_DISABLED",
                        "message": "Generated App creation is retired; set REGENT_LEGACY_ACCEPT_NEW=1 only for emergency rollback",
                    }
                },
            )
        if method == "POST" and path.startswith("/v1/goals/") and path.endswith(
            "/discovery-rounds"
        ):
            return JSONResponse(
                status_code=410,
                content={
                    "error": {
                        "code": "LEGACY_CREATE_DISABLED",
                        "message": "Legacy discovery rounds are retired during drain",
                    }
                },
            )
        _ = blocked_prefixes
        _ = key_slash
        return await call_next(request)


def mount_legacy_surface(app: FastAPI, settings: Settings) -> None:
    mount_legacy_create_guard(app, accept_new=bool(settings.legacy_accept_new))
    mount_legacy_console(app)
    mount_legacy_preview_routes(app, settings)
    mount_legacy_routers(app)
