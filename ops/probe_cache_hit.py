"""Probe within-run cache hit rate from AgentRunLedger sidecars (W4-P0-2).

Usage:
  python -B ops/probe_cache_hit.py
  python -B ops/probe_cache_hit.py --root data/workspaces --out docs/cache-hit-probe-2026-08-02.json

Scans ``.regent_run_ledger.json`` files and prints median cache_hit_rate.
Does not claim P0/P1 cost gates passed — only makes the number observable.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_ledgers(root: Path) -> list[dict]:
    found: list[dict] = []
    if not root.is_dir():
        return found
    for path in root.rglob(".regent_run_ledger.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        inp = int(data.get("input_tokens") or 0)
        cached = int(data.get("cached_tokens") or 0)
        rate = (cached / inp) if inp > 0 else None
        found.append(
            {
                "path": str(path),
                "input_tokens": inp,
                "cached_tokens": cached,
                "cache_hit_rate": rate,
                "turns": data.get("turns"),
            }
        )
    return found


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--root",
        type=Path,
        default=ROOT / "data" / "workspaces",
        help="Workspace root to scan for .regent_run_ledger.json",
    )
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    ledgers = _load_ledgers(args.root)
    rates = [float(x["cache_hit_rate"]) for x in ledgers if x.get("cache_hit_rate") is not None]
    report = {
        "record_type": "CacheHitProbeReport",
        "schema_version": "cache-hit-probe/v1",
        "created_at": datetime.now(UTC).isoformat(),
        "root": str(args.root),
        "n_ledgers": len(ledgers),
        "n_with_rate": len(rates),
        "median_cache_hit_rate": _median(rates),
        "mean_cache_hit_rate": (sum(rates) / len(rates)) if rates else None,
        "p0_guardrail_target": 0.40,
        "p1_guardrail_target": 0.60,
        "meets_p0_target": (
            (_median(rates) is not None and _median(rates) >= 0.40) if rates else None
        ),
        "claim": "observable_only — do not treat missing ledgers as pass",
        "samples": ledgers[:50],
    }
    out = args.out or (
        ROOT / "docs" / f"cache-hit-probe-{datetime.now(UTC).strftime('%Y-%m-%d')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "out": str(out),
                "n_ledgers": report["n_ledgers"],
                "median_cache_hit_rate": report["median_cache_hit_rate"],
                "meets_p0_target": report["meets_p0_target"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
