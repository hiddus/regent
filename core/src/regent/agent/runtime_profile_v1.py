"""Runtime Profile v1 schema helpers (M2-1).

Infrastructure contracts (entry, start, health, tests, preview type) live here —
not in user-facing success_criteria.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

RUNTIME_PROFILE_SCHEMA_VERSION = "runtime-profile/v1"

PreviewType = Literal["static", "runtime", "none"]
ProjectShape = Literal["static-web", "flask-web", "fastapi-web", "python-api", "exploratory"]


@dataclass(frozen=True, slots=True)
class RuntimeProfileV1:
    name: str
    version: str
    schema_version: str
    project_shape: ProjectShape
    entry_module: str
    entry_object: str
    start_command: str
    workdir: str
    health_routes: tuple[str, ...]
    readiness_routes: tuple[str, ...]
    smoke_routes: tuple[str, ...]
    install_command: str | None
    test_command: str | None
    require_tests: bool
    allow_network: bool
    preview_type: PreviewType
    network_allowlist: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "version": self.version,
            "project_shape": self.project_shape,
            "entry_module": self.entry_module,
            "entry_object": self.entry_object,
            "start_command": self.start_command,
            "workdir": self.workdir,
            "health_routes": list(self.health_routes),
            "readiness_routes": list(self.readiness_routes),
            "smoke_routes": list(self.smoke_routes),
            "install_command": self.install_command,
            "test_command": self.test_command,
            "require_tests": self.require_tests,
            "allow_network": self.allow_network,
            "preview_type": self.preview_type,
            "network_allowlist": list(self.network_allowlist),
        }

    @property
    def content_hash(self) -> str:
        blob = json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def parse_runtime_profile_v1(raw: dict[str, Any] | None) -> RuntimeProfileV1 | None:
    if not raw:
        return None
    # Accept nested abi or flat profile.
    data = dict(raw.get("abi_json") or raw)
    if raw.get("name") and "name" not in data:
        data["name"] = raw["name"]
    if raw.get("version") and "version" not in data:
        data["version"] = raw["version"]
    schema = str(data.get("schema_version") or raw.get("schema_version") or "")
    if schema and schema != RUNTIME_PROFILE_SCHEMA_VERSION:
        # Legacy abi without schema_version: coerce when shape fields exist.
        if "project_shape" not in data and "language" in data:
            return coerce_legacy_abi(raw)
        if "project_shape" not in data:
            return None
    if "project_shape" not in data:
        return coerce_legacy_abi(raw)
    return RuntimeProfileV1(
        name=str(data.get("name") or raw.get("name") or "unnamed"),
        version=str(data.get("version") or raw.get("version") or "1"),
        schema_version=RUNTIME_PROFILE_SCHEMA_VERSION,
        project_shape=str(data.get("project_shape") or "flask-web"),  # type: ignore[arg-type]
        entry_module=str(data.get("entry_module") or "src.app"),
        entry_object=str(data.get("entry_object") or "app"),
        start_command=str(data.get("start_command") or "python -m flask --app src.app run"),
        workdir=str(data.get("workdir") or "."),
        health_routes=tuple(str(x) for x in (data.get("health_routes") or ())),
        readiness_routes=tuple(str(x) for x in (data.get("readiness_routes") or ())),
        smoke_routes=tuple(str(x) for x in (data.get("smoke_routes") or ("/",))),
        install_command=(
            str(data["install_command"])
            if data.get("install_command")
            else None
        ),
        test_command=(str(data["test_command"]) if data.get("test_command") else None),
        require_tests=bool(data.get("require_tests", True)),
        allow_network=bool(data.get("allow_network", False)),
        preview_type=str(data.get("preview_type") or "runtime"),  # type: ignore[arg-type]
        network_allowlist=tuple(str(x) for x in (data.get("network_allowlist") or ())),
    )


def coerce_legacy_abi(raw: dict[str, Any]) -> RuntimeProfileV1:
    abi = dict(raw.get("abi_json") or raw)
    language = str(abi.get("language") or "python")
    name = str(raw.get("name") or "legacy")
    version = str(raw.get("version") or "1")
    if language == "static" or abi.get("static_files"):
        return RuntimeProfileV1(
            name=name,
            version=version,
            schema_version=RUNTIME_PROFILE_SCHEMA_VERSION,
            project_shape="static-web",
            entry_module="",
            entry_object="",
            start_command="",
            workdir=".",
            health_routes=(),
            readiness_routes=(),
            smoke_routes=("/",),
            install_command=None,
            test_command=None,
            require_tests=False,
            allow_network=False,
            preview_type="static",
            network_allowlist=(),
        )
    if abi.get("asgi") or "fastapi" in name.lower():
        return RuntimeProfileV1(
            name=name,
            version=version,
            schema_version=RUNTIME_PROFILE_SCHEMA_VERSION,
            project_shape="fastapi-web",
            entry_module="src.app",
            entry_object="app",
            start_command="uvicorn src.app:app --host 127.0.0.1 --port 8080",
            workdir=".",
            health_routes=("/health",),
            readiness_routes=(),
            smoke_routes=("/",),
            install_command="pip install -r requirements.txt",
            test_command="pytest -q --tb=line",
            require_tests=True,
            allow_network=False,
            preview_type="runtime",
            network_allowlist=(),
        )
    return RuntimeProfileV1(
        name=name,
        version=version,
        schema_version=RUNTIME_PROFILE_SCHEMA_VERSION,
        project_shape="flask-web",
        entry_module="src.app",
        entry_object="app",
        start_command="python -m flask --app src.app run --host 127.0.0.1 --port 8080",
        workdir=".",
        health_routes=(),
        readiness_routes=(),
        smoke_routes=("/",),
        install_command="pip install -r requirements.txt",
        test_command="pytest -q --tb=line",
        require_tests=True,
        allow_network=False,
        preview_type="runtime",
        network_allowlist=(),
    )


# Certified golden profiles for M2 exit gate.
CERTIFIED_RUNTIME_PROFILES_V1: tuple[RuntimeProfileV1, ...] = (
    RuntimeProfileV1(
        name="static-web-v1",
        version="1",
        schema_version=RUNTIME_PROFILE_SCHEMA_VERSION,
        project_shape="static-web",
        entry_module="",
        entry_object="",
        start_command="",
        workdir=".",
        health_routes=(),
        readiness_routes=(),
        smoke_routes=("/",),
        install_command=None,
        test_command=None,
        require_tests=False,
        allow_network=False,
        preview_type="static",
        network_allowlist=(),
    ),
    RuntimeProfileV1(
        name="flask-web-v1",
        version="1",
        schema_version=RUNTIME_PROFILE_SCHEMA_VERSION,
        project_shape="flask-web",
        entry_module="src.app",
        entry_object="app",
        start_command="python -m flask --app src.app run --host 127.0.0.1 --port 8080",
        workdir=".",
        health_routes=(),
        readiness_routes=(),
        smoke_routes=("/",),
        install_command="pip install -r requirements.txt",
        test_command="pytest -q --tb=line",
        require_tests=True,
        allow_network=False,
        preview_type="runtime",
        network_allowlist=(),
    ),
    RuntimeProfileV1(
        name="fastapi-web-v1",
        version="1",
        schema_version=RUNTIME_PROFILE_SCHEMA_VERSION,
        project_shape="fastapi-web",
        entry_module="src.app",
        entry_object="app",
        start_command="uvicorn src.app:app --host 127.0.0.1 --port 8080",
        workdir=".",
        health_routes=("/health",),
        readiness_routes=("/ready",),
        smoke_routes=("/", "/health"),
        install_command="pip install -r requirements.txt",
        test_command="pytest -q --tb=line",
        require_tests=True,
        allow_network=False,
        preview_type="runtime",
        network_allowlist=(),
    ),
    RuntimeProfileV1(
        name="exploratory-web-v1",
        version="1",
        schema_version=RUNTIME_PROFILE_SCHEMA_VERSION,
        project_shape="exploratory",
        entry_module="src.app",
        entry_object="app",
        start_command="python -m flask --app src.app run --host 127.0.0.1 --port 8080",
        workdir=".",
        health_routes=(),
        readiness_routes=(),
        smoke_routes=("/",),
        install_command="pip install -r requirements.txt",
        test_command=None,
        require_tests=False,
        allow_network=False,
        preview_type="runtime",
        network_allowlist=(),
    ),
)


def profile_by_name(name: str) -> RuntimeProfileV1 | None:
    for item in CERTIFIED_RUNTIME_PROFILES_V1:
        if item.name == name:
            return item
    return None
