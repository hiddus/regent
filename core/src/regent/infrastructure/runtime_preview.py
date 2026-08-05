"""Profile-aware preview deployment (P0-3 / P0-5 / R1).

Static profiles keep using StaticPreviewDeploymentProvider.
Runtime profiles materialize the verified workspace, require the Profile entry
module, start Profile ``start_command``, and only mark SUCCEEDED after HTTP
readiness. Evidence carries profile_hash + process pid/port for promotion gates.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from regent.agent.runtime_profile_v1 import (
    RuntimeProfileV1,
    parse_runtime_profile_v1,
    profile_by_name,
)
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


def _default_runtime_profile(workspace: Path | None = None) -> RuntimeProfileV1:
    """Ship-first fallback when plan omitted runtime_profile."""
    blob = ""
    if workspace is not None:
        for rel in ("requirements.txt", "src/app.py", "app.py", "main.py"):
            path = workspace / rel
            if path.is_file():
                try:
                    blob += path.read_text(encoding="utf-8", errors="ignore")[:4000]
                except OSError:
                    pass
    lower = blob.lower()
    name = "fastapi-web-v1" if ("fastapi" in lower or "uvicorn" in lower) else "flask-web-v1"
    profile = profile_by_name(name)
    assert profile is not None
    return profile


def _normalize_flask_layout(workspace: Path) -> dict[str, Any]:
    """Copy root templates/static into src/ when Flask package is src.app.

    Soft drafts often put templates/ at repo root while ``Flask(__name__)`` with
    ``__name__='src.app'`` resolves templates under ``src/templates``.
    """
    notes: list[str] = []
    src = workspace / "src"
    if not src.is_dir():
        return {"changed": False, "notes": notes}
    for name in ("templates", "static"):
        root_side = workspace / name
        src_side = src / name
        if root_side.is_dir() and not src_side.exists():
            shutil.copytree(root_side, src_side)
            notes.append(f"copied {name}/ -> src/{name}/")
    return {"changed": bool(notes), "notes": notes}


def _runtime_packages(profile: RuntimeProfileV1 | None, requirements_text: str) -> list[str]:
    """Lean runtime packages for preview — skip pytest/dev to avoid PyPI timeouts."""
    shape = (profile.project_shape if profile else "") or ""
    lower = requirements_text.lower()
    if shape == "fastapi-web" or "fastapi" in lower or "uvicorn" in lower:
        return ["fastapi", "uvicorn"]
    # Default Flask web (golden path).
    return ["Flask>=3.0.0"]


def _ensure_preview_deps(workspace: Path, profile: RuntimeProfileV1 | None) -> dict[str, Any]:
    """Install lean runtime deps into a workspace-local venv for preview."""
    reqs = workspace / "requirements.txt"
    req_text = ""
    if reqs.is_file():
        try:
            req_text = reqs.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            req_text = ""
    if not req_text.strip() and profile is None:
        return {"skipped": True, "reason": "no_requirements"}
    venv = workspace / ".preview-venv"
    marker = venv / ".regent-ready"
    if marker.is_file():
        return {"skipped": True, "reason": "venv_ready", "venv": str(venv)}
    steps: list[dict[str, Any]] = []
    create = subprocess.run(
        [sys.executable, "-m", "venv", str(venv)],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    steps.append({"cmd": "venv", "code": create.returncode, "err": (create.stderr or "")[-400:]})
    if create.returncode != 0:
        return {"ok": False, "steps": steps}
    if os.name == "nt":
        pip = venv / "Scripts" / "pip.exe"
        venv_bin = venv / "Scripts"
        venv_python = venv / "Scripts" / "python.exe"
    else:
        pip = venv / "bin" / "pip"
        venv_bin = venv / "bin"
        venv_python = venv / "bin" / "python"
    packages = _runtime_packages(profile, req_text)
    argv = [
        str(pip),
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--default-timeout=60",
        *packages,
    ]
    install = None
    for attempt in range(1, 4):
        install = subprocess.run(
            argv,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
            env={
                **os.environ,
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "HOME": str(workspace / ".preview-home"),
            },
        )
        steps.append(
            {
                "cmd": " ".join(argv),
                "attempt": attempt,
                "code": install.returncode,
                "err": (install.stderr or "")[-600:],
                "out": (install.stdout or "")[-300:],
            }
        )
        if install.returncode == 0:
            break
    if install is None or install.returncode != 0:
        return {"ok": False, "steps": steps, "venv_python": str(venv_python)}
    marker.write_text("ok", encoding="utf-8")
    return {
        "ok": True,
        "steps": steps,
        "venv": str(venv),
        "venv_bin": str(venv_bin),
        "venv_python": str(venv_python),
        "packages": packages,
    }


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
        # Ship-first: default missing profile to runtime process, never static zip greenwash.
        preview_type = profile.preview_type if profile else "runtime"
        if preview_type == "static" or preview_type == "none":
            result = await self._static.deploy(request)
            self._deployments[request.idempotency_key] = result
            return result

        deployment_id = str(uuid.uuid4())
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
                    "profile_hash": profile.content_hash if profile else "none",
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
                    "profile_hash": profile.content_hash if profile else "none",
                    "error": f"materialize failed: {exc}",
                    "failure_code": "PREVIEW_FAILED",
                },
            )
            self._deployments[request.idempotency_key] = result
            return result

        # Soft/agentic plans often omit runtime_profile; infer after materialize.
        if profile is None or not str(profile.start_command or "").strip():
            profile = _default_runtime_profile(target)
            preview_type = profile.preview_type if profile else "runtime"

        layout_fix = _normalize_flask_layout(target)

        profile_hash = profile.content_hash if profile else "none"
        entry_module = (profile.entry_module if profile else "") or ""
        if entry_module and not _entry_exists(target, entry_module):
            # Common soft-draft layouts: app.py at root instead of src/app.py
            for alt in ("app.py", "src/app.py", "main.py", "src/main.py"):
                if (target / alt).is_file():
                    # Keep start_command from profile; entry check only gates missing files.
                    entry_module = ""
                    break
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
            "layout_fix": layout_fix,
        }
        if self._base_url:
            evidence["materialized_browse_url"] = (
                f"{self._base_url}/preview/runtime/{deployment_id}/"
            )

        install_info = _ensure_preview_deps(target, profile)
        evidence["deps"] = install_info
        if install_info.get("ok") is False:
            shutil.rmtree(target, ignore_errors=True)
            result = DeploymentResult(
                external_request_id=request.idempotency_key,
                status="FAILED",
                evidence={
                    **evidence,
                    "error": "preview dependency install failed",
                    "failure_code": "PREVIEW_FAILED",
                },
            )
            self._deployments[request.idempotency_key] = result
            return result

        start_env: dict[str, str] = {}
        venv_bin = install_info.get("venv_bin")
        if not venv_bin and install_info.get("venv"):
            venv_path = Path(str(install_info["venv"]))
            venv_bin = str(
                venv_path / ("Scripts" if os.name == "nt" else "bin")
            )
        if venv_bin:
            start_env["PATH"] = f"{venv_bin}{os.pathsep}{os.environ.get('PATH', '')}"
            venv_python = Path(venv_bin) / ("python.exe" if os.name == "nt" else "python")
            if venv_python.is_file() and start_command.strip().startswith("python "):
                start_command = f"{venv_python} {start_command.strip()[7:]}"

        try:
            handle = self._supervisor.start(
                deployment_id=deployment_id,
                workspace=target,
                start_command=start_command,
                env=start_env or None,
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
            (target / ".regent-preview-port").write_text(str(handle.port), encoding="utf-8")
            advertise = (
                os.environ.get("REGENT_PREVIEW_ADVERTISE_HOST")
                or os.environ.get("HOSTNAME")
                or "127.0.0.1"
            )
            evidence["advertise_host"] = advertise
            evidence["docker_endpoint"] = f"http://{advertise}:{handle.port}/"
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
