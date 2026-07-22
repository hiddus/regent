import hashlib
from pathlib import Path

import pytest
from regent.application.p1_ports import (
    DependencyMaterializationRequest,
    SandboxBuildRequest,
)
from regent.infrastructure.sandbox import (
    DockerDependencyMaterializer,
    DockerSandboxDriver,
)


@pytest.mark.asyncio
async def test_sandbox_command_is_offline_and_restricted(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    async def runner(command: list[str]) -> int:
        commands.append(command)
        return 137

    source = tmp_path / "source.zip"
    bundle = tmp_path / "bundle"
    source.write_bytes(b"source")
    bundle.write_bytes(b"dependencies")
    driver = DockerSandboxDriver(
        root=tmp_path / "builds", image="sandbox@sha256:test", runner=runner
    )
    result = await driver.build(
        SandboxBuildRequest(
            workspace_snapshot_uri=source.as_uri(),
            dependency_bundle_uri=bundle.as_uri(),
            dependency_bundle_hash=hashlib.sha256(bundle.read_bytes()).hexdigest(),
            runtime_profile_ref="python-web-v1",
            runtime_profile_hash="a" * 64,
            idempotency_key="build-1",
            correlation_id="corr",
        )
    )
    command = commands[0]
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--user") + 1] == "65532:65532"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert result.status == "UNKNOWN"


@pytest.mark.asyncio
async def test_dependency_materializer_fails_closed_without_proxy(tmp_path: Path) -> None:
    async def permit_validator(_permit_id: str, _action: str) -> None:
        raise AssertionError("permit must not be consumed before proxy validation")

    materializer = DockerDependencyMaterializer(
        root=tmp_path,
        image="resolver@sha256:test",
        egress_proxy=None,
        permit_validator=permit_validator,
    )
    with pytest.raises(PermissionError):
        await materializer.materialize(
            DependencyMaterializationRequest(
                source_hash="a" * 64,
                dependency_intents=[{"name": "fastapi", "version": "0.115.0", "sha256": "b" * 64}],
                runtime_profile_ref="python-web-v1",
                permit_id="permit",
                idempotency_key="deps-1",
                correlation_id="corr",
            )
        )


def test_sandbox_rejects_dependency_hash_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    bundle = tmp_path / "bundle"
    source.write_bytes(b"source")
    bundle.write_bytes(b"dependencies")
    driver = DockerSandboxDriver(root=tmp_path / "builds", image="sandbox")
    request = SandboxBuildRequest(
        workspace_snapshot_uri=source.as_uri(),
        dependency_bundle_uri=bundle.as_uri(),
        dependency_bundle_hash="0" * 64,
        runtime_profile_ref="python-web-v1",
        runtime_profile_hash="a" * 64,
        idempotency_key="build",
        correlation_id="corr",
    )
    with pytest.raises(ValueError):
        import asyncio

        asyncio.run(driver.build(request))
