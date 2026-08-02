"""Profile-aware preview deployment (P0-3 / P0-5 / R1).

Static profiles keep using StaticPreviewDeploymentProvider.
Runtime profiles materialize the verified workspace, require the Profile entry
module, start Profile ``start_command``, and only mark SUCCEEDED after HTTP
readiness. Evidence carries profile_hash + process pid/port for promotion gates.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from regent.agent.runtime_profile_v1 import parse_runtime_profile_v1
from regent.infrastructure.deployment import DeploymentRequest, DeploymentResult
from regent.infrastructure.preview_process import PreviewProcessSupervisor


def _local_path(uri: str | None) -> Path | None:
    raw = str(uri or "").strip()
    if not raw:
        return None
    if not raw.startswith("file:"):
        path = Path(raw)
        return path if path.exists() else None
    parsed = urlparse(raw)
    raw_path = unquote(parsed.path)
    if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
        raw_path = raw_path[1:]
    path = Path(raw_path)
    return path if path.exists() else None


def _entry_exists(root: Path, entry_module: str) -> bool:
    """Accept file path, dotted module, or package with __init__.py."""
    direct = root / entry_module
    if direct.is_file():
        return True
    dotted = entry_module.replace(".", "/")
    py_file = root / f"{dotted}.py"
    if py_file.is_file():
        return True
    pkg = root / dotted
    return pkg.is_dir() and (pkg / "__init__.py").is_file()


class RuntimePreviewDeploymentProvider:
    """Deploy verified workspaces according to Runtime Profile preview_type."""

    def __init__(
        self,
        preview_root: Path,
        *,
        static_provider: Any,
        base_url: str = "",
        process_supervisor: PreviewProcessSupervisor | None = None,
        readiness_timeout_seconds: float = 25.0,
    ) -> None:
        self._root = preview_root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._static = static_provider
        self._base_url = base_url.rstrip("/")
        self._deployments: dict[str, DeploymentResult] = {}
        self._deployment_ids: dict[str, str] = {}
        self._supervisor = process_supervisor or PreviewProcessSupervisor()
        self._readiness_timeout = float(readiness_timeout_seconds)

    async def deploy(self, request: DeploymentRequest) -> DeploymentResult:
        existing = self._deployments.get(request.idempotency_key)
        if existing is not None:
            return existing

        profile = parse_runtime_profile_v1(
            dict(request.acceptance_contract or {}).get("runtime_profile")
            or dict(request.success_criteria or {}).get("runtime_profile")
            or {}
        )
        preview_type = profile.preview_type if profile else "static"
        if preview_type == "static" or preview_type == "none":
            result = await self._static.deploy(request)
            self._deployments[request.idempotency_key] = result
            return result

        deployment_id = str(uuid.uuid4())
        profile_hash = profile.content_hash if profile else "none"
        target = self._root / "runtime" / deployment_id
        target.mkdir(parents=True, exist_ok=True)

        artifact = _local_path(request.build_artifact_uri)
        if artifact is None:
            result = DeploymentResult(
                external_request_id=request.idempotency_key,
                status="FAILED",
                evidence={
                    "provider": "runtime-preview",
                    "preview_type": preview_type,
                    "profile_hash": profile_hash,
                    "error": "build artifact not found for runtime preview",
                    "failure_code": "PREVIEW_FAILED",
                },
            )
            self._deployments[request.idempotency_key] = result
            return result

        try:
            if zipfile.is_zipfile(artifact):
                with zipfile.ZipFile(artifact) as zf:
                    zf.extractall(target)
            elif artifact.is_dir():
                shutil.copytree(artifact, target, dirs_exist_ok=True)
            else:
                raise ValueError("runtime preview artifact must be zip or directory")
        except Exception as exc:  # noqa: BLE001 — fail closed into DeploymentResult
            shutil.rmtree(target, ignore_errors=True)
            result = DeploymentResult(
                external_request_id=request.idempotency_key,
                status="FAILED",
                evidence={
                    "provider": "runtime-preview",
                    "preview_type": preview_type,
                    "profile_hash": profile_hash,
                    "error": f"materialize failed: {exc}",
                    "failure_code": "PREVIEW_FAILED",
                },
            )
            self._deployments[request.idempotency_key] = result
            return result

        entry_module = (profile.entry_module if profile else "") or ""
        if entry_module and not _entry_exists(target, entry_module):
            shutil.rmtree(target, ignore_errors=True)
            result = DeploymentResult(
                external_request_id=request.idempotency_key,
                status="FAILED",
                evidence={
                    "provider": "runtime-preview",
                    "preview_type": preview_type,
                    "profile_hash": profile_hash,
                    "entry_module": entry_module,
                    "error": f"entry module missing: {entry_module}",
                    "failure_code": "PREVIEW_FAILED",
                },
            )
            self._deployments[request.idempotency_key] = result
            return result

        start_command = (profile.start_command if profile else "") or ""
        if not start_command.strip():
            shutil.rmtree(target, ignore_errors=True)
            result = DeploymentResult(
                external_request_id=request.idempotency_key,
                status="FAILED",
                evidence={
                    "provider": "runtime-preview",
                    "preview_type": preview_type,
                    "profile_hash": profile_hash,
                    "error": "runtime preview requires Profile start_command",
                    "failure_code": "PREVIEW_FAILED",
                },
            )
            self._deployments[request.idempotency_key] = result
            return result

        (target / "profile_hash.txt").write_text(profile_hash, encoding="utf-8")
        (target / "entry.txt").write_text(
            f"{profile.entry_module}:{profile.entry_object}" if profile else "",
            encoding="utf-8",
        )
        deployment_hash = hashlib.sha256(
            f"{deployment_id}:{profile_hash}".encode()
        ).hexdigest()
        evidence: dict[str, Any] = {
            "provider": "runtime-preview",
            "preview_type": preview_type,
            "profile_hash": profile_hash,
            "deployment_hash": deployment_hash,
            "runtime": profile.project_shape if profile else "unknown",
            "workspace_path": str(target),
            "start_command": start_command,
            "live_preview": True,
        }
        if self._base_url:
            evidence["materialized_browse_url"] = (
                f"{self._base_url}/preview/runtime/{deployment_id}/"
            )

        try:
            handle = self._supervisor.start(
                deployment_id=deployment_id,
                workspace=target,
                start_command=start_command,
            )
            routes = list(
                (profile.readiness_routes if profile else ())
                or (profile.smoke_routes if profile else ())
                or ("/",)
            )
            ready = self._supervisor.wait_ready(
                handle,
                routes=[str(r) for r in routes],
                timeout_seconds=self._readiness_timeout,
            )
            evidence["port"] = handle.port
            evidence["pid"] = handle.process.pid
            evidence["rewritten_start_command"] = handle.command
            evidence["readiness"] = ready
            if not ready.get("ready"):
                self._supervisor.stop(deployment_id)
                shutil.rmtree(target, ignore_errors=True)
                result = DeploymentResult(
                    external_request_id=request.idempotency_key,
                    status="FAILED",
                    evidence={
                        **evidence,
                        "error": f"preview process not ready: {ready.get('error')}",
                        "failure_code": "PREVIEW_FAILED",
                    },
                )
                self._deployments[request.idempotency_key] = result
                return result
        except Exception as exc:  # noqa: BLE001 — fail closed
            self._supervisor.stop(deployment_id)
            shutil.rmtree(target, ignore_errors=True)
            result = DeploymentResult(
                external_request_id=request.idempotency_key,
                status="FAILED",
                evidence={
                    **evidence,
                    "error": f"preview start failed: {exc}",
                    "failure_code": "PREVIEW_FAILED",
                },
            )
            self._deployments[request.idempotency_key] = result
            return result

        live_url = f"http://127.0.0.1:{handle.port}/"
        result = DeploymentResult(
            external_request_id=request.idempotency_key,
            status="SUCCEEDED",
            endpoint=live_url,
            evidence=evidence,
        )
        self._deployments[request.idempotency_key] = result
        self._deployment_ids[request.idempotency_key] = deployment_id
        return result

    async def query(self, external_request_id: str) -> DeploymentResult:
        existing = self._deployments.get(external_request_id)
        if existing is not None:
            return existing
        if hasattr(self._static, "query"):
            return await self._static.query(external_request_id)
        return DeploymentResult(
            external_request_id=external_request_id,
            status="UNKNOWN",
            evidence={"error": "deployment not found"},
        )

    async def rollback(self, external_request_id: str, correlation_id: str) -> DeploymentResult:
        deployment_id = self._deployment_ids.pop(external_request_id, None)
        if deployment_id:
            self._supervisor.stop(deployment_id)
            target = self._root / "runtime" / deployment_id
            shutil.rmtree(target, ignore_errors=True)
        existing = self._deployments.get(external_request_id)
        if existing is not None:
            rolled = DeploymentResult(
                external_request_id=external_request_id,
                status="FAILED",
                evidence={
                    **dict(existing.evidence or {}),
                    "correlation_id": correlation_id,
                    "rolled_back": True,
                    "failure_code": "PREVIEW_ROLLED_BACK",
                },
            )
            self._deployments[external_request_id] = rolled
            return rolled
        if hasattr(self._static, "rollback"):
            return await self._static.rollback(external_request_id, correlation_id)
        return DeploymentResult(
            external_request_id=external_request_id,
            status="UNKNOWN",
            evidence={"correlation_id": correlation_id},
        )
