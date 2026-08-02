"""Offline Qualification gate entry (R0 + R2 skeleton).

Modes:
  --contracts-only   Contract / inventory gate (default). Allows OFFLINE_QUALIFICATION.
  --full-golden      Contracts + local fixture Preview+smoke chain (no live model).
                     Green allows operator to set INTERNAL_DOGFOOD manually.
                     Does NOT open production canary.

Usage:
  python -B ops/run_agentic_offline_qualification.py --contracts-only
  python -B ops/run_agentic_offline_qualification.py --full-golden
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK_SET = ROOT / "fixtures" / "agent_core_m0_task_set_v1.json"
TASK_HASH = ROOT / "fixtures" / "agent_core_m0_task_set_v1.sha256"
RECORDINGS = ROOT / "fixtures" / "provider_recordings"

CONTRACT_TESTS = [
    "tests/unit/agent/test_agent_core_m0_m1_contracts.py",
    "tests/unit/agent/test_agent_core_m1_m5_contracts.py",
    "tests/unit/agent/test_w4_compact_skills.py",
    "tests/unit/application/test_gq3_production_report.py",
    "tests/unit/application/test_generation_quality.py",
    "tests/unit/application/test_budget_ledger.py::test_all_cost_types_defined",
    "tests/unit/application/test_goal_execution_contract.py::test_event_catalog_contains_all_p1_events",
    "tests/unit/application/test_delivery_batches.py::test_subagent_seeded_incremental_files",
    "tests/unit/infrastructure/test_preview_process.py",
    "tests/unit/ops/test_agentic_qualification_gate.py",
]


def _python() -> str:
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv.is_file():
        return str(venv)
    venv2 = ROOT / ".venv" / "bin" / "python"
    if venv2.is_file():
        return str(venv2)
    return sys.executable


def _check_frozen_task_set() -> dict:
    payload = json.loads(TASK_SET.read_text(encoding="utf-8"))
    digest = hashlib.sha256(TASK_SET.read_bytes()).hexdigest()
    expected = TASK_HASH.read_text(encoding="utf-8").strip()
    ok = digest == expected and len(payload.get("tasks") or []) == 12
    return {
        "id": "frozen_task_set",
        "ok": ok,
        "n_tasks": len(payload.get("tasks") or []),
        "hash_match": digest == expected,
        "digest": digest,
    }


def _check_provider_recordings() -> dict:
    required = [
        "length_truncation.json",
        "malformed_tool_args.json",
        "http_401.json",
        "http_429.json",
        "http_503.json",
    ]
    missing = [n for n in required if not (RECORDINGS / n).is_file()]
    return {
        "id": "provider_recordings",
        "ok": not missing,
        "missing": missing,
        "present": [n for n in required if n not in missing],
    }


def _check_ops_entrypoints() -> dict:
    needed = [
        "ops/run_agentic_offline_qualification.py",
        "ops/set_agentic_qualification.py",
        "ops/clamp_generation_strategy_freeze.py",
    ]
    missing = [n for n in needed if not (ROOT / n).is_file()]
    return {"id": "ops_entrypoints", "ok": not missing, "missing": missing}


def _run_pytest() -> dict:
    py = _python()
    # Unique basetemp each run — Windows preview processes can lock prior trees.
    tmp = Path(tempfile.mkdtemp(prefix="regent_offline_qual_pytest_"))
    cmd = [
        py,
        "-m",
        "pytest",
        *CONTRACT_TESTS,
        "-q",
        "--tb=line",
        f"--basetemp={tmp}",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "id": "contract_pytest",
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "cmd": cmd,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-1000:],
    }


def _run_fixture_golden_preview() -> dict:
    """R2 local golden: materialize fixture app → start_command → readiness (no LLM)."""
    py = _python()
    script = r"""
import asyncio, sys, tempfile
from pathlib import Path

