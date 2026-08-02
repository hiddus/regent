"""Local long-lived preview process supervisor (R1).

Starts Profile ``start_command``, waits for HTTP readiness, stops on rollback.
Does not alter sandbox ``exec_in_workspace`` (short-lived) semantics.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def rewrite_start_command(command: str, *, port: int) -> str:
    """Force bind port in common flask/uvicorn CLI shapes."""
    cmd = command.strip()
    if re.search(r"--port\s+\d+", cmd):
        return re.sub(r"--port\s+\d+", f"--port {port}", cmd)
    if re.search(r"-p\s+\d+", cmd):
        return re.sub(r"-p\s+\d+", f"-p {port}", cmd)
    # Append when command has no explicit port.
    if "uvicorn" in cmd or "flask" in cmd or "gunicorn" in cmd:
        return f"{cmd} --port {port}"
    return cmd


@dataclass(slots=True)
class PreviewProcessHandle:
    deployment_id: str
    workspace: Path
    port: int
    command: str
    process: subprocess.Popen[str]
    started_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "workspace": str(self.workspace),
            "port": self.port,
            "command": self.command,
            "pid": self.process.pid,
            "started_at": self.started_at,
            "alive": self.process.poll() is None,
        }


class PreviewProcessSupervisor:
    """Manage detached local preview servers keyed by deployment id."""

    def __init__(self) -> None:
        self._handles: dict[str, PreviewProcessHandle] = {}

    def start(
        self,
        *,
        deployment_id: str,
        workspace: Path,
        start_command: str,
        port: int | None = None,
        env: dict[str, str] | None = None,
    ) -> PreviewProcessHandle:
        self.stop(deployment_id)
        port = int(port or pick_free_port())
        command = rewrite_start_command(start_command, port=port)
        proc_env = os.environ.copy()
        proc_env.update(env or {})
        proc_env["REGENT_PREVIEW_PORT"] = str(port)
        proc_env["PORT"] = str(port)
        # Avoid flask debug reloader forking.
        proc_env.setdefault("FLASK_DEBUG", "0")
        proc_env.setdefault("WERKZEUG_RUN_MAIN", "true")
        popen_kwargs: dict[str, Any] = {
            "cwd": str(workspace),
            "env": proc_env,
            "shell": True,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "text": True,
        }
        if os.name == "nt":
            # Own process group so taskkill /T can tear down shell children.
            popen_kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        process = subprocess.Popen(command, **popen_kwargs)
        handle = PreviewProcessHandle(
            deployment_id=deployment_id,
            workspace=workspace.resolve(),
            port=port,
            command=command,
            process=process,
            started_at=time.time(),
        )
        self._handles[deployment_id] = handle
        return handle

    def wait_ready(
        self,
        handle: PreviewProcessHandle,
        *,
        routes: list[str],
        timeout_seconds: float = 25.0,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        last_error = "not_started"
        while time.time() < deadline:
            if handle.process.poll() is not None:
                return {
                    "ready": False,
                    "error": f"process exited early code={handle.process.returncode}",
                    "port": handle.port,
                }
            try:
                with socket.create_connection(("127.0.0.1", handle.port), timeout=0.4):
                    break
            except OSError as exc:
                last_error = str(exc)
                time.sleep(0.2)
        else:
            return {"ready": False, "error": f"bind timeout: {last_error}", "port": handle.port}

        probed: list[str] = []
        for route in routes or ["/"]:
            path = route if route.startswith("/") else f"/{route}"
            url = f"http://127.0.0.1:{handle.port}{path}"
            try:
                with urllib.request.urlopen(url, timeout=3) as resp:
                    code = int(getattr(resp, "status", 200) or 200)
                    probed.append(f"GET {path} -> {code}")
                    if code >= 500:
                        return {
                            "ready": False,
                            "error": f"route {path} returned {code}",
                            "port": handle.port,
                            "probes": probed,
                        }
            except Exception as exc:  # noqa: BLE001 — readiness fails closed
                # 4xx still means server is up for readiness purposes.
                if isinstance(exc, urllib.error.HTTPError) and int(exc.code) < 500:
                    probed.append(f"GET {path} -> {exc.code}")
                    continue
                return {
                    "ready": False,
                    "error": f"route {path} failed: {exc}",
                    "port": handle.port,
                    "probes": probed,
                }
        return {"ready": True, "port": handle.port, "probes": probed}

    def stop(self, deployment_id: str) -> None:
        handle = self._handles.pop(deployment_id, None)
        if handle is None:
            return
        if handle.process.poll() is not None:
            return
        if os.name == "nt":
            # Shell=True leaves a cmd.exe parent; kill the tree to release cwd locks.
            subprocess.run(
                ["taskkill", "/PID", str(handle.process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            try:
                handle.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return
        handle.process.terminate()
        try:
            handle.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            handle.process.kill()
