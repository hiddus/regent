#!/usr/bin/env python3
"""缓存前缀对照：不发付费请求，测量同场重试/跨场/跨章的公共前缀与计费口径。

用法：
  PYTHONPATH=core/src python deploy/novel/run_cache_prefix_probe.py
  PYTHONPATH=core/src python deploy/novel/run_cache_prefix_probe.py --out deploy/novel/artifacts/cache_prefix_probe.json
"""

from __future__ import annotations

import argparse
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
    Path("."),
)
_CORE = ROOT / "core" / "src"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from regent.novel.domain.price_book import cache_usage_report  # noqa: E402
from regent.novel.domain.scene_card import SCENE_WRITE_SYSTEM  # noqa: E402
from regent.novel.domain.script_protocol import (  # noqa: E402
    DIRECTOR_SELECT_CONTRACT,
    SCRIPT_DIVERSITY_CONTRACT,
    WEB_NOVEL_POWER_CONTRACT,
)


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_cases() -> list[dict]:
    fixed_system = SCENE_WRITE_SYSTEM
    script_system = WEB_NOVEL_POWER_CONTRACT + SCRIPT_DIVERSITY_CONTRACT
    base_ctx = {
        "work_rules": "金手指规则固定：文抄公只能复现已播爆款",
        "world_rules": "都市文娱恋综",
        "canon": [{"statement": "沈星野已性转入组"}],
        "story_ledger_block": "【故事段】推进既有代价后果",
    }
    s1 = {
        "context": base_ctx,
        "scene_card": {"scene_id": "s1", "purpose": "试探"},
        "working_state": {"trust": "低"},
        "target_chars": 700,
    }
    s1_retry = {
        **s1,
        "previous_draft": "旧稿一段",
        "revision_instruction": "补齐未落地节拍",
        "is_revision": True,
    }
    s2 = {
        "context": base_ctx,
        "scene_card": {"scene_id": "s2", "purpose": "反杀"},
        "working_state": {"trust": "中"},
        "target_chars": 800,
    }
    ch2_ctx = {
        **base_ctx,
        "chapter_no": 2,
        "story_ledger_block": "【故事段】第2章：让曝光代价落地",
    }
    s1_ch2 = {
        "context": ch2_ctx,
        "scene_card": {"scene_id": "s1", "purpose": "压迫"},
        "working_state": {},
        "target_chars": 700,
    }

    def pack(system: str, payload: dict) -> str:
        return system + "\n" + _dump(payload)

    pairs = [
        ("same_scene_retry", pack(fixed_system, s1), pack(fixed_system, s1_retry)),
        ("cross_scene", pack(fixed_system, s1), pack(fixed_system, s2)),
        ("cross_chapter", pack(fixed_system, s1), pack(fixed_system, s1_ch2)),
        (
            "script_alpha_beta",
            pack(script_system, {"route_role": "alpha", "route_task": "甲"}),
            pack(script_system, {"route_role": "beta", "route_task": "乙"}),
        ),
        (
            "director_select_stable",
            DIRECTOR_SELECT_CONTRACT + "\n" + _dump({"candidates": {"a": 1}}),
            DIRECTOR_SELECT_CONTRACT + "\n" + _dump({"candidates": {"b": 2}}),
        ),
    ]
    rows = []
    for name, left, right in pairs:
        pref = _common_prefix_len(left, right)
        rows.append(
            {
                "case": name,
                "left_chars": len(left),
                "right_chars": len(right),
                "common_prefix_chars": pref,
                "prefix_ratio_vs_min": round(pref / max(1, min(len(left), len(right))), 4),
                # 假设前缀全部命中缓存时的计费口径（非真实供应商命中）
                "billing_if_prefix_cached": cache_usage_report(
                    "deepseek-chat",
                    input_tokens=max(len(left), len(right)),
                    cached_input_tokens=pref,
                    output_tokens=200,
                ),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    rows = build_cases()
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "note": "公共前缀长度是缓存友好性的必要条件，不是真实命中证明。",
        "cases": rows,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
