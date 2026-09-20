#!/usr/bin/env python3
"""协议 S vs X 同条件对照：固定同一份选定剧本，比较整章执笔与逐场演绎。

两步验证（Codex 方案 §六）：

1. 固定选定剧本：同 fragment 各跑 S/X，比较完成率、字数、硬失败、调用与费用。
2. 连续故事段：用修正后的连载脚本各跑 N 章，比较情节重复、钩子管理、事实来源。

默认 SimProvider 干跑；``--live`` 接真实模型。结果写入 out_dir 便于人工盲读。
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
_CORE_SRC = ROOT / "core" / "src"
if _CORE_SRC.exists() and str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

from regent.novel.experiments import quality_ab as qa  # noqa: E402


def _blind_id(result: qa.SampleResult, arm: str) -> str:
    import hashlib
    import uuid

    return hashlib.sha256(
        f"{result.fragment_id}:{arm}:{uuid.uuid4().hex}".encode()
    ).hexdigest()[:12]


async def run_pair(
    *,
    fragment_id: str,
    provider_factory,
    call_cap: int = 40,
    max_revisions: int = 1,
) -> dict:
    """同一 fragment：先生成固定制作包，再分别用 S/X 执笔，消除剧本差异。"""
    frag = qa.fragment_by_id(fragment_id)

    # 第一步：只生成固定制作包（编剧×2 → 选本 → 装配），不执笔、不核验
    meter_prep = qa.BudgetMeter(call_cap=call_cap)
    prov_prep = qa.MeteredProvider(provider_factory(), meter_prep)
    fixed_packet, prep_artifacts, prep_steps, prep_stop = await qa.build_production_packet(
        prov_prep, frag
    )
    if not fixed_packet:
        # 选本失败：无法固定同条件对照，不得退回两臂各自生成混入比较
        return {
            "fragment_id": fragment_id,
            "created_at": datetime.now(UTC).isoformat(),
            "fixed_packet_used": False,
            "prep_calls": meter_prep.calls,
            "prep_stop": prep_stop or "no_packet",
            "prep_artifacts": {
                "director_choice": prep_artifacts.get("director_choice"),
                "fault_taxonomy": prep_artifacts.get("fault_taxonomy"),
            },
            "S": {"completed": False, "stop_reason": "no_fixed_packet"},
            "X": {"completed": False, "stop_reason": "no_fixed_packet"},
            "skipped": True,
        }

    # 第二步：用同一 packet 分别执笔
    meter_s = qa.BudgetMeter(call_cap=call_cap)
    prov_s = qa.MeteredProvider(provider_factory(), meter_s)
    result_s = await qa.run_protocol_s(
        prov_s, frag, max_revisions=max_revisions, fixed_packet=fixed_packet
    )

    meter_x = qa.BudgetMeter(call_cap=call_cap)
    prov_x = qa.MeteredProvider(provider_factory(), meter_x)
    from regent.novel.experiments.scene_exec import run_protocol_x

    result_x = await run_protocol_x(
        prov_x, frag, max_revisions=max_revisions, fixed_packet=fixed_packet
    )

    def summarize(r: qa.SampleResult, arm: str) -> dict:
        arts = r.artifacts or {}
        return {
            "arm": arm,
            "blind_id": _blind_id(r, arm),
            "completed": r.completed,
            "calls_used": r.calls_used,
            "cost_minor": r.cost_minor,
            "hard_fail_count": r.hard_fail_count,
            "used_revision": r.used_revision,
            "stop_reason": r.stop_reason,
            "prose_chars": len(r.prose or ""),
            "facts_committed": r.facts_committed,
            "facts_status": arts.get("facts_status"),
            "facts_count": len(r.facts or []),
            "hooks_opened": list(arts.get("hooks_opened") or []),
            "hooks_closed": list(arts.get("hooks_closed") or []),
            "character_shifts": list(arts.get("character_shifts") or []),
            "scene_count": len((arts.get("scene_plan") or {}).get("cards") or [])
            if arm == "X"
            else 0,
            "fixed_packet": bool(arts.get("fixed_packet")),
            "steps": list(r.steps_log or []),
            "prose": r.prose or "",
        }

    return {
        "fragment_id": fragment_id,
        "created_at": datetime.now(UTC).isoformat(),
        "fixed_packet_used": fixed_packet is not None,
        "prep_calls": meter_prep.calls,
        "S": summarize(result_s, "S"),
        "X": summarize(result_x, "X"),
    }


async def run_batch(
    *,
    fragment_ids: list[str],
    live: bool,
    out_dir: Path,
) -> dict:
    def provider_factory():
        if live:
            os.environ["NOVEL_QUALITY_AB_LIVE"] = "1"
            from regent.config import get_settings
            from regent.model.factory import build_model_provider

            return build_model_provider(get_settings())
        return qa.SimProvider()

    import os

    pairs: list[dict] = []
    for fid in fragment_ids:
        print(f"running pair {fid} ...", flush=True)
        pair = await run_pair(
            fragment_id=fid,
            provider_factory=provider_factory,
        )
        if pair.get("skipped"):
            print(f"  SKIP {fid}: {pair.get('prep_stop')}", flush=True)
            pairs.append(pair)
            continue
        pairs.append(pair)
        s, x = pair["S"], pair["X"]
        print(
            f"  S: completed={s['completed']} chars={s['prose_chars']} "
            f"calls={s['calls_used']} facts={s['facts_status']}",
            flush=True,
        )
        print(
            f"  X: completed={x['completed']} chars={x['prose_chars']} "
            f"calls={x['calls_used']} facts={x['facts_status']} "
            f"scenes={x['scene_count']}",
            flush=True,
        )

    def agg(arm: str) -> dict:
        rows = [p[arm] for p in pairs if not p.get("skipped") and isinstance(p.get(arm), dict)]
        n = len(rows) or 1
        if not rows:
            return {"n": 0, "completion_rate": 0.0}
        return {
            "n": len(rows),
            "completion_rate": sum(1 for r in rows if r.get("completed")) / n,
            "avg_chars": sum(r.get("prose_chars") or 0 for r in rows) / n,
            "avg_calls": sum(r.get("calls_used") or 0 for r in rows) / n,
            "avg_cost": sum(r.get("cost_minor") or 0 for r in rows) / n,
            "hard_fail_rate": sum(1 for r in rows if r.get("hard_fail_count")) / n,
            "facts_committed_rate": sum(1 for r in rows if r.get("facts_committed")) / n,
            "avg_hooks_opened": sum(len(r.get("hooks_opened") or []) for r in rows) / n,
            "avg_hooks_closed": sum(len(r.get("hooks_closed") or []) for r in rows) / n,
        }

    summary = {
        "created_at": datetime.now(UTC).isoformat(),
        "live": live,
        "fragments": fragment_ids,
        "S": agg("S"),
        "X": agg("X"),
        "verdict_hint": (
            "比较 completion_rate / avg_chars / facts_committed_rate / hooks_closed。"
            "若 X 在相近费用下 completion 与 facts 更高、hooks 能关闭，说明演绎层有效。"
        ),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pairs.json").write_text(
        json.dumps(pairs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 盲读材料：去掉 arm 标签，只留 blind_id + prose
    blind = []
    for p in pairs:
        if p.get("skipped"):
            continue
        for arm in ("S", "X"):
            if not isinstance(p.get(arm), dict) or not p[arm].get("prose"):
                continue
            blind.append(
                {
                    "blind_id": p[arm].get("blind_id"),
                    "fragment_id": p["fragment_id"],
                    "prose": p[arm]["prose"],
                    "prose_chars": p[arm].get("prose_chars") or 0,
                }
            )
    import random

    random.shuffle(blind)
    (out_dir / "blind_read.json").write_text(
        json.dumps(blind, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fragments",
        nargs="+",
        default=["f1_appraisal", "f2_reborn", "f3_awaken", "f4_ent_gender"],
    )
    parser.add_argument("--live", action="store_true", default=False)
    parser.add_argument("--out", type=Path, default=Path("/tmp/sx_compare"))
    args = parser.parse_args(argv)
    if args.live:
        import os

        os.environ["NOVEL_QUALITY_AB_LIVE"] = "1"
    asyncio.run(
        run_batch(
            fragment_ids=args.fragments,
            live=args.live,
            out_dir=args.out,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
