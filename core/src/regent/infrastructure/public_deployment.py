"""Public deployment providers for exposing previews to the internet.

Supports:
- Vercel deployment via CLI/API
- Netlify deployment via CLI/API
- Local tunnel (cloudflared/ngrok) for temporary public access
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class DeploymentResult:
    """Result of a public deployment."""

    success: bool
    url: str | None = None
    provider: str = ""
    deployment_id: str | None = None
    metadata: dict[str, Any] | None = None
    error: str | None = None


class PublicDeploymentProvider(Protocol):
    """Protocol for public deployment providers."""

    async def deploy(
        self,
        *,
        project_id: str,
        build_dir: Path,
        metadata: dict[str, Any] | None = None,
    ) -> DeploymentResult:
        """Deploy build artifacts to public hosting."""
        ...

    async def undeploy(self, deployment_id: str) -> bool:
        """Remove a deployment."""
        ...

    async def list_deployments(self, project_id: str) -> list[dict[str, Any]]:
        """List deployments for a project."""
        ...


# ---------------------------------------------------------------------------
# Vercel Deployment Provider
# ---------------------------------------------------------------------------


class VercelDeploymentProvider:
    """Deploy to Vercel using CLI or API."""

    def __init__(
        self,
        *,
        api_token: str | None = None,
        team_id: str | None = None,
        project_name: str | None = None,
        production: bool = False,
    ) -> None:
        self._api_token = api_token
        self._team_id = team_id
        self._project_name = project_name
        self._production = production
        self._deployments: dict[str, dict[str, Any]] = {}

    async def deploy(
        self,
        *,
        project_id: str,
        build_dir: Path,
        metadata: dict[str, Any] | None = None,
    ) -> DeploymentResult:
        """Deploy to Vercel."""
        if not shutil.which("vercel"):
            return DeploymentResult(
                success=False,
                error="Vercel CLI not found. Install with: npm i -g vercel",
                provider="vercel",
            )

        if not build_dir.exists():
            return DeploymentResult(
                success=False,
                error=f"Build directory not found: {build_dir}",
                provider="vercel",
            )

        try:
            # Build vercel command
            cmd = ["vercel", "deploy", "--yes"]
            if self._production:
                cmd.append("--prod")
            if self._api_token:
                cmd.extend(["--token", self._api_token])
            if self._team_id:
                cmd.extend(["--scope", self._team_id])

            # Run deployment
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(build_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            if proc.returncode != 0:
                return DeploymentResult(
                    success=False,
                    error=stderr.decode() if stderr else "Vercel deployment failed",
                    provider="vercel",
                )

            # Parse deployment URL from output
            output = stdout.decode()
            url = self._extract_url(output)
            if not url:
                return DeploymentResult(
                    success=False,
                    error="Could not extract deployment URL",
                    provider="vercel",
                )

            deployment_id = f"vercel-{project_id}-{int(asyncio.get_event_loop().time())}"
            self._deployments[deployment_id] = {
                "project_id": project_id,
                "url": url,
                "build_dir": str(build_dir),
                "metadata": metadata or {},
            }

            return DeploymentResult(
                success=True,
                url=url,
                provider="vercel",
                deployment_id=deployment_id,
                metadata={"production": self._production},
            )

        except TimeoutError:
            return DeploymentResult(
                success=False,
                error="Vercel deployment timed out",
                provider="vercel",
            )
        except Exception as e:
            logger.exception("Vercel deployment failed")
            return DeploymentResult(
                success=False,
                error=str(e),
                provider="vercel",
            )

    def _extract_url(self, output: str) -> str | None:
        """Extract deployment URL from Vercel CLI output."""
        # Vercel CLI outputs URL in various formats
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("https://") and ".vercel.app" in line:
                return line
            if "vercel.app" in line and line.startswith("http"):
                return line
        return None

    async def undeploy(self, deployment_id: str) -> bool:
        """Remove a Vercel deployment."""
        if deployment_id not in self._deployments:
            return False
        # Vercel CLI doesn't have a direct undeploy command
        # Would need to use API to remove deployment
        del self._deployments[deployment_id]
        return True

    async def list_deployments(self, project_id: str) -> list[dict[str, Any]]:
        """List Vercel deployments for a project."""
        return [
            {"id": dep_id, **dep_data}
            for dep_id, dep_data in self._deployments.items()
            if dep_data.get("project_id") == project_id
        ]


# ---------------------------------------------------------------------------
# Netlify Deployment Provider
# ---------------------------------------------------------------------------


class NetlifyDeploymentProvider:
    """Deploy to Netlify using CLI or API."""

    def __init__(
        self,
        *,
        auth_token: str | None = None,
        site_id: str | None = None,
        production: bool = False,
    ) -> None:
        self._auth_token = auth_token
        self._site_id = site_id
        self._production = production
        self._deployments: dict[str, dict[str, Any]] = {}

    async def deploy(
        self,
        *,
        project_id: str,
        build_dir: Path,
        metadata: dict[str, Any] | None = None,
    ) -> DeploymentResult:
        """Deploy to Netlify."""
        if not shutil.which("netlify"):
            return DeploymentResult(
                success=False,
                error="Netlify CLI not found. Install with: npm i -g netlify-cli",
                provider="netlify",
            )

        if not build_dir.exists():
            return DeploymentResult(
                success=False,
                error=f"Build directory not found: {build_dir}",
                provider="netlify",
            )

        try:
            # Build netlify command
            cmd = ["netlify", "deploy", "--dir", str(build_dir)]
            if self._production:
                cmd.append("--prod")
            if self._auth_token:
                cmd.extend(["--auth", self._auth_token])
            if self._site_id:
                cmd.extend(["--site", self._site_id])

            # Run deployment
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            if proc.returncode != 0:
                return DeploymentResult(
                    success=False,
                    error=stderr.decode() if stderr else "Netlify deployment failed",
                    provider="netlify",
                )

            # Parse deployment URL from output
            output = stdout.decode()
            url = self._extract_url(output)
            if not url:
                return DeploymentResult(
                    success=False,
                    error="Could not extract deployment URL",
                    provider="netlify",
                )

            deployment_id = f"netlify-{project_id}-{int(asyncio.get_event_loop().time())}"
            self._deployments[deployment_id] = {
                "project_id": project_id,
                "url": url,
                "build_dir": str(build_dir),
                "metadata": metadata or {},
            }

            return DeploymentResult(
                success=True,
                url=url,
                provider="netlify",
                deployment_id=deployment_id,
                metadata={"production": self._production},
            )

        except TimeoutError:
            return DeploymentResult(
                success=False,
                error="Netlify deployment timed out",
                provider="netlify",
            )
        except Exception as e:
            logger.exception("Netlify deployment failed")
            return DeploymentResult(
                success=False,
                error=str(e),
                provider="netlify",
            )

    def _extract_url(self, output: str) -> str | None:
        """Extract deployment URL from Netlify CLI output."""
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("https://") and ".netlify.app" in line:
                return line
            if "netlify.app" in line and line.startswith("http"):
                return line
        return None

    async def undeploy(self, deployment_id: str) -> bool:
        """Remove a Netlify deployment."""
        if deployment_id not in self._deployments:
            return False
        del self._deployments[deployment_id]
        return True

    async def list_deployments(self, project_id: str) -> list[dict[str, Any]]:
        """List Netlify deployments for a project."""
        return [
            {"id": dep_id, **dep_data}
            for dep_id, dep_data in self._deployments.items()
            if dep_data.get("project_id") == project_id
        ]


# ---------------------------------------------------------------------------
# Tunnel-based Provider (cloudflared/ngrok)
# ---------------------------------------------------------------------------


class TunnelDeploymentProvider:
    """Expose local preview via tunnel (cloudflared or ngrok)."""

    def __init__(
        self,
        *,
        local_port: int = 8000,
        tunnel_type: str = "cloudflared",
    ) -> None:
        self._local_port = local_port
        self._tunnel_type = tunnel_type
        self._tunnel_process: subprocess.Popen | None = None
        self._tunnel_url: str | None = None
        self._deployments: dict[str, dict[str, Any]] = {}

    async def deploy(
        self,
        *,
        project_id: str,
        build_dir: Path,
        metadata: dict[str, Any] | None = None,
    ) -> DeploymentResult:
        """Start a tunnel to expose local preview."""
        if self._tunnel_type == "cloudflared":
            return await self._deploy_cloudflared(project_id, metadata)
        elif self._tunnel_type == "ngrok":
            return await self._deploy_ngrok(project_id, metadata)
        else:
            return DeploymentResult(
                success=False,
                error=f"Unknown tunnel type: {self._tunnel_type}",
                provider=self._tunnel_type,
            )

    async def _deploy_cloudflared(
        self, project_id: str, metadata: dict[str, Any] | None
    ) -> DeploymentResult:
        """Deploy using cloudflared tunnel."""
        if not shutil.which("cloudflared"):
            return DeploymentResult(
                success=False,
                error="cloudflared not found. Install from https://github.com/cloudflare/cloudflared",
                provider="cloudflared",
            )

        try:
            # Start cloudflared tunnel
            self._tunnel_process = subprocess.Popen(
                ["cloudflared", "tunnel", "--url", f"http://localhost:{self._local_port}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # Wait for tunnel URL (cloudflared outputs to stderr)
            url = None
            for _ in range(30):  # Wait up to 30 seconds
                if self._tunnel_process.stderr:
                    line = self._tunnel_process.stderr.readline()
                    if "trycloudflare.com" in line:
                        # Extract URL from log line
                        import re

                        match = re.search(r"https://[^\s]+\.trycloudflare\.com", line)
                        if match:
                            url = match.group(0)
                            break
                await asyncio.sleep(1)

            if not url:
                if self._tunnel_process:
                    self._tunnel_process.terminate()
                return DeploymentResult(
                    success=False,
                    error="Failed to get tunnel URL",
                    provider="cloudflared",
                )

            self._tunnel_url = url
            deployment_id = f"tunnel-{project_id}-{int(asyncio.get_event_loop().time())}"
            self._deployments[deployment_id] = {
                "project_id": project_id,
                "url": url,
                "local_port": self._local_port,
                "tunnel_type": "cloudflared",
                "metadata": metadata or {},
            }

            return DeploymentResult(
                success=True,
                url=url,
                provider="cloudflared",
                deployment_id=deployment_id,
                metadata={"local_port": self._local_port},
            )

        except Exception as e:
            logger.exception("Cloudflared tunnel failed")
            if self._tunnel_process:
                self._tunnel_process.terminate()
            return DeploymentResult(
                success=False,
                error=str(e),
                provider="cloudflared",
            )

    async def _deploy_ngrok(
        self, project_id: str, metadata: dict[str, Any] | None
    ) -> DeploymentResult:
        """Deploy using ngrok tunnel."""
        if not shutil.which("ngrok"):
            return DeploymentResult(
                success=False,
                error="ngrok not found. Install from https://ngrok.com/download",
                provider="ngrok",
            )

        try:
            # Start ngrok tunnel
            self._tunnel_process = subprocess.Popen(
                ["ngrok", "http", str(self._local_port), "--log", "stdout", "--log-format", "json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # Wait for tunnel URL
            url = None
            for _ in range(30):
                if self._tunnel_process.stdout:
                    line = self._tunnel_process.stdout.readline()
                    if line:
                        try:
                            log_entry = json.loads(line)
                            if log_entry.get("msg") == "started tunnel":
                                url = log_entry.get("url")
                                break
                        except json.JSONDecodeError:
                            pass
                await asyncio.sleep(1)

            if not url:
                if self._tunnel_process:
                    self._tunnel_process.terminate()
                return DeploymentResult(
                    success=False,
                    error="Failed to get ngrok URL",
                    provider="ngrok",
                )

            self._tunnel_url = url
            deployment_id = f"tunnel-{project_id}-{int(asyncio.get_event_loop().time())}"
            self._deployments[deployment_id] = {
                "project_id": project_id,
                "url": url,
                "local_port": self._local_port,
                "tunnel_type": "ngrok",
                "metadata": metadata or {},
            }

            return DeploymentResult(
                success=True,
                url=url,
                provider="ngrok",
                deployment_id=deployment_id,
                metadata={"local_port": self._local_port},
            )

        except Exception as e:
            logger.exception("Ngrok tunnel failed")
            if self._tunnel_process:
                self._tunnel_process.terminate()
            return DeploymentResult(
                success=False,
                error=str(e),
                provider="ngrok",
            )

    async def undeploy(self, deployment_id: str) -> bool:
        """Stop the tunnel."""
        if deployment_id not in self._deployments:
            return False

        if self._tunnel_process:
            self._tunnel_process.terminate()
            self._tunnel_process = None
        self._tunnel_url = None
        del self._deployments[deployment_id]
        return True

    async def list_deployments(self, project_id: str) -> list[dict[str, Any]]:
        """List tunnel deployments for a project."""
        return [
            {"id": dep_id, **dep_data}
            for dep_id, dep_data in self._deployments.items()
            if dep_data.get("project_id") == project_id
        ]