async def main() -> int:
    from regent.agent.runtime_profile_v1 import RUNTIME_PROFILE_SCHEMA_VERSION, RuntimeProfileV1
    from regent.application.p1_ports import DeploymentRequest
    from regent.infrastructure.runtime_preview import RuntimePreviewDeploymentProvider

    root = Path(tempfile.mkdtemp(prefix="regent_golden_"))
    ws = root / "ws"
    ws.mkdir()
    (ws / "src").mkdir()
    (ws / "src" / "app.py").write_text("app = object()\n", encoding="utf-8")
    (ws / "serve.py").write_text(
        "import os\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "PORT = int(os.environ['PORT'])\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')\n"
        "    def log_message(self, *a): pass\n"
        "HTTPServer(('127.0.0.1', PORT), H).serve_forever()\n",
        encoding="utf-8",
    )
    profile = RuntimeProfileV1(
        name="offline-qual-fixture",
        version="1",
        schema_version=RUNTIME_PROFILE_SCHEMA_VERSION,
        project_shape="flask-web",
        entry_module="src.app",
        entry_object="app",
        start_command="python serve.py",
        workdir=".",
        health_routes=(),
        readiness_routes=("/",),
        smoke_routes=("/",),
        install_command=None,
        test_command=None,
        require_tests=False,
        allow_network=False,
        preview_type="runtime",
        network_allowlist=(),
    )

    class _Static:
        async def deploy(self, request):
            raise AssertionError("static unused")

    provider = RuntimePreviewDeploymentProvider(
        root / "previews",
        static_provider=_Static(),
        base_url="http://preview.test",
        readiness_timeout_seconds=20.0,
    )
    result = await provider.deploy(
        DeploymentRequest(
            build_artifact_uri=str(ws),
            environment="preview",
            idempotency_key="golden-1",
            correlation_id="golden",
            acceptance_contract={"runtime_profile": profile.as_dict()},
        )
    )
    await provider.rollback("golden-1", "done")
    print(result.status)
    print(result.evidence.get("profile_hash") == profile.content_hash)
    print(bool(result.evidence.get("live_preview")))
    return 0 if result.status == "SUCCEEDED" else 1

