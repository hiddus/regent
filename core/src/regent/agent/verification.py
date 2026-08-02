"""Adversarial verification gate for generated products (M2)."""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from typing import Any

from regent.agent.runtime_profile_v1 import RuntimeProfileV1, parse_runtime_profile_v1
from regent.agent.skills import route_skills_for_gaps
from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import VerificationGap, VerificationVerdict
from regent.application.delivery_review_service import review_files_for_delivery


class VerificationAgent:
    """Independent verifier: static bans + tests + smoke.

    Collects all physically runnable stage results in one pass (M2-3).
    Never modifies the product under test.
    """

    def __init__(
        self,
        toolkit: WorkspaceToolkit,
        *,
        runtime_profile: RuntimeProfileV1 | dict[str, Any] | None = None,
    ) -> None:
        self._toolkit = toolkit
        if isinstance(runtime_profile, RuntimeProfileV1):
            self._profile = runtime_profile
        else:
            self._profile = parse_runtime_profile_v1(
                dict(runtime_profile) if runtime_profile else None
            )

    async def verify(
        self,
        *,
        acceptance_contract: dict[str, Any] | None = None,
        success_criteria: dict[str, Any] | None = None,
        run_smoke: bool = True,
        runtime_profile: RuntimeProfileV1 | dict[str, Any] | None = None,
    ) -> VerificationVerdict:
        if runtime_profile is not None:
            if isinstance(runtime_profile, RuntimeProfileV1):
                self._profile = runtime_profile
            else:
                self._profile = parse_runtime_profile_v1(dict(runtime_profile))

        report = self._toolkit.snapshot_files_report()
        files = report.files
        gaps: list[VerificationGap] = []
        stages: dict[str, Any] = {
            "static": {"attempted": True},
            "tests": {"attempted": False},
            "start": {"attempted": False},
            "smoke": {"attempted": False},
            "manifest": report.as_dict(),
            "profile_hash": self._profile.content_hash if self._profile else None,
        }

        if report.truncated or not report.integrity_ok:
            gaps.append(
                VerificationGap(
                    code="ARTIFACT_INCOMPLETE",
                    detail="workspace manifest truncated or integrity failed",
                    artifact_snippet=json.dumps(report.as_dict(), ensure_ascii=False)[:2_000],
                )
            )

        review = review_files_for_delivery(
            files,
            acceptance_contract=acceptance_contract,
            success_criteria=success_criteria,
        )
        static_failed = False
        for check in review.checks:
            if not check.passed:
                static_failed = True
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
        stages["static"]["passed"] = not static_failed
        stages["static"]["summary"] = review.summary

        # M2-3: always attempt tests when physically possible (even if static failed).
        test_result = await self._run_project_tests(files, success_criteria or {})
        stages["tests"] = test_result
        if test_result.get("failed"):
            gaps.append(
                VerificationGap(
                    code="TEST_FAILED" if test_result.get("attempted") else "TEST_COMMAND_MISSING",
                    detail=str(test_result.get("error") or "project tests failed"),
                    artifact_snippet=str(test_result.get("log") or "")[:2_000],
                    status="FAIL",
                )
            )
        elif test_result.get("blocked"):
            gaps.append(
                VerificationGap(
                    code="TEST_COMMAND_MISSING",
                    detail=str(test_result.get("error") or "tests blocked"),
                    blocked_by=str(test_result.get("blocked_by") or "profile"),
                    status="BLOCKED",
                )
            )
        elif test_result.get("degraded"):
            # Exploratory profile: explicit degradation, cannot promote as formal delivery.
            stages["tests"]["promotion_allowed"] = False

        smoke: dict[str, Any] = {"attempted": False}
        if run_smoke:
            # R0 / B5: always attempt smoke so runtime evidence is not structurally
            # unreachable when static fails. Static gaps remain; verdict still fails.
            smoke = await self._smoke_http(files, success_criteria or {})
            if static_failed and self._profile and self._profile.project_shape != "static-web":
                smoke = {
                    **smoke,
                    "static_failed_concurrent": True,
                    "note": "smoke attempted despite static failures (anti B5 short-circuit)",
                }
            if not smoke.get("passed") and not smoke.get("blocked"):
                gaps.append(
                    VerificationGap(
                        code="SMOKE_FAILED" if smoke.get("attempted") else "START_FAILED",
                        detail=str(smoke.get("error") or "app failed smoke"),
                        artifact_snippet=str(smoke.get("log") or "")[:2_000],
                    )
                )
            elif smoke.get("blocked"):
                gaps.append(
                    VerificationGap(
                        code="START_FAILED",
                        detail=str(smoke.get("error") or "start blocked"),
                        blocked_by=str(smoke.get("blocked_by") or "unknown"),
                        status="BLOCKED",
                    )
                )
        stages["smoke"] = smoke
        stages["start"] = {
            "attempted": bool(smoke.get("attempted")),
            "passed": bool(smoke.get("passed")),
            "blocked": bool(smoke.get("blocked")),
            "blocked_by": smoke.get("blocked_by"),
        }

        # Attach skill guidance refs (verifier does not mutate artifacts) — M5-3.
        skill_refs = [
            m.as_dict()
            for m in route_skills_for_gaps([g.code for g in gaps])
        ]
        stages["skill_guidance"] = skill_refs

        verification_hash = hashlib.sha256(
            json.dumps(
                {
                    "gaps": [{"code": g.code, "detail": g.detail} for g in gaps],
                    "stages": {
                        k: {sk: sv for sk, sv in v.items() if sk != "log"}
                        if isinstance(v, dict)
                        else v
                        for k, v in stages.items()
                        if k != "manifest"
                    },
                },
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        stages["verification_hash"] = verification_hash

        if gaps:
            has_only_blocked = all(g.status == "BLOCKED" for g in gaps)
            return VerificationVerdict(
                verdict="BLOCKED" if has_only_blocked else "FAIL",
                gaps=gaps,
                smoke={**smoke, "project_tests": test_result, "stages": stages},
                summary=f"{'BLOCKED' if has_only_blocked else 'FAIL'} with {len(gaps)} gaps",
            )
        return VerificationVerdict(
            verdict="PASS",
            gaps=[],
            smoke={**smoke, "project_tests": test_result, "stages": stages},
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
        require_tests = True if self._profile is None else self._profile.require_tests
        command = _resolve_test_command(files, success_criteria, self._profile)
        if command is None:
            if require_tests:
                return {
                    "attempted": False,
                    "failed": True,
                    "degraded": False,
                    "blocked": False,
                    "error": "TEST_COMMAND_MISSING: profile requires tests but none found",
                    "log": "",
                }
            return {
                "attempted": False,
                "failed": False,
                "degraded": True,
                "blocked": False,
                "promotion_allowed": False,
                "error": "TEST_COMMAND_MISSING: exploratory profile allows no tests (no promote)",
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
        if self._profile and self._profile.project_shape == "static-web":
            if "index.html" in files:
                return {
                    "attempted": True,
                    "passed": True,
                    "error": None,
                    "log": "static-web: index.html present",
                    "routes": list(self._profile.smoke_routes) or ["/"],
                }
            return {
                "attempted": True,
                "passed": False,
                "error": "static-web missing index.html",
                "log": "",
            }

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

        app_rel, module = _resolve_entry(files, self._profile)
        if app_rel is None or module is None:
            return {
                "attempted": True,
                "passed": False,
                "error": "no app entrypoint for profile",
                "log": "",
            }

        port = _pick_free_port()
        routes = _routes_from_profile_and_criteria(self._profile, success_criteria)
        entry_object = (
            str(self._profile.entry_object) if self._profile else "app"
        ) or "app"
        probe_script = self._toolkit.root / ".regent_smoke_probe.py"
        probe_script.write_text(
            _smoke_probe_script(
                module=module,
                entry_object=entry_object,
                port=port,
                routes=routes,
            ),
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


def _smoke_probe_script(
    *, module: str, entry_object: str, port: int, routes: list[str]
) -> str:
    routes_lit = repr(list(routes))
    return f"""
import importlib
import socket
import threading
import time
import urllib.request

MODULE = {module!r}
ENTRY_OBJECT = {entry_object!r}
PORT = {port}
ROUTES = {routes_lit}
LOG = []

def _serve():
    mod = importlib.import_module(MODULE)
    app = getattr(mod, ENTRY_OBJECT, None)
    if app is None:
        raise SystemExit(f"no entry object {{ENTRY_OBJECT!r}} on {{MODULE}}")
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


def _routes_from_profile_and_criteria(
    profile: RuntimeProfileV1 | None,
    success_criteria: dict[str, Any],
) -> list[str]:
    """M2-2: only Profile-declared or criteria-declared routes — no unconditional /health."""
    routes: list[str] = []
    if profile is not None:
        for item in list(profile.smoke_routes) + list(profile.health_routes) + list(
            profile.readiness_routes
        ):
            path = str(item).strip()
            if path and path not in routes:
                routes.append(path if path.startswith("/") else f"/{path}")
    for key in ("smoke_routes", "api_routes", "required_routes"):
        raw = success_criteria.get(key)
        if isinstance(raw, list):
            for item in raw:
                path = str(item).strip()
                if path and path not in routes:
                    routes.append(path if path.startswith("/") else f"/{path}")
    if not routes:
        routes = ["/"]
    return routes[:8]


def _resolve_entry(
    files: dict[str, str], profile: RuntimeProfileV1 | None
) -> tuple[str | None, str | None]:
    if profile and profile.entry_module:
        module = profile.entry_module
        # src.app → src/app.py
        candidate = module.replace(".", "/") + ".py"
        if candidate in files:
            return candidate, module
        # Fall through to common paths.
    for candidate, module in (("src/app.py", "src.app"), ("app.py", "app")):
        if candidate in files:
            return candidate, module
    return None, None


def _resolve_test_command(
    files: dict[str, str],
    success_criteria: dict[str, Any],
    profile: RuntimeProfileV1 | None,
) -> str | None:
    if profile and profile.test_command:
        has_tests = any(
            p.replace("\\", "/").startswith("tests/") or Path(p).name.startswith("test_")
            for p in files
        )
        if has_tests:
            return profile.test_command
        # require_tests but no tests → caller treats command=None as TEST_COMMAND_MISSING
        return None
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
