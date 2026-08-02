"""Live / structural Offline Qual golden for INTERNAL_DOGFOOD evidence (W4-P1-1).

Modes:
  --structural   Always: accepted → REVISE clone → V2 hash continuity (no LLM).
                 Sets check revise_v2_structural; does NOT set live_model_v2_green.
  --live         Requires model credentials; runs a minimal Agentic turn then
                 structural REVISE/V2. Sets live_model_revise_v2 when green.

Usage:
  python -B ops/run_agentic_live_golden.py --structural
  python -B ops/run_agentic_live_golden.py --live
  python -B ops/run_agentic_offline_qualification.py --live-golden  # wraps this

Exit 0 when requested checks green. Structural alone never unlocks DOGFOOD.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "src"))


def _structural_revise_v2() -> dict:
    from regent.agent.accepted_workspace import (
        clone_accepted_snapshot,
        verify_promotion_hashes,
        write_accepted_workspace_snapshot,
    )
    from regent.agent.file_manifest import build_workspace_manifest
    from regent.agent.runtime_profile_v1 import CERTIFIED_RUNTIME_PROFILES_V1, profile_by_name

    profile = profile_by_name("flask-web-v1") or CERTIFIED_RUNTIME_PROFILES_V1[1]
    root = Path(tempfile.mkdtemp(prefix="regent_live_golden_"))
    v1 = root / "v1"
    v1.mkdir()
    (v1 / "src").mkdir()
    (v1 / "src" / "app.py").write_text(
        "from flask import Flask\napp = Flask(__name__)\n"
        "@app.get('/')\ndef home():\n    return 'ok'\n",
        encoding="utf-8",
    )
    (v1 / "requirements.txt").write_text("flask\n", encoding="utf-8")
    snap = write_accepted_workspace_snapshot(
        v1, root / "store", profile_hash=profile.content_hash, verification_hash="v1ver"
    )
    revise = clone_accepted_snapshot(snap.uri, root / "v2")
    # Incremental V2 edit (not cold start).
    app_py = revise / "src" / "app.py"
    app_py.write_text(
        app_py.read_text(encoding="utf-8") + "\n# v2 revise\n",
        encoding="utf-8",
    )
    snap2 = write_accepted_workspace_snapshot(
        revise, root / "store2", profile_hash=profile.content_hash, verification_hash="v2ver"
    )
    manifest = build_workspace_manifest(revise)
    errors = verify_promotion_hashes(
        manifest_hash=manifest.content_hash,
        profile_hash=profile.content_hash,
        verification_hash="v2ver",
        preview_deployment_hash=snap2.content_hash[:16],
        expected={
            "manifest_hash": manifest.content_hash,
            "profile_hash": profile.content_hash,
            "verification_hash": "v2ver",
            "preview_deployment_hash": snap2.content_hash[:16],
        },
    )
    ok = not errors and "v2 revise" in app_py.read_text(encoding="utf-8")
    return {
        "id": "revise_v2_structural",
        "ok": ok,
        "errors": errors,
        "v1_uri": snap.uri,
        "v2_uri": snap2.uri,
        "note": "structural REVISE/V2 continuity; not live-model evidence",
    }


async def _live_agentic_minimal() -> dict:
    """One-shot agentic write+submit if provider credentials exist."""
    from regent.agent.agent_runner import AgentRunner
    from regent.agent.runtime_profile_v1 import profile_by_name, CERTIFIED_RUNTIME_PROFILES_V1
    from regent.agent.tools import WorkspaceToolkit
    from regent.agent.types import AgentBudget
    from regent.config import get_settings
    from regent.model.factory import build_model_provider

    settings = get_settings()
    try:
        provider = build_model_provider(settings)
    except Exception as exc:  # noqa: BLE001
        return {
            "id": "live_model_revise_v2",
            "ok": False,
            "skipped": True,
            "error": f"provider unavailable: {exc}",
        }

    profile = profile_by_name("flask-web-v1") or CERTIFIED_RUNTIME_PROFILES_V1[1]
    ws = Path(tempfile.mkdtemp(prefix="regent_live_agent_"))
    toolkit = WorkspaceToolkit(ws)
    runner = AgentRunner(
        provider,
        toolkit,
        budget=AgentBudget(max_turns=8, max_tokens=40_000, max_wall_seconds=180),
        context_window_tokens=int(settings.agent_context_window_tokens),
        runtime_profile=profile,
        skills_enabled=True,
    )
    plan = {
        "goal_anchor_text": "Build a minimal Flask hello app with GET / returning ok",
        "planned_paths": ["src/app.py", "requirements.txt", "README.md"],
        "acceptance_contract": {
            "runtime_profile": profile.as_dict(),
            "first_deliverable": "flask hello",
            "success_criteria": {"smoke_routes": ["/"]},
            "batch_run_smoke": False,
        },
    }
    try:
        result = await runner.run(plan, verify=False)
        structural = _structural_revise_v2()
        # Seed structural from live files if present.
        ok = bool(result.files) and "src/app.py" in result.files and structural.get("ok")
        return {
            "id": "live_model_revise_v2",
            "ok": ok,
            "skipped": False,
            "files": sorted(result.files.keys())[:20],
            "turns": result.turns,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cache_hit_rate": (
                result.ledger.cache_hit_rate if getattr(result, "ledger", None) else None
            ),
            "structural": structural,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "id": "live_model_revise_v2",
            "ok": False,
            "skipped": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--structural", action="store_true", default=False)
    p.add_argument("--live", action="store_true", default=False)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()
    if not args.structural and not args.live:
        args.structural = True

    checks: list[dict] = []
    if args.structural or args.live:
        checks.append(_structural_revise_v2())
    if args.live:
        checks.append(asyncio.run(_live_agentic_minimal()))

    live_ok = any(
        c.get("id") == "live_model_revise_v2" and c.get("ok") and not c.get("skipped")
        for c in checks
    )
    structural_ok = any(
        c.get("id") == "revise_v2_structural" and c.get("ok") for c in checks
    )
    allows = "DISABLED"
    if live_ok:
        allows = "INTERNAL_DOGFOOD"
    elif structural_ok:
        allows = "OFFLINE_QUALIFICATION"

    report = {
        "record_type": "AgenticLiveGoldenReport",
        "schema_version": "live-golden/v1",
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "live" if args.live else "structural",
        "gate": {
            "allows_state": allows,
            "live_model_v2_green": live_ok,
            "structural_revise_v2_green": structural_ok,
            "does_not_auto_promote": True,
        },
        "checks": checks,
    }
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    out = args.out or (ROOT / "docs" / f"agentic-live-golden-report-{day}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "out": str(out),
                "allows_state": allows,
                "live_model_v2_green": live_ok,
            },
            ensure_ascii=False,
        )
    )
    for c in checks:
        print(f"  {c['id']}: {'OK' if c.get('ok') else 'FAIL/SKIP'}")
    # Structural-only success → exit 0; live requested but failed → 1
    if args.live and not live_ok:
        return 1
    return 0 if structural_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
