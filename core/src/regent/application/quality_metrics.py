"""User-facing quality metrics skeleton (PRD §10.5 / GQ-0).

GQ-3 reports must include these user outcomes, not only internal pass rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

QUALITY_METRICS_CONTRACT_VERSION = "gq-user-quality/v1"


@dataclass(frozen=True, slots=True)
class UserQualityMetrics:
    first_runnable: bool
    repair_rounds: int
    human_intervened: bool
    wall_time_to_usable_ms: float | None
    passed: bool


def aggregate_user_quality(rows: Sequence[UserQualityMetrics]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "first_runnable_rate": None,
            "mean_repair_rounds": None,
            "human_intervention_rate": None,
            "mean_wall_time_to_usable_ms": None,
            "status": "INSUFFICIENT_EVIDENCE",
        }
    usable_times = [
        r.wall_time_to_usable_ms
        for r in rows
        if r.wall_time_to_usable_ms is not None and r.passed
    ]
    return {
        "n": n,
        "first_runnable_rate": sum(1 for r in rows if r.first_runnable) / n,
        "mean_repair_rounds": sum(r.repair_rounds for r in rows) / n,
        "human_intervention_rate": sum(1 for r in rows if r.human_intervened) / n,
        "mean_wall_time_to_usable_ms": (
            (sum(usable_times) / len(usable_times)) if usable_times else None
        ),
        "status": "OK",
        "contract_version": QUALITY_METRICS_CONTRACT_VERSION,
    }
