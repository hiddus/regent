"""Public deployment API endpoints."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from regent.config import get_settings
from regent.infrastructure.public_deployment import (
    DeploymentResult,
    NetlifyDeploymentProvider,
    TunnelDeploymentProvider,
    VercelDeploymentProvider,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/public-deploy", tags=["public-deploy"])


class DeployRequest(BaseModel):
    """Request to deploy to public hosting."""

    project_id: str
    provider: str = "tunnel"  # "vercel", "netlify", "tunnel"
    production: bool = False
    metadata: dict[str, Any] | None = None


class DeployResponse(BaseModel):
    """Response from public deployment."""

    success: bool
    url: str | None = None
    provider: str
    deployment_id: str | None = None
    error: str | None = None


class UndeployRequest(BaseModel):
    """Request to remove a public deployment."""

    deployment_id: str


class DeploymentInfo(BaseModel):
    """Information about a deployment."""

    id: str
    project_id: str
    url: str
    provider: str
    created_at: str | None = None
    metadata: dict[str, Any] | None = None


# Initialize providers based on config
def _get_provider(provider_name: str, local_port: int = 8000):
    """Get deployment provider by name."""
    settings = get_settings()

    if provider_name == "vercel":
        return VercelDeploymentProvider(
            api_token=settings.vercel_token,
            team_id=settings.vercel_team_id,
            production=False,  # Default to preview deployments
        )
    elif provider_name == "netlify":
        return NetlifyDeploymentProvider(
            auth_token=settings.netlify_token,
            production=False,
        )
    elif provider_name == "tunnel":
        return TunnelDeploymentProvider(
            local_port=local_port,
            tunnel_type=settings.tunnel_type or "cloudflared",
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_name}")


@router.post("/deploy", response_model=DeployResponse)
async def deploy_publicly(request: DeployRequest) -> DeployResponse:
    """Deploy project artifacts to public hosting.

    Supports:
    - Vercel: Requires VERCEL_TOKEN environment variable
    - Netlify: Requires NETLIFY_TOKEN environment variable
    - Tunnel: Uses cloudflared or ngrok to expose local preview
    """
    try:
        provider = _get_provider(request.provider)

        # Get build directory from workspace
        settings = get_settings()
        workspace_root = Path(settings.workspace_root)
        build_dir = workspace_root / request.project_id / "build"

        if not build_dir.exists():
            # Try preview directory
            build_dir = workspace_root / "previews" / request.project_id
            if not build_dir.exists():
                raise HTTPException(
                    status_code=404,
                    detail=f"No build artifacts found for project {request.project_id}",
                )

        result: DeploymentResult = await provider.deploy(
            project_id=request.project_id,
            build_dir=build_dir,
            metadata=request.metadata,
        )

        return DeployResponse(
            success=result.success,
            url=result.url,
            provider=result.provider,
            deployment_id=result.deployment_id,
            error=result.error,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Public deployment failed")
        return DeployResponse(
            success=False,
            error=str(e),
            provider=request.provider,
        )


@router.post("/undeploy")
async def undeploy(request: UndeployRequest) -> dict[str, bool]:
    """Remove a public deployment."""
    try:
        # Try all providers to find the deployment
        for provider_name in ["vercel", "netlify", "tunnel"]:
            provider = _get_provider(provider_name)
            if await provider.undeploy(request.deployment_id):
                return {"success": True}

        raise HTTPException(status_code=404, detail="Deployment not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Undeploy failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/deployments/{project_id}", response_model=list[DeploymentInfo])
async def list_deployments(project_id: str) -> list[DeploymentInfo]:
    """List all public deployments for a project."""
    deployments: list[DeploymentInfo] = []

    for provider_name in ["vercel", "netlify", "tunnel"]:
        try:
            provider = _get_provider(provider_name)
            provider_deployments = await provider.list_deployments(project_id)
            for dep in provider_deployments:
                deployments.append(
                    DeploymentInfo(
                        id=dep["id"],
                        project_id=dep["project_id"],
                        url=dep["url"],
                        provider=provider_name,
                        metadata=dep.get("metadata"),
                    )
                )
        except Exception:
            logger.warning(f"Failed to list {provider_name} deployments", exc_info=True)

    return deployments


@router.get("/providers")
async def list_providers() -> dict[str, list[str]]:
    """List available deployment providers."""
    import shutil

    providers = ["tunnel"]  # Tunnel is always available if configured

    if shutil.which("vercel"):
        providers.append("vercel")
    if shutil.which("netlify"):
        providers.append("netlify")

    return {"providers": providers}
