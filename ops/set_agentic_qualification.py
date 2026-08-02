"""Set REGENT_AGENTIC_QUALIFICATION_STATE locally and/or on S0.

Usage:
  python -B ops/set_agentic_qualification.py OFFLINE_QUALIFICATION --report docs/agentic-offline-qual-report-2026-08-02.json
  python -B ops/set_agentic_qualification.py DISABLED --remote
  python -B ops/set_agentic_qualification.py CANARY_5 --remote --also-canary-percent 5 --also-gate true

Promotion rules (unless --force):
  - Upgrades: adjacent step only (DISABLED→OFFLINE→DOGFOOD→CANARY_5→…→DEFAULT)
  - Downgrades: any lower state allowed without report
  - Upgrades to OFFLINE_QUALIFICATION+: require a fresh qual report whose
    gate.allows_state ranks ≥ target
  - Upgrades to INTERNAL_DOGFOOD+: report must include live-model REVISE/V2
    evidence (fixture Preview golden alone is NOT enough)

Does NOT open canary by default. Traffic still requires:
  qualification in {INTERNAL_DOGFOOD, CANARY_*, DEFAULT}
  + REGENT_GENERATION_STRATEGY_CANARY_PERCENT > 0
  + REGENT_GENERATION_STRATEGY_CANARY_GATE=true  (ops gate, not funnel self-lock)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = Path(__file__).resolve().parent
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

from agentic_qualification_gate import (  # noqa: E402
    LADDER,
    find_latest_report,
    load_report,
    validate_promotion,
)

VALID = LADDER


def _upsert_env_file(path: Path, updates: dict[str, str]) -> None:
    vals: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.strip().startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v
    vals.update(updates)
    path.write_text(
        "\n".join(f"{k}={v}" for k, v in sorted(vals.items())) + "\n",
        encoding="utf-8",
    )


def _read_env_state() -> str:
    for name in (".runtime.env", ".deploy.env", ".env"):
        path = ROOT / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("REGENT_AGENTIC_QUALIFICATION_STATE="):
                return line.split("=", 1)[1].strip() or "DISABLED"
    return os.environ.get("REGENT_AGENTIC_QUALIFICATION_STATE", "DISABLED") or "DISABLED"


def _local(state: str, *, percent: str | None, gate: str | None) -> None:
    updates = {"REGENT_AGENTIC_QUALIFICATION_STATE": state}
    if percent is not None:
        updates["REGENT_GENERATION_STRATEGY_CANARY_PERCENT"] = percent
    if gate is not None:
        updates["REGENT_GENERATION_STRATEGY_CANARY_GATE"] = gate
    for name in (".env", ".deploy.env", ".runtime.env"):
        path = ROOT / name
        if path.exists() or name == ".env":
            _upsert_env_file(path, updates)
            print(f"local_upsert {path}")


def _remote(state: str, *, percent: str | None, gate: str | None) -> int:
    import paramiko
    from dotenv import dotenv_values

    cfg = dotenv_values(ROOT / ".env")
    host = cfg.get("SERVER_IP") or "118.31.171.159"
    password = cfg.get("LOGIN_PASSWORD")
    if not password:
        print("LOGIN_PASSWORD missing in .env", file=sys.stderr)
        return 2

    extras = ""
    if percent is not None:
        extras += f"\nensure_kv \"$ENVF\" REGENT_GENERATION_STRATEGY_CANARY_PERCENT {percent}"
    if gate is not None:
        extras += f"\nensure_kv \"$ENVF\" REGENT_GENERATION_STRATEGY_CANARY_GATE {gate}"

    remote = f"""
set -euo pipefail
ensure_kv() {{
  local f="$1" k="$2" v="$3"
  touch "$f"
  if grep -q "^${{k}}=" "$f" 2>/dev/null; then
    sed -i "s|^${{k}}=.*|${{k}}=${{v}}|" "$f"
  else
    echo "${{k}}=${{v}}" >> "$f"
  fi
}}
for ENVF in /opt/regent/.deploy.env /opt/regent/.runtime.env /opt/regent/.env; do
  ensure_kv "$ENVF" REGENT_AGENTIC_QUALIFICATION_STATE {state}{extras}
