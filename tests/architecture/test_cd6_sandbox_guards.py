"""CD-6.5 regression guards for agent sandbox + API surface (T1–T6)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from regent.config import Settings
from regent.infrastructure.sandbox import (
    DockerSandboxDriver,
    apply_host_path_map,
    build_agent_sandbox,
    parse_host_path_map,
    resolve_agent_sandbox_user,
)

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "core" / "src"


def test_t1_routers_mounted_via_openapi() -> None:
    """T1: F-1 guard — use OpenAPI paths, not app.routes (_IncludedRouter)."""
    from regent.api.main import create_app

    paths = set(create_app().openapi()["paths"])
    assert any("human-tasks" in p for p in paths)
    assert "/v1/uploads" in paths
    assert any(p.startswith("/v1/webhooks") for p in paths)
    assert any(p.startswith("/v1/reports") for p in paths)
    assert any(p.startswith("/v1/public-deploy") for p in paths)


def test_t3_production_forbids_local_sandbox_settings() -> None:
    """T3: Settings layer raises ValueError (not RuntimeError)."""
    with pytest.raises(ValueError, match="sandbox_mode must be 'docker'"):
        Settings(_env_file=None, environment="production", sandbox_mode="local")


def test_t3_build_agent_sandbox_runtime_error_on_production_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Cfg:
        environment = "production"
        sandbox_mode = "local"
        build_root = "/tmp/regent-builds"
        agent_sandbox_image = "regent-agent-exec-v1:1"
        host_path_map = ""
        agent_sandbox_uid = "65532:65532"

    with pytest.raises(RuntimeError, match="forbidden in production"):
        build_agent_sandbox(settings=_Cfg())


def test_t6_workspace_exec_argv_contract(tmp_path: Path) -> None:
    """T6: --entrypoint / network none / cap-drop / non-root user / mapped mount."""
    driver = DockerSandboxDriver(
        root=tmp_path / "builds",
        image="regent-agent-exec-v1:1",
        host_path_map={"/var/lib/regent": "/opt/regent"},
        run_as_user="1234:1234",
    )
    argv = driver.workspace_exec_command(
        Path("/var/lib/regent/workspaces/demo"),
        "echo ok",
        allow_network=False,
    )
    assert argv[argv.index("--entrypoint") + 1] == "sh"
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in argv
    assert argv[argv.index("--user") + 1] == "1234:1234"
    assert "0:" not in argv[argv.index("--user") + 1]
    mount = argv[argv.index("--mount") + 1]
    assert "src=/opt/regent/workspaces/demo" in mount
    assert argv[argv.index("--entrypoint") + 2] == "regent-agent-exec-v1:1"
    assert "-lc" in argv
    assert "echo ok" in argv


def test_t6_fail_closed_without_host_path_map_in_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "regent.infrastructure.sandbox.running_in_container", lambda: True
    )
    driver = DockerSandboxDriver(
        root=tmp_path / "builds",
        image="regent-agent-exec-v1:1",
        host_path_map={},
        require_host_path_map_in_container=True,
    )
    with pytest.raises(RuntimeError, match="REGENT_HOST_PATH_MAP"):
        driver.workspace_exec_command(tmp_path, "echo ok")


def test_host_path_map_parsing() -> None:
    assert parse_host_path_map("") == {}
    assert parse_host_path_map("/var/lib/regent=/opt/regent") == {
        "/var/lib/regent": "/opt/regent"
    }
    mapped = apply_host_path_map(
        Path("/var/lib/regent/workspaces/a"),
        {"/var/lib/regent": "/opt/regent"},
    )
    assert mapped == "/opt/regent/workspaces/a"


def test_resolve_agent_sandbox_user_rejects_root() -> None:
    class _Cfg:
        agent_sandbox_uid = "0:0"

    with pytest.raises(ValueError, match="must not be root"):
        resolve_agent_sandbox_user(_Cfg())


def test_build_agent_sandbox_uses_agent_image(tmp_path: Path) -> None:
    class _Cfg:
        environment = "development"
        sandbox_mode = "docker"
        build_root = str(tmp_path / "builds")
        agent_sandbox_image = "regent-agent-exec-v1:1"
        sandbox_image = "regent-python-web-v1-sandbox:1"
        host_path_map = ""
        agent_sandbox_uid = "65532:65532"

    driver = build_agent_sandbox(settings=_Cfg())
    assert isinstance(driver, DockerSandboxDriver)
    assert driver._image == "regent-agent-exec-v1:1"


def test_t2_production_toolkit_constructors_pass_command_sandbox() -> None:
    """T2: production sources must not construct bare WorkspaceToolkit(root)."""
    allowed_bare = {
        # tests may construct without sandbox under explicit AST allowlist later
    }
    offenders: list[str] = []
    for path in (CORE_SRC / "regent").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name != "WorkspaceToolkit":
                continue
            keywords = {kw.arg for kw in node.keywords if kw.arg}
            if "command_sandbox" not in keywords:
                rel = str(path.relative_to(ROOT))
                if rel not in allowed_bare:
                    offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, f"bare WorkspaceToolkit without command_sandbox: {offenders}"


@pytest.mark.asyncio
async def test_network_prefixes_require_egress_proxy_cd75(tmp_path: Path) -> None:
    """CD-7.5: pip/curl may request network, but bare bridge is refuse-closed."""
    from regent.agent.tools import WorkspaceToolkit, _NETWORK_PREFIXES

    assert "pip " in _NETWORK_PREFIXES
    assert "curl " in _NETWORK_PREFIXES

    driver = DockerSandboxDriver(
        root=tmp_path / "builds",
        image="regent-agent-exec-v1:1",
        egress_proxy=None,
    )
    with pytest.raises(PermissionError, match="EGRESS_PROXY"):
        driver.workspace_exec_command(tmp_path, "pip install x", allow_network=True)

    gated = DockerSandboxDriver(
        root=tmp_path / "builds",
        image="regent-agent-exec-v1:1",
        egress_proxy="http://127.0.0.1:8888",
    )
    argv = gated.workspace_exec_command(tmp_path, "pip install x", allow_network=True)
    assert argv[argv.index("--network") + 1] == "bridge"
    assert any("HTTPS_PROXY=http://127.0.0.1:8888" == a for a in argv)

    class _Sandbox:
        def __init__(self) -> None:
            self.allow_network: bool | None = None

        async def exec_in_workspace(self, _ws, _cmd, *, timeout_seconds=60, allow_network=False):
            self.allow_network = allow_network
            return "exit=0\nok"

    sandbox = _Sandbox()
    toolkit = WorkspaceToolkit(tmp_path, command_sandbox=sandbox)  # type: ignore[arg-type]
    await toolkit.run_command("pip install nowhere")
    assert sandbox.allow_network is True
    await toolkit.run_command("python -c 'print(1)'")
    assert sandbox.allow_network is False
