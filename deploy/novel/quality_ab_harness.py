#!/usr/bin/env python3
"""质量对照实验 A/B/C 入口。

默认干跑（清单+空白评分表）。``--sim`` 用假模型贯通 9 样本，不付费。
实跑必须 ``--live`` 且 ``NOVEL_QUALITY_AB_LIVE=1``。

方案 C 只存在于实验协议，不进入生产执行器。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

_HERE = Path(__file__).resolve()
# 仓库内：deploy/novel/...；容器内可能落在 /tmp/...
ROOT = next(
    (
        p
        for p in [_HERE.parents[i] for i in range(min(4, len(_HERE.parents)))]
        if (p / "core" / "src" / "regent").exists()
    ),
    Path("/tmp"),
)
_CORE_SRC = ROOT / "core" / "src"
if _CORE_SRC.exists() and str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

from regent.novel.experiments import quality_ab as qa  # noqa: E402

DEFAULT_OUT = ROOT / "deploy" / "novel" / "artifacts" / "quality_ab"
if not DEFAULT_OUT.parent.exists():
    DEFAULT_OUT = Path("/tmp/quality_ab_out")


def write_dry_artifacts(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    plans = qa.build_matrix()
    matrix = [asdict(row) for row in plans]
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "dry-run",
        "call_cap_per_sample": qa.CALL_CAP_PER_SAMPLE,
        "max_revisions": qa.MAX_REVISIONS,
        "fragments": list(qa.FRAGMENTS),
        "protocols": qa.PROTOCOLS,
        "samples": matrix,
        "stop_conditions": [
            "per-sample call/cost cap",
            "no-progress rewrite stop (production path)",
            "do not replace failed runs to inflate success rate",
            "blind scores only on four axes",
        ],
        "verdict_rule": (
            "If B does not stably beat A on want_to_continue and total blind score "
            "under similar budget, shrink director to scene design + key decisions."
        ),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_blank_score_sheet(out_dir / "blind_score_sheet.csv", plans)
    print(f"wrote {out_dir / 'manifest.json'}")
    print(f"wrote {out_dir / 'blind_score_sheet.csv'}")
    print(f"samples={len(matrix)} protocols=A,B,C fragments={len(qa.FRAGMENTS)}")


def _write_blank_score_sheet(score_path: Path, plans: list[qa.SamplePlan]) -> None:
    with score_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "sample_id",
                "fragment_id",
                "protocol_hidden",
                *qa.BLIND_AXES,
                "notes",
                "calls_used",
                "cost_minor",
                "completed",
                "hard_fail_count",
                "used_revision",
            ],
        )
        writer.writeheader()
        for row in plans:
            writer.writerow(
                {
                    "sample_id": f"{row.fragment_id}:{row.protocol}",
                    "fragment_id": row.fragment_id,
                    "protocol_hidden": "",
                    **{axis: "" for axis in qa.BLIND_AXES},
                    "notes": "",
                    "calls_used": "",
                    "cost_minor": "",
                    "completed": "",
                    "hard_fail_count": "",
                    "used_revision": "",
                }
            )


def write_run_artifacts(out_dir: Path, *, mode: str, results: list[qa.SampleResult]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    blind_dir = out_dir / "blind"
    blind_dir.mkdir(exist_ok=True)

    full = [qa.result_public_dict(r) for r in results]
    key_map = {
        r.blind_id: {
            "protocol": r.protocol,
            "fragment_id": r.fragment_id,
            "sample_id": f"{r.fragment_id}:{r.protocol}",
        }
        for r in results
    }
    summary = qa.summarize_results(results)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "call_cap_per_sample": qa.CALL_CAP_PER_SAMPLE,
        "max_revisions": qa.MAX_REVISIONS,
        "fragments": list(qa.FRAGMENTS),
        "protocols": qa.PROTOCOLS,
        "summary": summary,
        "sample_count": len(results),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "results.json").write_text(
        json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "key_map.json").write_text(
        json.dumps(key_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    score_path = out_dir / "blind_score_sheet.csv"
    with score_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "blind_id",
                "fragment_id",
                *qa.BLIND_AXES,
                "notes",
                "calls_used",
                "cost_minor",
                "completed",
                "hard_fail_count",
                "used_revision",
                "stop_reason",
            ],
        )
        writer.writeheader()
        for row in results:
            (blind_dir / f"{row.blind_id}.txt").write_text(row.prose, encoding="utf-8")
            writer.writerow(
                {
                    "blind_id": row.blind_id,
                    "fragment_id": row.fragment_id,
                    **{axis: "" for axis in qa.BLIND_AXES},
                    "notes": "",
                    "calls_used": row.calls_used,
                    "cost_minor": row.cost_minor,
                    "completed": int(row.completed),
                    "hard_fail_count": row.hard_fail_count,
                    "used_revision": int(row.used_revision),
                    "stop_reason": row.stop_reason,
                }
            )

    print(f"wrote {out_dir / 'manifest.json'}")
    print(f"wrote {out_dir / 'results.json'}")
    print(f"wrote {out_dir / 'key_map.json'} (评委勿看)")
    print(f"wrote {score_path}")
    print(f"wrote {blind_dir}/<blind_id>.txt")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


async def run_batch(
    *,
    provider: qa.ModelProvider,
    protocols: tuple[str, ...] | None,
    fragments: tuple[str, ...] | None,
) -> list[qa.SampleResult]:
    plans = qa.build_matrix(protocols=protocols, fragments=fragments)
    results: list[qa.SampleResult] = []
    for plan in plans:
        print(f"running {plan.fragment_id}:{plan.protocol} ...", flush=True)
        result = await qa.run_sample(
            protocol=plan.protocol,
            fragment_id=plan.fragment_id,
            provider=provider,
            call_cap=plan.call_cap,
            max_revisions=plan.max_revisions,
        )
        print(
            f"  -> completed={result.completed} calls={result.calls_used} "
            f"hard_fails={result.hard_fail_count} stop={result.stop_reason}",
            flush=True,
        )
        results.append(result)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--sim", action="store_true", default=False, help="假模型贯通，不付费")
    parser.add_argument("--live", action="store_true", default=False)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--protocol",
        action="append",
        choices=list(qa.PROTOCOLS),
        help="可重复；默认 A,B,C",
    )
    parser.add_argument(
        "--fragment",
        action="append",
        choices=[f["id"] for f in qa.FRAGMENTS],
        help="可重复；默认全部片段",
    )
    args = parser.parse_args(argv)
    protocols = tuple(args.protocol) if args.protocol else None
    fragments = tuple(args.fragment) if args.fragment else None

    modes = [m for m in ("dry-run" if args.dry_run else None, "sim" if args.sim else None, "live" if args.live else None) if m]
    if len(modes) > 1:
        print("请只选一种模式：默认干跑 / --sim / --live", file=sys.stderr)
        return 2

    if args.live:
        if os.environ.get("NOVEL_QUALITY_AB_LIVE") != "1":
            print(
                "拒绝实跑：需要 --live 且 NOVEL_QUALITY_AB_LIVE=1。",
                file=sys.stderr,
            )
            return 2
        from regent.config import get_settings
        from regent.model.factory import build_model_provider

        provider = build_model_provider(get_settings())
        results = asyncio.run(
            run_batch(provider=provider, protocols=protocols, fragments=fragments)
        )
        write_run_artifacts(args.out, mode="live", results=results)
        return 0

    if args.sim:
        results = asyncio.run(
            run_batch(
                provider=qa.SimProvider(),  # type: ignore[arg-type]
                protocols=protocols,
                fragments=fragments,
            )
        )
        write_run_artifacts(args.out, mode="sim", results=results)
        return 0

    write_dry_artifacts(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
