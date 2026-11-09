"""O4: regent doctor — ops self-check (distinct from /health liveness)."""

from __future__ import annotations

from typing import Any

from regent.application.agent_loop_exit import utc_now_iso
from regent.application.workflow_presets import list_workflow_presets

DOCTOR_SCHEMA = "regent.doctor"
DOCTOR_VERSION = 1


def run_doctor(
    *,
    db_ok: bool | None = None,
    worker_hint: str | None = None,
    delivery_review_seeded: bool | None = None,
    canary_percent: float | None = None,
    settings_summary: dict[str, Any] | None = None,
    host_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail[:400]})

    if db_ok is None:
        add("database", True, "not probed (pass db_ok to probe)")
    else:
        add("database", bool(db_ok), "reachable" if db_ok else "unreachable")

    add(
        "workflow_presets",
        True,
        f"{len(list_workflow_presets())} admitted presets",
    )
    if delivery_review_seeded is None:
        add("delivery_review_capability", True, "not probed")
    else:
        add(
            "delivery_review_capability",
            bool(delivery_review_seeded),
            "seeded" if delivery_review_seeded else "missing",
        )
    if canary_percent is not None:
        add(
            "m6_canary",
            True,
            f"agentic canary percent={canary_percent}",
        )
    else:
        add("m6_canary", True, "percent not supplied")
    if worker_hint:
        add("worker", True, worker_hint[:200])
    else:
        add("worker", True, "hint not supplied (check process separately)")

    if host_summary is not None:
        unhealthy = bool(host_summary.get("unhealthy"))
        reasons = host_summary.get("reasons") or []
        res = host_summary.get("resources") or {}
        detail = (
            f"unhealthy={unhealthy} disk={res.get('disk_percent')}% "
            f"mem={res.get('mem_percent')}% load1={res.get('load1')} "
            f"venvs={res.get('preview_venv_count')} reasons={reasons[:3]}"
        )
        add("host_resources", not unhealthy, detail)
    else:
        add("host_resources", True, "not probed")

    settings = dict(settings_summary or {})
    # Never include secrets.
    redacted = {
        k: v
        for k, v in settings.items()
        if "key" not in k.lower() and "secret" not in k.lower() and "token" not in k.lower()
    }
    ok_all = all(bool(c["ok"]) for c in checks)
    return {
        "schema": DOCTOR_SCHEMA,
        "v": DOCTOR_VERSION,
        "at": utc_now_iso(),
        "ok": ok_all,
        "checks": checks,
        "settings_redacted": redacted,
        "host": host_summary,
    }