raise SystemExit(asyncio.run(main()))
"""
    proc = subprocess.run(
        [py, "-c", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**dict(**{k: v for k, v in __import__("os").environ.items()}), "PYTHONPATH": str(ROOT / "core" / "src")},
    )
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    ok = proc.returncode == 0 and (lines[:1] == ["SUCCEEDED"])
    return {
        "id": "fixture_golden_preview",
        "ok": ok,
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-1500:],
        "stderr_tail": (proc.stderr or "")[-1000:],
        "note": "local fixture Preview+readiness; live-model agentic golden remains operator-run",
        "allows_internal_dogfood_when_green": True,
    }


def _check_skill_packs() -> dict:
    root = ROOT / "core" / "src" / "regent" / "agent" / "skill_packs"
    required = {
        "runtime-contract",
        "web-app-scaffold",
        "test-harness",
        "persistence",
        "http-api",
        "evidence",
        "ui",
    }
    present = {
        p.name
        for p in root.iterdir()
        if p.is_dir() and (p / "SKILL.json").is_file()
    } if root.is_dir() else set()
    missing = sorted(required - present)
    return {
        "id": "skill_packs_seven",
        "ok": not missing and len(present) >= 7,
        "present": sorted(present),
        "missing": missing,
    }


def _build_report(checks: list[dict], *, mode: str) -> dict:
    all_ok = all(bool(c.get("ok")) for c in checks)
    contracts_ok = all(
        bool(c.get("ok"))
        for c in checks
        if c.get("id") != "fixture_golden_preview"
    )
    golden_ok = next(
        (bool(c.get("ok")) for c in checks if c.get("id") == "fixture_golden_preview"),
        False,
    )
    # Fixture Preview proves process+readiness only. Per decision-note §4, DOGFOOD
    # requires live-model Agentic + REVISE/V2 — that evidence is a separate check.
    live_v2 = any(
        isinstance(c, dict)
        and c.get("ok") is True
        and not c.get("skipped")
        and str(c.get("id") or "")
        in {"live_model_agentic_golden", "agentic_revise_v2", "live_model_revise_v2"}
        for c in checks
    ) or any(
        isinstance(c, dict) and c.get("live_model_v2_green") is True for c in checks
    )
    allows = "DISABLED"
    if live_v2 and (mode in {"live-golden", "full-golden"}):
        allows = "INTERNAL_DOGFOOD"
    elif mode in {"full-golden", "live-golden"} and (
        all_ok or contracts_ok or any(
            isinstance(c, dict)
            and c.get("id") == "revise_v2_structural"
            and c.get("ok")
            for c in checks
        )
    ):
        allows = "OFFLINE_QUALIFICATION"
    elif contracts_ok:
        allows = "OFFLINE_QUALIFICATION"
    return {
        "record_type": "AgenticOfflineQualificationReport",
        "schema_version": "offline-qual-report/v3",
        "mode": mode,
        "created_at": datetime.now(UTC).isoformat(),
        "plan_ref": "docs/agentic-repair-wave-2026-08-02.md",
        "qualification_ref": "docs/decision-note-agentic-qualification-ladder-2026-08-01.md",
        "gate": {
            "contracts_green": contracts_ok,
            "golden_fixture_green": golden_ok if mode == "full-golden" else None,
            "live_model_v2_green": live_v2,
            "allows_state": allows,
            "does_not_auto_promote": True,
            "does_not_open_canary": True,
            "dogfood_requires": (
                "live-model Agentic Runner + REVISE/V2 green in report "
                "(fixture Preview alone → OFFLINE_QUALIFICATION only)"
            ),
        },
        "checks": checks,
        "next_ops": [
            "python -B ops/set_agentic_qualification.py OFFLINE_QUALIFICATION --dry-run",
            "python -B ops/set_agentic_qualification.py OFFLINE_QUALIFICATION",
            "# INTERNAL_DOGFOOD only after a report with live_model_v2_green=true",
        ],
        "explicit_non_goals": [
            "Do not open production canary from this report",
            "Do not flip generation_strategy default to agentic",
            "Do not auto-advance qualification_state",
            "Do not treat fixture Preview as INTERNAL_DOGFOOD evidence",
        ],
    }


def _run_live_golden(*, require_live: bool) -> dict:
    """Delegate to ops/run_agentic_live_golden.py and return merged check dicts."""
    py = _python()
    cmd = [py, "-B", str(ROOT / "ops" / "run_agentic_live_golden.py")]
    cmd.append("--live" if require_live else "--structural")
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # Prefer reading the report written by the child.
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    report_path = ROOT / "docs" / f"agentic-live-golden-report-{day}.json"
    checks: list[dict] = []
    live_green = False
    if report_path.is_file():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            checks = list(payload.get("checks") or [])
            live_green = bool((payload.get("gate") or {}).get("live_model_v2_green"))
        except json.JSONDecodeError:
            checks = []
    ok = proc.returncode == 0 if require_live else any(
        c.get("id") == "revise_v2_structural" and c.get("ok") for c in checks
    ) or proc.returncode == 0
    return {
        "id": "live_golden_lane",
        "ok": ok,
        "require_live": require_live,
        "live_model_v2_green": live_green,
        "child_checks": checks,
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-1200:],
        "stderr_tail": (proc.stderr or "")[-800:],
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--contracts-only", action="store_true", default=False)
    p.add_argument("--full-golden", action="store_true", default=False)
    p.add_argument(
        "--live-golden",
        action="store_true",
        default=False,
        help="Include structural REVISE/V2; with model key also try live agentic",
    )
    p.add_argument(
        "--require-live",
        action="store_true",
        default=False,
        help="Fail if live_model_v2_green is not true (implies --live-golden)",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Report path (default docs/agentic-offline-qual-report-YYYY-MM-DD.json)",
    )
    p.add_argument("--skip-pytest", action="store_true", help="Inventory checks only")
    args = p.parse_args()

    if args.require_live:
        args.live_golden = True

    mode = "contracts-only"
    if args.live_golden:
        mode = "live-golden"
    elif args.full_golden:
        mode = "full-golden"

    checks = [
        _check_frozen_task_set(),
        _check_provider_recordings(),
        _check_ops_entrypoints(),
        _check_skill_packs(),
    ]
    if not args.skip_pytest:
        checks.append(_run_pytest())
    if mode in {"full-golden", "live-golden"}:
        checks.append(_run_fixture_golden_preview())
    if mode == "live-golden":
        live = _run_live_golden(require_live=bool(args.require_live))
        checks.append(live)
        # Flatten child live checks into top-level for gate helpers.
        for child in live.get("child_checks") or []:
            if isinstance(child, dict) and child.get("id"):
                checks.append(child)

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    out = args.out or (ROOT / "docs" / f"agentic-offline-qual-report-{day}.json")
    report = _build_report(checks, mode=mode)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "out": str(out),
                "mode": mode,
                "ok": all(bool(c.get("ok")) for c in checks if c.get("id") != "live_model_revise_v2" or args.require_live),
                "allows_state": report["gate"]["allows_state"],
                "live_model_v2_green": report["gate"].get("live_model_v2_green"),
            },
            ensure_ascii=False,
        )
    )
    for c in checks:
        print(f"  {c['id']}: {'OK' if c.get('ok') else 'FAIL'}")
    # Soft: skipped live check should not fail non-require-live runs.
    hard = []
    for c in checks:
        if c.get("id") == "live_model_revise_v2" and c.get("skipped") and not args.require_live:
            continue
        if c.get("id") == "live_golden_lane" and not args.require_live:
            # structural child is enough
            if any(
                x.get("id") == "revise_v2_structural" and x.get("ok")
                for x in (c.get("child_checks") or [])
            ):
                continue
        hard.append(c)
    return 0 if all(bool(c.get("ok")) for c in hard) else 1


if __name__ == "__main__":
    raise SystemExit(main())
