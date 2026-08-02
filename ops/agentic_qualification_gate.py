"""Qualification ladder gate helpers for ops promotion (adjacent + report).

Documents the ladder; does not auto-flip Settings. Used by
``ops/set_agentic_qualification.py``.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

LADDER: tuple[str, ...] = (
    "DISABLED",
    "OFFLINE_QUALIFICATION",
    "INTERNAL_DOGFOOD",
    "CANARY_5",
    "CANARY_25",
    "CANARY_50",
    "DEFAULT",
)

# Upgrades to these require a report whose gate.allows_state ranks >= target.
REPORT_REQUIRED_FROM = "OFFLINE_QUALIFICATION"

# Dogfood+ must prove live-model REVISE/V2 — fixture Preview alone is insufficient.
LIVE_V2_REQUIRED_FROM = "INTERNAL_DOGFOOD"

DEFAULT_MAX_AGE = timedelta(hours=72)


def ladder_index(state: str) -> int:
    try:
        return LADDER.index(state)
    except ValueError as exc:
        raise ValueError(f"unknown qualification state: {state}") from exc


def is_adjacent_or_downgrade(*, current: str, target: str) -> bool:
    """Allow one-step upgrade or any downgrade (including multi-step)."""
    cur_i = ladder_index(current)
    tgt_i = ladder_index(target)
    if tgt_i <= cur_i:
        return True
    return tgt_i == cur_i + 1


def load_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("qualification report must be a JSON object")
    return data


def report_created_at(report: dict[str, Any]) -> datetime:
    raw = str(report.get("created_at") or "").strip()
    if not raw:
        raise ValueError("report missing created_at")
    # Support trailing Z.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def report_is_fresh(
    report: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_MAX_AGE,
) -> bool:
    now = now or datetime.now(UTC)
    created = report_created_at(report)
    return (now - created) <= max_age


def report_allows_state(report: dict[str, Any]) -> str:
    gate = dict(report.get("gate") or {})
    return str(gate.get("allows_state") or "DISABLED")


def report_has_live_model_v2(report: dict[str, Any]) -> bool:
    """True when report proves live-model agentic + REVISE/V2 (not fixture-only)."""
    gate = dict(report.get("gate") or {})
    if gate.get("live_model_v2_green") is True:
        return True
    for check in report.get("checks") or []:
        if not isinstance(check, dict):
            continue
        cid = str(check.get("id") or "")
        if cid in {
            "live_model_agentic_golden",
            "agentic_revise_v2",
            "live_model_revise_v2",
        } and check.get("ok") is True:
            return True
    return False


def find_latest_report(docs_dir: Path) -> Path | None:
    patterns = (
        "agentic-offline-qual-report-*.json",
        "agentic-live-golden-report-*.json",
    )
    candidates: list[Path] = []
    for pat in patterns:
        candidates.extend(docs_dir.glob(pat))
    if not candidates:
        return None
    # Prefer reports that unlock the highest allows_state, then newest mtime.
    def _rank(path: Path) -> tuple[int, float]:
        try:
            data = load_report(path)
            allows = report_allows_state(data)
            return (ladder_index(allows), path.stat().st_mtime)
        except Exception:
            return (-1, path.stat().st_mtime)

    return max(candidates, key=_rank)


def validate_sample_gates(report: dict[str, Any], target: str) -> list[str]:
    """Q3 hook: CANARY_* upgrades need explicit sample_gates in the report."""
    errors: list[str] = []
    if ladder_index(target) < ladder_index("CANARY_5"):
        return errors
    gates = dict(report.get("sample_gates") or {})
    if target == "CANARY_5":
        n = int(gates.get("dogfood_independent_tasks") or 0)
        if n < 20:
            errors.append(
                f"sample_gates.dogfood_independent_tasks={n} < 20 "
                f"(required for CANARY_5; omit --force only with real Dogfood samples)"
            )
        if "infra_false_fail_count" not in gates:
            errors.append("sample_gates.infra_false_fail_count missing (must be 0 for CANARY_5)")
        else:
            try:
                infra_n = int(gates["infra_false_fail_count"])
            except (TypeError, ValueError):
                infra_n = -1
            if infra_n != 0:
                errors.append("sample_gates.infra_false_fail_count must be 0 for CANARY_5")
    if ladder_index(target) >= ladder_index("CANARY_25"):
        n = int(gates.get("independent_goals") or 0)
        if n < 40:
            errors.append(
                f"sample_gates.independent_goals={n} < 40 (required for {target})"
            )
    return errors


def validate_promotion(
    *,
    current: str,
    target: str,
    report: dict[str, Any] | None,
    force: bool = False,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_MAX_AGE,
) -> list[str]:
    """Return list of blocking errors (empty = allowed)."""
    errors: list[str] = []
    if current == target:
        return errors
    if not is_adjacent_or_downgrade(current=current, target=target):
        errors.append(
            f"non-adjacent upgrade forbidden: {current} → {target} "
            f"(only one step up, or any downgrade)"
        )
    # Downgrades need no report (adjacent check already allows any lower).
    if ladder_index(target) <= ladder_index(current):
        return errors
    # Upgrades from here.
    if force:
        return []  # break-glass; caller must warn
    if ladder_index(target) >= ladder_index(REPORT_REQUIRED_FROM):
        if report is None:
            errors.append(
                f"upgrade to {target} requires a fresh AgenticOfflineQualificationReport "
                f"(pass --report or place docs/agentic-offline-qual-report-*.json)"
            )
            return errors
        if not report_is_fresh(report, now=now, max_age=max_age):
            errors.append(
                f"report stale: created_at={report.get('created_at')} "
                f"max_age_hours={max_age.total_seconds() / 3600:.0f}"
            )
        allows = report_allows_state(report)
        if ladder_index(allows) < ladder_index(target):
            errors.append(
                f"report gate.allows_state={allows!r} does not cover target={target!r}"
            )
        if ladder_index(target) >= ladder_index(LIVE_V2_REQUIRED_FROM):
            if not report_has_live_model_v2(report):
                errors.append(
                    "INTERNAL_DOGFOOD+ requires live-model Agentic + REVISE/V2 evidence "
                    "in the report (fixture Preview alone is insufficient; "
                    "see decision-note §4)"
                )
        errors.extend(validate_sample_gates(report, target))
    return errors
