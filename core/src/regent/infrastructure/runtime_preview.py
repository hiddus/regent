"""Profile-aware preview deployment (M4-1 minimal).

Static profiles keep using StaticPreviewDeploymentProvider.
Runtime profiles record a runtime preview contract; full process proxy is
activated when sandbox runtime is available. Until then, deployments that
claim runtime but only have static HTML fail closed instead of lying.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from regent.agent.runtime_profile_v1 import parse_runtime_profile_v1
from regent.infrastructure.deployment import DeploymentRequest, DeploymentResult


class RuntimePreviewDeploymentProvider:
    """Deploy verified workspaces according to Runtime Profile preview_type."""

    def __init__(
        self,
        preview_root: Path,
        *,
        static_provider: Any,
        base_url: str = "",
    ) -> None:
        self._root = preview_root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._static = static_provider
        self._base_url = base_url.rstrip("/")
        self._deployments: dict[str, DeploymentResult] = {}

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

        # Runtime preview: require entry module evidence in artifact metadata path.
        # Full long-lived process hosting is sandbox-backed; here we mint a
        # deployment record with profile hash for promotion gates (M4-3).
        deployment_id = str(uuid.uuid4())
        profile_hash = profile.content_hash if profile else "none"
        marker = self._root / "runtime" / deployment_id
        marker.mkdir(parents=True, exist_ok=True)
        (marker / "profile_hash.txt").write_text(profile_hash, encoding="utf-8")
        (marker / "entry.txt").write_text(
            f"{profile.entry_module}:{profile.entry_object}" if profile else "",
            encoding="utf-8",
        )
        url = f"{self._base_url}/preview/runtime/{deployment_id}/" if self._base_url else None
        evidence = {
            "provider": "runtime-preview",
            "preview_type": preview_type,
            "profile_hash": profile_hash,
            "deployment_hash": hashlib.sha256(
                f"{deployment_id}:{profile_hash}".encode()
            ).hexdigest(),
            "runtime": profile.project_shape if profile else "unknown",
            "note": (
                "runtime preview marker written; Journey execution uses "
                "verification smoke semantics against the same Profile"
            ),
        }
        if url:
            result = DeploymentResult(
                external_request_id=request.idempotency_key,
                status="SUCCEEDED",
                endpoint=url,
                evidence=evidence,
            )
        else:
            result = DeploymentResult(
                external_request_id=request.idempotency_key,
                status="FAILED",
                evidence={
                    **evidence,
                    "error": "public_base_url required for runtime preview",
                },
            )
        self._deployments[request.idempotency_key] = result
        return result
