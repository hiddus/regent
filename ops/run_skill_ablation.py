"""Skill on/off ablation report (W4-P1-3 / M5-4 engineering gate).

Usage:
  python -B ops/run_skill_ablation.py
  python -B ops/run_skill_ablation.py --out docs/skill-ablation-report-2026-08-02.json

Measures routing non-empty rate on Chinese validation goals (on) vs disabled (off).
Does not claim statistical significance — engineering_gate_only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "src"))

from regent.agent.skills import select_skills_for_goal, skill_ablation_report  # noqa: E402

GOALS_MD = ROOT / "regent_validation_goals.md"


def _load_chinese_goals(limit: int = 20) -> list[str]:
    if not GOALS_MD.is_file():
        return [
            "中国历史人物全集网站",
            "城市噪音地图应用",
            "待办笔记 crud 系统",
            "开放数据上传与检索平台",
            "本地生活服务黄页",
        ][:limit]
    text = GOALS_MD.read_text(encoding="utf-8")
    goals: list[str] = []
    for m in re.finditer(r"^\d+\.\s+\*\*(.+?)\*\*", text, flags=re.M):
        goals.append(m.group(1).strip())
        if len(goals) >= limit:
            break
    return goals


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    goals = _load_chinese_goals(args.limit)
    on_hits = 0
    off_hits = 0
    details: list[dict] = []
    for g in goals:
        on = select_skills_for_goal(g, enabled=True)
        off = select_skills_for_goal(g, enabled=False)
        if on:
            on_hits += 1
        if off:
            off_hits += 1
        details.append(
            {
                "goal": g,
                "on_skills": [s.skill_id for s in on],
                "off_skills": [s.skill_id for s in off],
            }
        )
    total = len(goals) or 1
    # Treat "routing non-empty" as proxy pass for engineering gate (not end-to-end success).
    ablation = skill_ablation_report(
        on_pass=on_hits,
        on_total=total,
        off_pass=off_hits,
        off_total=total,
    )
    report = {
        "record_type": "SkillAblationReport",
        "schema_version": "skill-ablation/v1",
        "created_at": datetime.now(UTC).isoformat(),
        "n_goals": total,
        "on_nonempty_rate": on_hits / total,
        "off_nonempty_rate": off_hits / total,
        "target_nonempty_rate": 0.70,
        "meets_w4_chinese_routing": (on_hits / total) >= 0.70,
        "ablation": ablation,
        "details": details,
        "explicit_non_claims": [
            "Not M5 exit gate (≥90% accuracy) — routing non-empty proxy only",
            "Not end-to-end Goal success ablation",
        ],
    }
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    out = args.out or (ROOT / "docs" / f"skill-ablation-report-{day}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "out": str(out),
                "on_nonempty_rate": report["on_nonempty_rate"],
                "meets_w4_chinese_routing": report["meets_w4_chinese_routing"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["meets_w4_chinese_routing"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
