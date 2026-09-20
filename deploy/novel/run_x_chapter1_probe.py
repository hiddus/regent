#!/usr/bin/env python3
"""协议 X 短样本：只跑第 1 章，禁止当成长篇连载。

用法（容器内）：
  NOVEL_QUALITY_AB_LIVE=1 python /tmp/run_x_chapter1_probe.py --out /tmp/x_ch1_probe
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

_HERE = Path(__file__).resolve()
ROOT = next(
    (
        p
        for p in [_HERE.parents[i] for i in range(min(4, len(_HERE.parents)))]
        if (p / "core" / "src" / "regent").exists()
    ),
    Path("/tmp"),
)
_CORE = ROOT / "core" / "src"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from regent.novel.experiments import quality_ab as qa  # noqa: E402
from regent.novel.experiments.scene_exec import run_protocol_x  # noqa: E402


def _provider():
    from regent.config import get_settings
    from regent.model.factory import build_model_provider

    return build_model_provider(get_settings())


async def main_async(*, fragment_id: str, out: Path) -> dict:
    frag = qa.fragment_by_id(fragment_id)
    meter = qa.BudgetMeter(call_cap=max(qa.CALL_CAP_PER_SAMPLE, 40))
    provider = qa.MeteredProvider(_provider(), meter)
    result = await run_protocol_x(provider, frag, max_revisions=2)
    prose = (result.prose or "").strip()
    artifacts = result.artifacts or {}
    hard_fails = list(artifacts.get("scene_hard_fails") or artifacts.get("hard_fails") or [])
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "protocol": "X",
        "fragment_id": fragment_id,
        "completed": result.completed,
        "stop_reason": result.stop_reason,
        "prose_chars": len(prose),
        "calls_used": result.calls_used,
        "cost_minor": result.cost_minor,
        "hard_fail_count": result.hard_fail_count,
        "hard_fails": hard_fails,
        "scene_hard_fails": artifacts.get("scene_hard_fails"),
        "soft_evidence_warns": artifacts.get("soft_evidence_warns"),
        "scene_audits": artifacts.get("scene_audits"),
        "scene_plan": artifacts.get("scene_plan"),
        "selected_id": artifacts.get("selected_id"),
        "steps_log": list(result.steps_log or []),
        "prose": prose,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(
        json.dumps({k: v for k, v in report.items() if k != "prose"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "chapter1.txt").write_text(prose, encoding="utf-8")
    (out / "report_full.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in (
        "completed", "stop_reason", "prose_chars", "calls_used", "cost_minor",
        "hard_fail_count", "hard_fails"
    )}, ensure_ascii=False, indent=2), flush=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fragment", default="f4_ent_gender")
    parser.add_argument("--out", type=Path, default=Path("/tmp/x_ch1_probe"))
    args = parser.parse_args()
    if os.environ.get("NOVEL_QUALITY_AB_LIVE") != "1":
        print("需要 NOVEL_QUALITY_AB_LIVE=1", file=sys.stderr)
        return 2
    asyncio.run(main_async(fragment_id=args.fragment, out=args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
