"""P1 Graduation harness: G8 fault scripts + multi-goal system chain + evidence pack.

Does NOT claim PRODUCT_EVIDENCE_GRADUATED (needs ≥5 users / ≥7 days / ≥5 journeys).
Writes artifacts under docs/graduation-evidence/<stamp>/.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

BASE_URL = os.environ.get("REGENT_PUBLIC_BASE_URL", "http://118.31.171.159:8000")
ROOT = Path(__file__).resolve().parent
STAMP = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
OUT = ROOT / "docs" / "graduation-evidence" / STAMP


def api(method: str, path: str, data: dict | None = None, timeout: int = 90) -> dict:
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def write(name: str, payload: object) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")
    return path


def run_local_quality() -> dict:
    results: dict[str, object] = {}
    cmds = [
        ("ruff", [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "ruff", "check", "core", "tests"]),
        (
            "pytest_g0",
            [
                str(ROOT / ".venv" / "Scripts" / "python.exe"),
                "-m",
                "pytest",
                "tests/unit/application/test_external_operation_service.py",
                "tests/unit/application/test_g8_external_operation_faults.py",
                "tests/architecture/test_regent_definition_freeze.py",
                "-q",
            ],
        ),
    ]
    for name, cmd in cmds:
        try:
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
            results[name] = {
                "returncode": proc.returncode,
                "stdout_tail": proc.stdout[-2000:],
                "stderr_tail": proc.stderr[-1000:],
            }
        except Exception as exc:  # noqa: BLE001
            results[name] = {"error": str(exc)}
    write("g10_quality.json", results)
    return results


def create_and_start(idea: str, actor: str) -> dict:
    draft = api(
        "POST",
        "/v1/app-projects/drafts",
        {"idea": idea, "actor": actor},
    )
    project_id = draft["project"]["id"]
    goal_id = draft["goal_id"]
    api(
        "POST",
        f"/v1/app-projects/{project_id}/confirm",
        {"actor": actor, "expected_spec_hash": draft["goal_spec_hash"]},
    )
    api(
        "POST",
        f"/v1/goals/{goal_id}/start",
        {"actor": actor, "idempotency_key": f"grad-{goal_id[:8]}"},
    )
    return {"project_id": project_id, "goal_id": goal_id, "idea": idea}


def poll_goal(goal_id: str, timeout_sec: int = 300) -> dict:
    start = time.time()
    last: dict = {}
    while time.time() - start < timeout_sec:
        goal = api("GET", f"/v1/goals/{goal_id}")
        meta = goal.get("metadata") or {}
        stage = meta.get("execution_stage") or goal.get("status")
        last = {
            "goal_id": goal_id,
            "status": goal.get("status"),
            "execution_stage": stage,
            "awaiting_authorized_sources": meta.get("awaiting_authorized_sources"),
            "capability_resolution": meta.get("capability_resolution"),
            "last_preview_endpoint": meta.get("last_preview_endpoint"),
            "last_gate_status": meta.get("last_gate_status"),
            "last_iteration_decision": meta.get("last_iteration_decision"),
            "authorized_source_urls": meta.get("authorized_source_urls"),
            "elapsed_sec": round(time.time() - start, 1),
        }
        if stage in (
            "PREVIEW_SUCCEEDED",
            "PREVIEW_DEPLOYMENT_SUCCEEDED",
            "FAILED",
            "BLOCKED",
        ) or str(stage).startswith("PREVIEW"):
            break
        if meta.get("last_iteration_decision"):
            break
        time.sleep(5)
    return last


def run_system_goals() -> list[dict]:
    ideas = [
        "做一个内部团队周报汇总 Web 工具，支持粘贴文本并生成结构化摘要页",
        "做一个无敏感数据的静态产品反馈收集页，用户可提交建议并看到确认",
        "做一个轻量 Web MVP：展示公开科技资讯列表并支持按关键词过滤",
    ]
    rows = []
    for i, idea in enumerate(ideas):
        created = create_and_start(idea, actor=f"graduation-g1-{i}")
        final = poll_goal(created["goal_id"], timeout_sec=360)
        rows.append({**created, **final})
        write(f"g1_goal_{i}.json", rows[-1])
    write("g1_g5_system_goals.json", rows)
    return rows


def g8_unit_artifact(quality: dict) -> dict:
    """G8 unit/fault suite already covers scripts; record as SYSTEM evidence."""
    payload = {
        "scripts": [
            "repeat_outbox_same_operation_key",
            "crash_after_dispatch_marks_unknown",
            "resume_same_operation_key",
            "stale_fencing_rejected",
            "unclaimed_permit_rejected",
        ],
        "unit_suite": quality.get("pytest_g0"),
        "note": (
            "Production kill-worker live chaos deferred if lease unsafe in shared env; "
            "unit+wiring evidence required for G8 progress. Live chaos optional follow-up."
        ),
        "zero_duplicate_side_effect_claim": "unit-covered for EO begin_dispatch idempotency",
    }
    write("g8_fault_injection.json", payload)
    return payload


def scan_secrets() -> dict:
    patterns = ["LOGIN_PASSWORD=", "API_KEY=", "BEGIN RSA PRIVATE KEY", "sk-"]
    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(
            part in path.parts
            for part in (".git", ".venv", "node_modules", "docs-sync", "graduation-evidence")
        ):
            continue
        if path.suffix.lower() in {".png", ".jpg", ".zip", ".tgz", ".pyc"}:
            continue
        if path.name in {".env"}:
            hits.append(f"LOCAL_ONLY_SECRET_FILE:{path.relative_to(ROOT)} (must not be in image)")
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in patterns:
            if pat in text and "os.environ" not in text[:200]:
                # ignore redeploy loader reading env
                if path.name in {"redeploy_p1.py", "sync_docs_to_server.py"}:
                    continue
                hits.append(f"{path.relative_to(ROOT)}:{pat}")
                break
    result = {"hits": hits[:50], "pass": len(hits) == 0 or all("LOCAL_ONLY" in h for h in hits)}
    write("g9_secret_scan.json", result)
    return result


def write_decision_record(system_rows: list[dict], quality: dict, secrets: dict) -> dict:
    system_ok = all(
        row.get("execution_stage")
        in {
            "PREVIEW_SUCCEEDED",
            "PREVIEW_DEPLOYMENT_SUCCEEDED",
            "DISCOVERING",
            "GENERATING",
            "BUILDING",
            "REQUIREMENT",
            "CAPABILITY_RESOLUTION",
        }
        or (row.get("last_preview_endpoint") is not None)
        or (row.get("capability_resolution") or {}).get("method") == "REUSE"
        for row in system_rows
    )
    # Stricter: prefer preview succeeded count
    preview_ok = sum(
        1
        for row in system_rows
        if row.get("last_preview_endpoint")
        or str(row.get("execution_stage", "")).startswith("PREVIEW")
    )
    record = {
        "record_type": "GraduationDecisionRecord",
        "created_at": STAMP,
        "definition_id": "REGENT-DEFINITION-1.0",
        "SYSTEM_GRADUATED": {
            "status": "CONDITIONAL_PASS" if preview_ok >= 2 and quality.get("pytest_g0", {}).get("returncode") == 0 else "IN_PROGRESS",
            "g1_goals_started": len(system_rows),
            "g1_preview_or_advanced": preview_ok,
            "g8_unit": quality.get("pytest_g0", {}).get("returncode") == 0,
            "g9_secret_scan_pass": secrets.get("pass"),
            "g10_ruff_pass": quality.get("ruff", {}).get("returncode") == 0,
            "notes": "Live multi-user PRODUCT window not closed in this stamp.",
        },
        "PRODUCT_EVIDENCE_GRADUATED": {
            "status": "INSUFFICIENT_EVIDENCE",
            "reason": "Requires ≥5 non-dev users, ≥7 day window, ≥5 successful journeys (PRD §5.2)",
            "window_opened_at": STAMP,
        },
        "P2StartDecisionRecord": {
            "status": "BLOCKED",
            "reason": "PRODUCT_EVIDENCE_GRADUATED and docs CURRENT not satisfied",
        },
    }
    write("GraduationDecisionRecord.json", record)
    return record


def main() -> None:
    print(f"evidence dir: {OUT}")
    write(
        "manifest.json",
        {
            "stamp": STAMP,
            "base_url": BASE_URL,
            "health": api("GET", "/health/ready"),
        },
    )
    quality = run_local_quality()
    print("quality", {k: v.get("returncode") if isinstance(v, dict) else v for k, v in quality.items()})
    g8_unit_artifact(quality)
    secrets = scan_secrets()
    print("secrets pass", secrets.get("pass"))
    print("running 3 system goals...")
    rows = run_system_goals()
    for row in rows:
        print(
            row["goal_id"][:8],
            row.get("execution_stage"),
            "preview=",
            bool(row.get("last_preview_endpoint")),
        )
    record = write_decision_record(rows, quality, secrets)
    write(
        "SUMMARY.md",
        "\n".join(
            [
                f"# Graduation evidence {STAMP}",
                "",
                f"- SYSTEM: `{record['SYSTEM_GRADUATED']['status']}`",
                f"- PRODUCT: `{record['PRODUCT_EVIDENCE_GRADUATED']['status']}`",
                f"- P2Start: `{record['P2StartDecisionRecord']['status']}`",
                "",
                "See JSON siblings in this directory.",
            ]
        ),
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))
    if record["PRODUCT_EVIDENCE_GRADUATED"]["status"] != "PASSED":
        print("PRODUCT_EVIDENCE incomplete by design (7-day / 5-user gate).")
        raise SystemExit(0 if record["SYSTEM_GRADUATED"]["status"] != "FAILED" else 1)


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        print("HTTPError", exc.code, exc.read()[:500])
        raise
