"""Adversarial verification gate for generated products."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
from typing import Any

import httpx

from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import VerificationGap, VerificationVerdict
from regent.application.delivery_review_service import review_files_for_delivery


class VerificationAgent:
    """Independent verifier: static bans + real process smoke.

    Stance (from Claude Code verificationAgent): find the last 20%; reading
    code is not verification; never modify the product under test.
    """

    def __init__(self, toolkit: WorkspaceToolkit) -> None:
        self._toolkit = toolkit

    async def verify(
        self,
        *,
        acceptance_contract: dict[str, Any] | None = None,
        success_criteria: dict[str, Any] | None = None,
        run_smoke: bool = True,
    ) -> VerificationVerdict:
        files = self._toolkit.snapshot_files()
        gaps: list[VerificationGap] = []

        review = review_files_for_delivery(
            files,
            acceptance_contract=acceptance_contract,
            success_criteria=success_criteria,
        )
        for check in review.checks:
            if not check.passed:
                snippet = ""
                if "static" in check.name or "trivial" in check.name:
                    snippet = self._snippet_for(["src/app.py", "app.py"])
                gaps.append(
                    VerificationGap(
                        code=check.name,
                        detail=check.detail or "failed",
                        artifact_snippet=snippet,
                    )
                )

        smoke: dict[str, Any] = {"attempted": False}
        test_result: dict[str, Any] = {"attempted": False, "degraded": False}
        if not gaps:
            test_result = await self._run_project_tests(files, success_criteria or {})
            if test_result.get("failed"):
                gaps.append(
                    VerificationGap(
                        code="project-tests",
                        detail=str(test_result.get("error") or "project tests failed"),
                        artifact_snippet=str(test_result.get("log") or "")[:2_000],
                    )
                )
            elif test_result.get("degraded"):
                # Explicit degradation path — do not silent-skip; do not hard-fail.
                pass

        if run_smoke and not gaps:
            smoke = await self._smoke_http(files, success_criteria or {})
            if not smoke.get("passed"):
                gaps.append(
                    VerificationGap(
                        code="smoke-http",
                        detail=str(smoke.get("error") or "app failed smoke"),
                        artifact_snippet=str(smoke.get("log") or "")[:2_000],
                    )
                )

        if gaps:
            return VerificationVerdict(
                verdict="FAIL",
                gaps=gaps,
                smoke={**smoke, "project_tests": test_result},
                summary=f"FAIL with {len(gaps)} gaps",
            )
        return VerificationVerdict(
            verdict="PASS",
            gaps=[],
            smoke={**smoke, "project_tests": test_result},
            summary=review.summary or "PASS",
        )

    def _snippet_for(self, candidates: list[str]) -> str:
        for rel in candidates:
            try:
                return self._toolkit.read_text(rel, max_chars=1_500)
            except (OSError, ValueError, FileNotFoundError):
                continue
        return ""

    async def _run_project_tests(
        self,
        files: dict[str, str],
        success_criteria: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve and run pytest / project test command (GQ-2 / §13.6).

        Missing commands degrade explicitly — never silent skip.
        """
        command = _resolve_test_command(files, success_criteria)
        if command is None:
            return {
                "attempted": False,
                "failed": False,
                "degraded": True,
                "error": "TEST_COMMAND_MISSING: no pytest/tests/ or configured test command",
                "log": "",
            }
        log = await self._toolkit.run_command(command, timeout_seconds=120)
        passed = log.startswith("exit=0")
        return {
            "attempted": True,
            "failed": not passed,
            "degraded": False,
            "command": command,
            "error": None if passed else "project tests failed",
            "log": log[:4_000],
        }

    async def _smoke_http(
        self,
        files: dict[str, str],
        success_criteria: dict[str, Any],
    ) -> dict[str, Any]:
        """compileall → start app on ephemeral port → HTTP probe core routes."""
        compile_result = await self._toolkit.run_command(
            f"{sys.executable} -m compileall -q .",
            timeout_seconds=60,
        )
        if not compile_result.startswith("exit=0"):
            return {
                "attempted": True,
                "passed": False,
                "error": "compileall failed",
                "log": compile_result,
            }

        app_rel = None
        for candidate in ("src/app.py", "app.py"):
            if candidate in files:
                app_rel = candidate
                break
        if app_rel is None:
            return {
                "attempted": True,
                "passed": False,
                "error": "no app.py entrypoint",
                "log": "",
            }

        port = _pick_free_port()
        module = "src.app" if app_rel.startswith("src/") else "app"
        server_script = self._toolkit.root / ".regent_smoke_server.py"
        server_script.write_text(
            _server_launcher(module=module, port=port),
            encoding="utf-8",
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(server_script),
            cwd=str(self._toolkit.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        log_chunks: list[str] = []
        try:
            ready = await _wait_port(port, timeout=20.0)
            if not ready:
                out = await _drain(process, timeout=1.0)
                log_chunks.append(out)
                return {
                    "attempted": True,
                    "passed": False,
                    "error": f"server did not bind :{port}",
                    "log": "\n".join(log_chunks),
                }

            routes = _routes_from_criteria(success_criteria)
            async with httpx.AsyncClient(timeout=5.0) as client:
                for route in routes:
                    url = f"http://127.0.0.1:{port}{route}"
                    try:
                        resp = await client.get(url)
                    except Exception as exc:  # noqa: BLE001
                        return {
                            "attempted": True,
                            "passed": False,
                            "error": f"request {route} failed: {exc}",
                            "log": "\n".join(log_chunks),
                            "port": port,
                            "routes": routes,
                        }
                    log_chunks.append(f"GET {route} -> {resp.status_code}")
                    if resp.status_code >= 500:
                        return {
                            "attempted": True,
                            "passed": False,
                            "error": f"{route} returned {resp.status_code}",
                            "log": "\n".join(log_chunks),
                            "port": port,
                            "routes": routes,
                        }
            return {
                "attempted": True,
                "passed": True,
                "error": None,
                "log": "\n".join(log_chunks),
                "port": port,
                "routes": routes,
            }
        finally:
            process.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=5)
            server_script.unlink(missing_ok=True)


def _server_launcher(*, module: str, port: int) -> str:
    return f"""
import importlib

mod = importlib.import_module({module!r})
app = getattr(mod, "app", None)
if app is None:
    raise SystemExit("no app object")

# Flask
if hasattr(app, "run") and not hasattr(app, "router"):
    app.run(host="127.0.0.1", port={port}, debug=False, use_reloader=False)
    raise SystemExit(0)

# ASGI (FastAPI/Starlette)
try:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port={port}, log_level="error")
except Exception:
    from werkzeug.serving import run_simple
    run_simple("127.0.0.1", {port}, app, use_reloader=False, use_debugger=False)
"""


def _routes_from_criteria(success_criteria: dict[str, Any]) -> list[str]:
    routes = ["/"]
    for key in ("smoke_routes", "api_routes", "required_routes"):
        raw = success_criteria.get(key)
        if isinstance(raw, list):
            for item in raw:
                path = str(item).strip()
                if path and path not in routes:
                    routes.append(path if path.startswith("/") else f"/{path}")
    for extra in ("/api/health", "/health"):
        if extra not in routes:
            routes.append(extra)
    return routes[:4]


def _resolve_test_command(
    files: dict[str, str], success_criteria: dict[str, Any]
) -> str | None:
    """Prefer explicit criteria, then pyproject/pytest markers, then tests/ tree."""
    for key in ("test_command", "pytest_command", "verification_test_command"):
        raw = success_criteria.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    if "pytest.ini" in files or "pyproject.toml" in files:
        # Prefer pytest when project declares it; still require a tests path or default.
        if any(p.startswith("tests/") or p.startswith("test_") for p in files):
            return f"{sys.executable} -m pytest -q --tb=line"
    if any(p.startswith("tests/") for p in files):
        return f"{sys.executable} -m pytest -q --tb=line"
    return None


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_port(port: int, *, timeout: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return True
        except OSError:
            await asyncio.sleep(0.25)
    return False


async def _drain(process: asyncio.subprocess.Process, *, timeout: float) -> str:
    try:
        assert process.stdout is not None
        out = await asyncio.wait_for(process.stdout.read(8_000), timeout=timeout)
        return out.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""