done
grep -E 'AGENTIC_QUALIFICATION|CANARY_PERCENT|CANARY_GATE' /opt/regent/.deploy.env | sort
echo 'NOTE: container env is baked at create-time; caller should run ops/recreate_from_deploy_env.py then sync_local_to_server.py'
docker exec regent-api python -c "from regent.config import get_settings; s=get_settings(); print('running_container', s.agentic_qualification_state, s.generation_strategy_canary_percent, s.generation_strategy_canary_gate)" || true
"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username="root", password=password, timeout=30)
    _, o, e = ssh.exec_command(remote, timeout=180)
    print((o.read() + e.read()).decode("utf-8", "replace"))
    code = o.channel.recv_exit_status()
    ssh.close()
    return code


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("state", choices=VALID)
    p.add_argument("--remote", action="store_true", help="Apply on S0 via SSH")
    p.add_argument(
        "--local",
        action="store_true",
        help="Upsert local .env files (default if not --remote)",
    )
    p.add_argument("--also-canary-percent", default=None, help="Optional percent 0-100")
    p.add_argument(
        "--also-gate",
        default=None,
        choices=["true", "false"],
        help="Optional canary gate",
    )
    p.add_argument(
        "--from-state",
        default=None,
        choices=VALID,
        help="Current state (default: read local env / DISABLED)",
    )
    p.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Qualification report JSON (default: latest docs/agentic-offline-qual-report-*.json)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Break-glass: skip adjacent/report checks (prints loud warning)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate only; do not write env or SSH",
    )
    args = p.parse_args()

    current = args.from_state or _read_env_state()
    if current not in VALID:
        current = "DISABLED"

    report = None
    report_path = args.report
    if report_path is None and not args.force:
        report_path = find_latest_report(ROOT / "docs")
    if report_path is not None:
        report = load_report(report_path.resolve())

    errors = validate_promotion(
        current=current,
        target=args.state,
        report=report,
        force=bool(args.force),
    )
    if errors:
        print("PROMOTION_DENIED", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print(
            "hint: contracts/full-golden → OFFLINE; live-model+V2 report → DOGFOOD+; "
            "adjacent step only; --force to break-glass",
            file=sys.stderr,
        )
        return 3

    if args.force and current != args.state:
        print(
            f"WARNING: --force bypassed adjacent/report gates ({current} → {args.state})",
            file=sys.stderr,
        )

    if args.state.startswith("CANARY") and args.also_canary_percent is None:
        print(
            "note: CANARY_* set without --also-canary-percent; "
            "traffic still needs percent>0 and gate=true",
            file=sys.stderr,
        )

    print(
        json.dumps(
            {
                "current": current,
                "target": args.state,
                "report": str(report_path) if report_path else None,
                "force": bool(args.force),
                "dry_run": bool(args.dry_run),
            },
            ensure_ascii=False,
        )
    )

    if args.dry_run:
        print("DRY_RUN_OK", args.state)
        return 0

    do_local = args.local or not args.remote
    if do_local:
        _local(args.state, percent=args.also_canary_percent, gate=args.also_gate)
    if args.remote:
        code = _remote(args.state, percent=args.also_canary_percent, gate=args.also_gate)
        if code != 0:
            return code
        # Restart cannot reload baked env — recreate from host .deploy.env.
        print("recreate_from_deploy_env…", file=sys.stderr)
        from recreate_from_deploy_env import main as recreate_main

        try:
            recreate_main()
        except SystemExit as exc:
            code = int(exc.code or 0)
        if code != 0:
            return code
        print(
            "NOTE: run python -B ops/sync_local_to_server.py after recreate "
            "(docker-cp'd code is replaced by image layers)",
            file=sys.stderr,
        )
        return 0
    print("OK", args.state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
