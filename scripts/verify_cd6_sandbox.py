"""CD-6 local verification: argv contract + local sandbox triad (echo / write / pytest)."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from regent.agent.tools import WorkspaceToolkit
from regent.infrastructure.sandbox import DockerSandboxDriver, LocalSandboxDriver


async def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        driver = DockerSandboxDriver(
            root=root / "b",
            image="regent-agent-exec-v1:1",
            run_as_user="1000:1000",
            host_path_map={"/var/lib/regent": "/opt/regent"},
        )
        argv = driver.workspace_exec_command(Path("/var/lib/regent/workspaces/x"), "echo ok")
        assert argv[argv.index("--entrypoint") + 1] == "sh"
        assert argv[argv.index("--network") + 1] == "none"
        assert "src=/opt/regent/workspaces/x" in argv[argv.index("--mount") + 1]
        print("argv contract OK")

        local = LocalSandboxDriver(root=root / "agent")
        toolkit = WorkspaceToolkit(root / "ws", command_sandbox=local)
        r1 = await toolkit.run_command("python -c \"print('ok')\"")
        assert r1.startswith("exit=0") and "ok" in r1, r1
        write_cmd = "python -c \"open('probe','w').write('1')\""
        r2 = await toolkit.run_command(write_cmd)
        assert r2.startswith("exit=0"), r2
        assert (root / "ws" / "probe").read_text(encoding="utf-8") == "1"
        (root / "ws" / "test_probe.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8"
        )
        r3 = await toolkit.run_command("pytest -q")
        assert r3.startswith("exit=0"), r3
        print("local triad OK (python print / write / pytest)")
        print("CD-6 local verification PASSED")
        print("NOTE: docker image build skipped if docker CLI absent — run:")
        print(
            "  docker build -t regent-agent-exec-v1:1 "
            "-f capabilities/bootstrap/agent-exec/Dockerfile "
            "capabilities/bootstrap/agent-exec"
        )


if __name__ == "__main__":
    asyncio.run(main())
