"""Adversarial verification gate for generated products."""

from __future__ import annotations

import socket
from typing import Any

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
        """compileall → start app → HTTP probe — all via toolkit sandbox (F-3 / §13.8).

        Long-lived host ``create_subprocess_exec`` is forbidden. The smoke probe
        is a single sandboxed ``python`` invocation that starts the app in-process
        (background thread), probes routes, then exits.
        """
        compile_result = await self._toolkit.run_command(
            "python -m compileall -q .",
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
        routes = _routes_from_criteria(success_criteria)
        probe_script = self._toolkit.root / ".regent_smoke_probe.py"
        probe_script.write_text(
            _smoke_probe_script(module=module, port=port, routes=routes),
            encoding="utf-8",
        )
        try:
            log = await self._toolkit.run_command(
                "python .regent_smoke_probe.py",
                timeout_seconds=90,
            )
            passed = log.startswith("exit=0") and "SMOKE_OK" in log
            return {
                "attempted": True,
                "passed": passed,
                "error": None if passed else "app failed smoke",
                "log": log,
                "port": port,
                "routes": routes,
            }
        finally:
            probe_script.unlink(missing_ok=True)


def _smoke_probe_script(*, module: str, port: int, routes: list[str]) -> str:
    """Self-contained probe: start app in a daemon thread, HTTP GET routes, exit."""
    routes_lit = repr(list(routes))
    return f"""
import importlib
import socket
import threading
import time
import urllib.request

MODULE = {module!r}
PORT = {port}
ROUTES = {routes_lit}
LOG = []

def _serve():
    mod = importlib.import_module(MODULE)
    app = getattr(mod, "app", None)
    if app is None:
        raise SystemExit("no app object")
    if hasattr(app, "run") and not hasattr(app, "router"):
        app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
        return
    try:
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error")
    except Exception:
        from werkzeug.serving import run_simple
        run_simple("127.0.0.1", PORT, app, use_reloader=False, use_debugger=False)

thread = threading.Thread(target=_serve, daemon=True)
thread.start()

deadline = time.time() + 20.0
while time.time() < deadline:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
            break
    except OSError:
        time.sleep(0.2)
else:
    print("SMOKE_FAIL: server did not bind")
    raise SystemExit(1)

for route in ROUTES:
    url = f"http://127.0.0.1:{{PORT}}{{route}}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            code = getattr(resp, "status", 200)
            LOG.append(f"GET {{route}} -> {{code}}")
            if int(code) >= 500:
                print("\\n".join(LOG))
                print(f"SMOKE_FAIL: {{route}} returned {{code}}")
                raise SystemExit(1)
    except Exception as exc:
        print("\\n".join(LOG))
        print(f"SMOKE_FAIL: request {{route}} failed: {{exc}}")
        raise SystemExit(1)

print("\\n".join(LOG))
print("SMOKE_OK")
raise SystemExit(0)
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
        if any(
            p.replace("\\", "/").startswith("tests/") or p.startswith("test_")
            for p in files
        ):
            return "pytest -q --tb=line"
    if any(p.replace("\\", "/").startswith("tests/") for p in files):
        return "pytest -q --tb=line"
    return None


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
