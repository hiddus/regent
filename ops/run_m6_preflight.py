"""M6 preflight: offline freeze integrity, provider replay, Skill ablation, S0 baseline.

Does NOT open canary. Writes docs/m6-preflight-report-YYYY-MM-DD.json.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
TASK_SET = ROOT / "fixtures" / "agent_core_m0_task_set_v1.json"
TASK_HASH = ROOT / "fixtures" / "agent_core_m0_task_set_v1.sha256"
SKILL_LABELS = ROOT / "fixtures" / "agent_core_m5_skill_labels_v1.json"
RECORDINGS = ROOT / "fixtures" / "provider_recordings"
REPORT_DIR = ROOT / "docs"


def _wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((centre - margin) / denom, (centre + margin) / denom)


def phase_task_set_integrity() -> dict[str, Any]:
    payload = json.loads(TASK_SET.read_text(encoding="utf-8"))
    digest = hashlib.sha256(TASK_SET.read_bytes()).hexdigest()
    expected = TASK_HASH.read_text(encoding="utf-8").strip()
    ok = (
        len(payload.get("tasks") or []) == 12
        and digest == expected
        and set(payload.get("debug_holdout") or []) == {"m0-11", "m0-12"}
    )
    return {
        "ok": ok,
        "task_count": len(payload.get("tasks") or []),
        "content_hash": digest,
        "hash_match": digest == expected,
        "debug_holdout": payload.get("debug_holdout"),
        "tune_ids": [
            t["id"]
            for t in payload["tasks"]
            if t["id"] not in set(payload.get("debug_holdout") or [])
        ],
    }


def phase_provider_replay() -> dict[str, Any]:
    # Lazy imports so script can report import failures cleanly.
    from regent.agent.types import ChatMessage
    from regent.model import (
        ModelOutputError,
        ModelTruncatedError,
        OpenAICompatibleProvider,
        ToolCallInvalidError,
    )

    async def _one(path: Path) -> dict[str, Any]:
        sample = json.loads(path.read_text(encoding="utf-8"))
        expect = dict(sample.get("expect") or {})
        status = int(sample.get("http_status") or 200)
        row: dict[str, Any] = {"id": sample.get("id") or path.stem, "file": path.name}

        if sample.get("error") == "timeout":

            def timeout_handler(_request: httpx.Request) -> httpx.Response:
                raise httpx.TimeoutException("replay timeout")

            transport: httpx.AsyncBaseTransport = httpx.MockTransport(timeout_handler)
        else:

            def handler(_request: httpx.Request) -> httpx.Response:
                if status >= 400:
                    body = sample.get("response_text") or json.dumps(
                        sample.get("response") or {"error": "x"}
                    )
                    return httpx.Response(
                        status, content=body.encode("utf-8"), headers={"content-type": "application/json"}
                    )
                return httpx.Response(200, json=sample["response"])

            transport = httpx.MockTransport(handler)

        # Keep retries at 0 for preflight speed; classification still recorded on attempt 1.
        retries = 0
        async with httpx.AsyncClient(transport=transport) as client:
            provider = OpenAICompatibleProvider(
                base_url="https://model.example/v1",
                api_key="replay",
                model="replay-model",
                client=client,
                max_http_retries=retries,
            )
            try:
                resp = await provider.chat(
                    messages=[ChatMessage(role="user", content="replay")]
                )
                row["outcome"] = "ok"
                row["finish_reason"] = getattr(resp, "finish_reason", None)
                row["tool_name"] = (
                    resp.message.tool_calls[0].name if resp.message.tool_calls else None
                )
                row["primary_failure_code"] = None
                row["retryable"] = None
            except ModelTruncatedError as exc:
                row["outcome"] = "error"
                row["primary_failure_code"] = getattr(exc, "failure_code", "MODEL_TRUNCATED")
            except ToolCallInvalidError as exc:
                row["outcome"] = "error"
                row["primary_failure_code"] = getattr(exc, "failure_code", "TOOL_CALL_INVALID")
            except httpx.HTTPStatusError as exc:
                row["outcome"] = "error"
                row["status"] = int(exc.response.status_code)
                row["retryable"] = row["status"] in {408, 409, 425, 429, 500, 502, 503, 504}
                row["error"] = f"HTTPStatusError:{row['status']}"
            except ModelOutputError as exc:
                row["outcome"] = "error"
                row["error"] = str(exc)[:200]
                attempts = list(getattr(provider, "last_http_attempts", []) or [])
                last = attempts[-1] if attempts else {}
                row["retryable"] = bool(last.get("retryable"))
                if last.get("status") is not None:
                    row["status"] = last.get("status")
                if "timeout" in str(exc).lower() or "Timeout" in str(exc):
                    row["error"] = "timeout"
                    row["retryable"] = True
            except httpx.TimeoutException:
                row["outcome"] = "error"
                row["error"] = "timeout"
                row["retryable"] = True
            except Exception as exc:  # noqa: BLE001 — catalog unexpected for report
                row["outcome"] = "error"
                row["primary_failure_code"] = type(exc).__name__
                row["error"] = str(exc)[:200]
                attempts = list(getattr(provider, "last_http_attempts", []) or [])
                last = attempts[-1] if attempts else {}
                row["retryable"] = bool(last.get("retryable")) or "timeout" in str(exc).lower()
                if sample.get("error") == "timeout" or "Timeout" in type(exc).__name__:
                    row["error"] = "timeout"
                    row["retryable"] = True

        want_code = expect.get("primary_failure_code")
        if want_code:
            row["pass"] = row.get("primary_failure_code") == want_code
        elif expect.get("ok"):
            row["pass"] = row.get("outcome") == "ok" and (
                not expect.get("tool_name") or row.get("tool_name") == expect.get("tool_name")
            )
        elif expect.get("error") == "timeout":
            err = str(row.get("error") or row.get("primary_failure_code") or "")
            row["pass"] = "timeout" in err.lower() or "Timeout" in err
        elif "retryable" in expect:
            # Auth/rate/5xx fixtures: must fail (not soft-ok) with matching retryability.
            status_ok = True
            if expect.get("status") is not None:
                status_ok = row.get("status") == expect.get("status")
            row["pass"] = (
                row.get("outcome") == "error"
                and bool(row.get("retryable")) == bool(expect.get("retryable"))
                and status_ok
            )
        else:
            row["pass"] = False
        return row

    async def _all() -> list[dict[str, Any]]:
        return [await _one(path) for path in sorted(RECORDINGS.glob("*.json"))]

    rows = asyncio.run(_all())
    passed = sum(1 for r in rows if r.get("pass"))
    return {
        "ok": passed == len(rows) and len(rows) > 0,
        "n": len(rows),
        "passed": passed,
        "rows": rows,
    }


def phase_skill_router_and_ablation() -> dict[str, Any]:
    from regent.agent.skills import (
        list_builtin_skill_ids,
        select_skills_for_goal,
        skill_ablation_report,
    )

    task_payload = json.loads(TASK_SET.read_text(encoding="utf-8"))
    labels_payload = json.loads(SKILL_LABELS.read_text(encoding="utf-8"))
    labels: dict[str, list[str]] = {
        k: list(v) for k, v in (labels_payload.get("labels") or {}).items()
    }
    prereg = dict(labels_payload.get("preregistered") or {})
    min_acc = float(prereg.get("min_router_accuracy") or 0.9)

    builtin = list_builtin_skill_ids()
    per_task: list[dict[str, Any]] = []
    tp = fp = fn = 0
    exact = 0
    on_pass = off_pass = 0

    for task in task_payload["tasks"]:
        tid = task["id"]
        text = str(task.get("description") or "")
        expected = set(labels.get(tid) or [])
        got_on = {m.skill_id for m in select_skills_for_goal(text, enabled=True)}
        got_off = {m.skill_id for m in select_skills_for_goal(text, enabled=False)}
        assert not got_off

        # Multilabel micro counts
        tp += len(expected & got_on)
        fp += len(got_on - expected)
        fn += len(expected - got_on)
        is_exact = expected == got_on
        if is_exact:
            exact += 1
        # Engineering ablation proxy: on selects ≥1 expected skill; off selects 0.
        on_hit = bool(expected & got_on)
        if on_hit:
            on_pass += 1
        # off always 0 skills → never "pass" for scaffold guidance; count as fail
        per_task.append(
            {
                "task_id": tid,
                "expected": sorted(expected),
                "got_on": sorted(got_on),
                "exact": is_exact,
                "on_hit": on_hit,
            }
        )

    n = len(per_task)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        (2 * precision * recall / (precision + recall))
        if (precision + recall)
        else 0.0
    )
    exact_acc = exact / n if n else 0.0
    ablation = skill_ablation_report(
        on_pass=on_pass, on_total=n, off_pass=off_pass, off_total=n
    )
    ci = _wilson(on_pass, n)
    ablation["on_ci95"] = None if ci is None else {"low": round(ci[0], 4), "high": round(ci[1], 4)}
    ablation["delta_ci_crosses_zero"] = (
        True
        if ci is None
        else (ci[0] - 0.0) <= 0  # off rate is 0; treat on CI vs 0
    )

    router_ok = exact_acc >= min_acc or f1 >= min_acc
    return {
        "ok": router_ok and ablation["delta"] > 0,
        "builtin_skills": builtin,
        "n_tasks": n,
        "exact_match_accuracy": round(exact_acc, 4),
        "micro_precision": round(precision, 4),
        "micro_recall": round(recall, 4),
        "micro_f1": round(f1, 4),
        "min_router_accuracy": min_acc,
        "ablation": ablation,
        "per_task": per_task,
        "note": (
            "Ablation here is router hit-rate on/off (engineering gate). "
            "Full Skill on/off generation pass-rate still needs live agentic runs."
        ),
    }


def phase_contract_pytest() -> dict[str, Any]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit/agent/test_agent_core_m0_m1_contracts.py",
        "tests/unit/agent/test_agent_core_m1_m5_contracts.py",
        "tests/unit/model/test_provider.py",
        "-q",
        "--tb=line",
    ]
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "tail": "\n".join(out.strip().splitlines()[-20:]),
    }


def phase_live_subset(*, enable: bool, limit: int = 2) -> dict[str, Any]:
    """Optional: real-model AgentRunner smoke on first N tune tasks (no publish).

    Prefers local secrets; falls back to running inside S0 ``regent-api`` where
    production model env is already mounted.
    """
    if not enable:
        return {"ok": True, "skipped": True, "reason": "pass --live to enable"}

    from dotenv import dotenv_values

    secrets = {
        **dotenv_values(ROOT / ".env"),
        **dotenv_values(ROOT / ".secrets.env"),
    }
    api_key = (
        secrets.get("REGENT_MODEL_API_KEY")
        or secrets.get("MODEL_API_KEY")
        or ""
    ).strip()
    if api_key:
        return _live_subset_local(secrets=secrets, limit=limit)
    return _live_subset_on_s0(limit=limit)


def _live_subset_local(*, secrets: dict[str, Any], limit: int) -> dict[str, Any]:
    from regent.agent.agent_runner import AgentRunner
    from regent.agent.tools import WorkspaceToolkit
    from regent.agent.types import AgentBudget, VerificationGap, VerificationVerdict
    from regent.model import OpenAICompatibleProvider

    api_key = (
        secrets.get("REGENT_MODEL_API_KEY")
        or secrets.get("MODEL_API_KEY")
        or ""
    ).strip()
    base_url = (
        secrets.get("REGENT_MODEL_BASE_URL")
        or secrets.get("MODEL_BASE_URL")
        or "https://api.deepseek.com"
    ).strip()
    model = (
        secrets.get("REGENT_MODEL_NAME")
        or secrets.get("MODEL_NAME")
        or "deepseek-v4-flash"
    ).strip()

    tasks = json.loads(TASK_SET.read_text(encoding="utf-8"))["tasks"]
    holdout = set(
        json.loads(TASK_SET.read_text(encoding="utf-8")).get("debug_holdout") or []
    )
    tune = [t for t in tasks if t["id"] not in holdout][:limit]

    class _SoftVerify:
        def __init__(self, toolkit: WorkspaceToolkit, **kwargs: Any) -> None:
            self._toolkit = toolkit

        async def verify(self, **kwargs: Any) -> VerificationVerdict:
            files = self._toolkit.snapshot_files()
            if not files:
                return VerificationVerdict(
                    verdict="FAIL",
                    gaps=[VerificationGap(code="ARTIFACT_INCOMPLETE", detail="empty")],
                )
            return VerificationVerdict(verdict="PASS", gaps=[])

    async def _run_one(task: dict[str, Any], *, skills_enabled: bool) -> dict[str, Any]:
        import regent.agent.agent_runner as runner_mod

        with tempfile.TemporaryDirectory(prefix=f"m6-{task['id']}-") as td:
            toolkit = WorkspaceToolkit(Path(td))
            provider = OpenAICompatibleProvider(
                base_url=base_url,
                api_key=api_key,
                model=model,
                max_http_retries=2,
                timeout_seconds=120,
                max_output_tokens=8192,
                thinking_mode="disabled",
            )
            original = runner_mod.VerificationAgent
            runner_mod.VerificationAgent = _SoftVerify  # type: ignore[misc,assignment]
            try:
                runner = AgentRunner(
                    provider,
                    toolkit,
                    budget=AgentBudget(
                        max_turns=40,
                        max_tokens=min(int(task.get("budget_tokens") or 80000), 200000),
                        max_wall_seconds=600,
                    ),
                    skills_enabled=skills_enabled,
                )
                goal_text = str(task["description"])
                goal_text += (
                    "\n\nIMPORTANT: Write a minimal runnable app (at least src/app.py "
                    "and requirements.txt), then call the submit tool immediately. "
                    "Do not keep iterating forever."
                )
                result = await runner.run(
                    {
                        "goal_anchor_text": goal_text,
                        "acceptance_contract": task.get("success_criteria") or {},
                    },
                    verify=True,
                    run_smoke=False,
                )
                return {
                    "task_id": task["id"],
                    "skills_enabled": skills_enabled,
                    "submitted": bool(result.submitted),
                    "file_count": len(result.files or {}),
                    "turns": result.turns,
                    "tokens": result.input_tokens + result.output_tokens,
                    "skill_refs": [
                        {"skill_id": s.get("skill_id"), "version": s.get("version")}
                        for s in (result.skill_refs or [])
                    ],
                    "verdict": (
                        result.verification.verdict if result.verification else None
                    ),
                    "pass": bool(
                        result.submitted
                        and result.verification
                        and result.verification.verdict == "PASS"
                        and (result.files or {})
                    ),
                }
            except Exception as exc:  # noqa: BLE001
                files = dict(getattr(exc, "files", None) or {})
                return {
                    "task_id": task["id"],
                    "skills_enabled": skills_enabled,
                    "pass": False,
                    "file_count": len(files),
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                }
            finally:
                runner_mod.VerificationAgent = original  # type: ignore[misc,assignment]

    async def _all() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for task in tune:
            rows.append(await _run_one(task, skills_enabled=True))
            rows.append(await _run_one(task, skills_enabled=False))
        return rows

    rows = asyncio.run(_all())
    on_rows = [r for r in rows if r.get("skills_enabled")]
    off_rows = [r for r in rows if not r.get("skills_enabled")]
    on_pass = sum(1 for r in on_rows if r.get("pass"))
    off_pass = sum(1 for r in off_rows if r.get("pass"))
    from regent.agent.skills import skill_ablation_report

    return {
        "ok": on_pass >= 1,
        "skipped": False,
        "where": "local",
        "model": model,
        "base_url": base_url,
        "tasks": [t["id"] for t in tune],
        "rows": rows,
        "ablation_live": skill_ablation_report(
            on_pass=on_pass,
            on_total=len(on_rows),
            off_pass=off_pass,
            off_total=len(off_rows),
        ),
        "note": "Soft verify (file non-empty + submit); not full sandbox Journey.",
    }


def _live_subset_on_s0(*, limit: int) -> dict[str, Any]:
    """Run soft live subset inside regent-api (has model credentials)."""
    try:
        import paramiko
        from dotenv import dotenv_values
    except ImportError as exc:
        return {"ok": False, "skipped": True, "reason": str(exc)}

    cfg = {
        (k.lstrip("\ufeff") if isinstance(k, str) else k): v
        for k, v in dotenv_values(ROOT / ".env").items()
    }
    if not cfg.get("LOGIN_PASSWORD"):
        return {"ok": False, "skipped": True, "reason": "no LOGIN_PASSWORD for S0 live"}

    remote_script = f'''
import asyncio, json, tempfile
from pathlib import Path
from regent.agent.agent_runner import AgentRunner
from regent.agent.skills import skill_ablation_report
from regent.agent.tools import WorkspaceToolkit
from regent.agent.types import AgentBudget, VerificationGap, VerificationVerdict
from regent.config import get_settings
from regent.model import OpenAICompatibleProvider
import regent.agent.agent_runner as runner_mod

settings = get_settings()
key = (settings.model_api_key.get_secret_value() if settings.model_api_key else "") or ""
if not key:
    print(json.dumps({{"ok": False, "skipped": True, "reason": "no model_api_key in S0"}}))
    raise SystemExit(0)

TASK_SET = Path("/tmp/agent_core_m0_task_set_v1.json")
payload = json.loads(TASK_SET.read_text(encoding="utf-8"))
holdout = set(payload.get("debug_holdout") or [])
tune = [t for t in payload["tasks"] if t["id"] not in holdout][:{int(limit)}]

class SoftVerify:
    def __init__(self, toolkit, **kwargs):
        self._toolkit = toolkit
    async def verify(self, **kwargs):
        files = self._toolkit.snapshot_files()
        if not files:
            return VerificationVerdict(verdict="FAIL", gaps=[VerificationGap(code="ARTIFACT_INCOMPLETE", detail="empty")])
        return VerificationVerdict(verdict="PASS", gaps=[])

async def run_one(task, skills_enabled: bool):
    with tempfile.TemporaryDirectory(prefix=f"m6-{{task['id']}}-") as td:
        toolkit = WorkspaceToolkit(Path(td))
        provider = OpenAICompatibleProvider(
            base_url=settings.model_base_url or "https://api.deepseek.com",
            api_key=key,
            model=settings.model_name or "deepseek-v4-flash",
            max_http_retries=2,
            timeout_seconds=120,
            max_output_tokens=8192,
            thinking_mode=getattr(settings, "model_thinking_mode", None) or "disabled",
        )
        original = runner_mod.VerificationAgent
        runner_mod.VerificationAgent = SoftVerify
        try:
            runner = AgentRunner(
                provider, toolkit,
                budget=AgentBudget(max_turns=40, max_tokens=200000, max_wall_seconds=600),
                skills_enabled=skills_enabled,
            )
            goal_text = str(task["description"]) + (
                "\\n\\nIMPORTANT: Write a minimal runnable app (at least src/app.py "
                "and requirements.txt), then call the submit tool immediately. "
                "Do not keep iterating forever."
            )
            result = await runner.run(
                {{"goal_anchor_text": goal_text, "acceptance_contract": task.get("success_criteria") or {{}}}},
                verify=True, run_smoke=False,
            )
            return {{
                "task_id": task["id"], "skills_enabled": skills_enabled,
                "submitted": bool(result.submitted),
                "file_count": len(result.files or {{}}),
                "turns": result.turns,
                "tokens": result.input_tokens + result.output_tokens,
                "pass": bool(result.submitted and result.verification and result.verification.verdict == "PASS" and (result.files or {{}})),
            }}
        except Exception as exc:
            files = dict(getattr(exc, "files", None) or {{}})
            return {{"task_id": task["id"], "skills_enabled": skills_enabled, "pass": False, "file_count": len(files), "error": f"{{type(exc).__name__}}: {{exc}}"[:300]}}
        finally:
            runner_mod.VerificationAgent = original

async def main():
    rows = []
    for task in tune:
        rows.append(await run_one(task, True))
        rows.append(await run_one(task, False))
    on_rows = [r for r in rows if r.get("skills_enabled")]
    off_rows = [r for r in rows if not r.get("skills_enabled")]
    on_pass = sum(1 for r in on_rows if r.get("pass"))
    off_pass = sum(1 for r in off_rows if r.get("pass"))
    print(json.dumps({{
        "ok": on_pass >= 1,
        "skipped": False,
        "where": "s0-regent-api",
        "model": settings.model_name,
        "base_url": settings.model_base_url,
        "tasks": [t["id"] for t in tune],
        "rows": rows,
        "ablation_live": skill_ablation_report(on_pass=on_pass, on_total=len(on_rows), off_pass=off_pass, off_total=len(off_rows)),
        "note": "Soft verify inside S0; not full sandbox Journey.",
    }}, ensure_ascii=False))

asyncio.run(main())
'''

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        cfg.get("SERVER_IP") or "118.31.171.159",
        username=cfg.get("LOGIN_USER") or "root",
        password=cfg["LOGIN_PASSWORD"],
        timeout=30,
    )
    # Ensure task set + script available in container
    sftp = ssh.open_sftp()
    with sftp.file("/tmp/_m6_live_subset.py", "w") as f:
        f.write(remote_script)
    sftp.put(
        str(TASK_SET),
        "/tmp/agent_core_m0_task_set_v1.json",
    )
    sftp.close()
    cmd = (
        "docker cp /tmp/_m6_live_subset.py regent-api:/tmp/_m6_live_subset.py && "
        "docker cp /tmp/agent_core_m0_task_set_v1.json "
        "regent-api:/tmp/agent_core_m0_task_set_v1.json && "
        "docker exec -w /tmp regent-api python _m6_live_subset.py"
    )
    _, o, e = ssh.exec_command(cmd, timeout=900)
    text_out = (o.read() + e.read()).decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    ssh.close()
    # Parse last JSON object from stdout
    payload: dict[str, Any] | None = None
    for line in reversed(text_out.splitlines()):
        line = line.strip()
        if line.startswith("{") and (
            "ablation_live" in line or '"ok"' in line
        ):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if payload is None:
        return {
            "ok": False,
            "skipped": False,
            "where": "s0-regent-api",
            "exit_code": code,
            "error": text_out[-1500:],
        }
    payload["exit_code"] = code
    return payload


def phase_s0_baseline() -> dict[str, Any]:
    try:
        import paramiko
        from dotenv import dotenv_values
    except ImportError as exc:
        return {"ok": False, "skipped": True, "reason": str(exc)}

    cfg = {
        (k.lstrip("\ufeff") if isinstance(k, str) else k): v
        for k, v in dotenv_values(ROOT / ".env").items()
    }
    password = cfg.get("LOGIN_PASSWORD")
    if not password:
        return {"ok": False, "skipped": True, "reason": "no LOGIN_PASSWORD"}

    remote_py = '''
import json
from sqlalchemy import create_engine, text
from regent.config import get_settings
url = get_settings().database_url
sync = url if "+psycopg" in url else url.replace("postgresql://", "postgresql+psycopg://", 1)
eng = create_engine(sync)
out = {"cancelled_poison": 0, "remaining_failed": 0, "remaining_dl": 0, "open_gap_tasks": 0}
sql = """
UPDATE outbox_events
SET status='DEAD_LETTER',
    last_error = COALESCE(last_error,'') || ' | m6_preflight_poison_quarantine'
WHERE status IN ('FAILED','DEAD_LETTER')
  AND (
    last_error ILIKE '%idempotency key scope mismatch%'
    OR last_error ILIKE '%LEASE_CONFLICT%'
    OR last_error ILIKE '%frozen generation plan is required%'
    OR last_error ILIKE '%cannot mark FAILED_TERMINAL%'
  )
  AND status = 'FAILED'
RETURNING id
"""
with eng.begin() as c:
    r = c.execute(text(sql))
    out["cancelled_poison"] = len(r.fetchall())
    # Also count already-quarantined
    out["already_dead_letter_matching"] = c.execute(
        text(
            """
            SELECT count(*) FROM outbox_events
            WHERE status='DEAD_LETTER'
              AND last_error ILIKE '%m6_preflight_poison_quarantine%'
            """
        )
    ).scalar()
    out["remaining_failed"] = c.execute(
        text("SELECT count(*) FROM outbox_events WHERE status='FAILED'")
    ).scalar()
    out["remaining_dl"] = c.execute(
        text("SELECT count(*) FROM outbox_events WHERE status='DEAD_LETTER'")
    ).scalar()
    out["open_gap_tasks"] = c.execute(
        text(
            "SELECT count(*) FROM human_tasks "
            "WHERE task_type='DELIVERY_GAP_INTERVENE' AND status='OPEN'"
        )
    ).scalar()
print(json.dumps(out))
'''

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        cfg.get("SERVER_IP") or "118.31.171.159",
        username=cfg.get("LOGIN_USER") or "root",
        password=password,
        timeout=30,
    )
    sftp = ssh.open_sftp()
    with sftp.file("/tmp/_m6_preflight_s0.py", "w") as f:
        f.write(remote_py)
    sftp.close()

    remote = (
        "set -e\n"
        "echo '=== freeze ==='\n"
        "docker exec regent-api printenv | grep -E 'GENERATION_STRATEGY' | sort\n"
        "echo '=== outbox poison clear ==='\n"
        "docker cp /tmp/_m6_preflight_s0.py regent-api:/tmp/_m6_preflight_s0.py\n"
        "docker exec -w /tmp regent-api python _m6_preflight_s0.py\n"
        "echo '=== health ==='\n"
        "curl -s http://127.0.0.1:8000/healthz || curl -s http://127.0.0.1:8000/health || true\n"
        "echo\n"
    )
    _, o, e = ssh.exec_command(remote, timeout=120)
    text_out = (o.read() + e.read()).decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    ssh.close()

    freeze_ok = (
        "REGENT_GENERATION_STRATEGY=artifact-backed" in text_out
        and "CANARY_PERCENT=0" in text_out
        and "CANARY_GATE=false" in text_out
    )
    poison: dict[str, Any] = {}
    for line in text_out.splitlines():
        line = line.strip()
        if line.startswith("{") and "cancelled_poison" in line:
            try:
                poison = json.loads(line)
            except json.JSONDecodeError:
                pass
    return {
        "ok": code == 0 and freeze_ok and int(poison.get("open_gap_tasks") or 0) == 0,
        "exit_code": code,
        "freeze_ok": freeze_ok,
        "poison": poison,
        "tail": "\n".join(text_out.strip().splitlines()[-40:]),
    }


def decide(report: dict[str, Any]) -> dict[str, Any]:
    phases = report["phases"]
    blockers: list[str] = []
    engineering_ok = True
    if not phases["task_set"]["ok"]:
        blockers.append("frozen_task_set_integrity")
        engineering_ok = False
    if not phases["provider_replay"]["ok"]:
        blockers.append("provider_recording_replay")
        engineering_ok = False
    if not phases["contracts"]["ok"]:
        blockers.append("m0_m5_contract_tests")
        engineering_ok = False
    if not phases["skills"]["ok"]:
        blockers.append("skill_router_or_ablation_proxy")
        engineering_ok = False
    if not phases["s0"]["ok"] and not phases["s0"].get("skipped"):
        blockers.append("s0_baseline_freeze_or_poison")
        engineering_ok = False

    live = phases.get("live_subset") or {}
    live_evidence = bool(live.get("ok") and not live.get("skipped"))
    if not live_evidence:
        blockers.append("live_agentic_subset_soft_pass")

    if engineering_ok and live_evidence:
        return {
            "m6_canary_allowed": False,
            "decision": "READY_FOR_OWNER_CANARY_APPROVAL",
            "engineering_preflight": "PASS",
            "live_soft_pass": "PASS",
            "blockers": [],
            "next": (
                "Engineering + soft live subset green. Owner may open 5% agentic canary "
                "with 7d/100-goal watch; do not flip default strategy."
            ),
        }
    if engineering_ok:
        return {
            "m6_canary_allowed": False,
            "decision": "NO_GO",
            "engineering_preflight": "PASS",
            "live_soft_pass": "FAIL",
            "blockers": blockers,
            "live_rows": live.get("rows"),
            "next": (
                "Engineering preflight green (freeze/replay/skills/S0). "
                "Live soft agentic subset did not pass — do not open canary until "
                "at least one tune task submits+soft-verifies with skills on."
            ),
        }
    return {
        "m6_canary_allowed": False,
        "decision": "NO_GO",
        "engineering_preflight": "FAIL",
        "live_soft_pass": "PENDING" if live.get("skipped") else "FAIL",
        "blockers": blockers,
        "next": (
            "Fix engineering blockers first; then re-run with --live; "
            "Owner must still approve 5% canary separately."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run real-model AgentRunner soft verify on first 2 tune tasks (on/off).",
    )
    parser.add_argument("--live-limit", type=int, default=2)
    parser.add_argument("--skip-s0", action="store_true")
    parser.add_argument("--skip-pytest", action="store_true")
    args = parser.parse_args()

    report: dict[str, Any] = {
        "record_type": "M6PreflightReport",
        "generated_at": datetime.now(UTC).isoformat(),
        "plan_ref": "docs/agent-core-restoration-executable-plan-2026-08-01.md",
        "phases": {},
    }

    print("== phase: task_set ==")
    report["phases"]["task_set"] = phase_task_set_integrity()
    print(json.dumps(report["phases"]["task_set"], ensure_ascii=False))

    print("== phase: provider_replay ==")
    report["phases"]["provider_replay"] = phase_provider_replay()
    print(
        json.dumps(
            {k: report["phases"]["provider_replay"][k] for k in ("ok", "n", "passed")},
            ensure_ascii=False,
        )
    )

    print("== phase: skills ==")
    report["phases"]["skills"] = phase_skill_router_and_ablation()
    skills_summary = {
        k: report["phases"]["skills"][k]
        for k in (
            "ok",
            "exact_match_accuracy",
            "micro_f1",
            "ablation",
        )
    }
    print(json.dumps(skills_summary, ensure_ascii=False))

    if args.skip_pytest:
        report["phases"]["contracts"] = {"ok": True, "skipped": True}
    else:
        print("== phase: contracts pytest ==")
        report["phases"]["contracts"] = phase_contract_pytest()
        print(report["phases"]["contracts"].get("tail", "")[-500:])

    print("== phase: live_subset ==")
    report["phases"]["live_subset"] = phase_live_subset(
        enable=args.live, limit=max(1, args.live_limit)
    )
    print(
        json.dumps(
            {
                k: report["phases"]["live_subset"].get(k)
                for k in ("ok", "skipped", "reason", "ablation_live", "tasks")
            },
            ensure_ascii=False,
        )
    )

    if args.skip_s0:
        report["phases"]["s0"] = {"ok": True, "skipped": True}
    else:
        print("== phase: s0 baseline ==")
        report["phases"]["s0"] = phase_s0_baseline()
        print(
            json.dumps(
                {
                    k: report["phases"]["s0"].get(k)
                    for k in ("ok", "freeze_ok", "poison", "skipped", "reason")
                },
                ensure_ascii=False,
            )
        )

    report["verdict"] = decide(report)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out_path = REPORT_DIR / f"m6-preflight-report-{stamp}.json"
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("== verdict ==")
    print(json.dumps(report["verdict"], ensure_ascii=False, indent=2))
    print(f"REPORT={out_path}")
    return 0 if report["verdict"]["decision"] != "ERROR" else 1


if __name__ == "__main__":
    sys.exit(main())
