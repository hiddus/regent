"""导出作品每章的追读自评、章纲和手段判定打回，看评审意见之后各章是否照做。

用法：python deploy/novel/arc_reviews.py <起始章> <work_id> [<work_id> ...]
输出：deploy/novel/artifacts/r3/arc_<首个 work_id 前 8 位>.txt
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ssh import Remote  # noqa: E402
from run_chapter1 import q  # noqa: E402

ART = Path(__file__).resolve().parent / "artifacts" / "r3"
P = "c.generation_context->'production'"


def dump(r: Remote, work_id: str, start: int) -> list[str]:
    lines = [f"===== {work_id}"]
    lines.append(q(
        r,
        "SELECT c.chapter_no || ' a' || c.attempt || ' ' || c.state "
        f"|| ' calls=' || coalesce({P}->>'call_count','0') "
        f"|| ' plans=' || coalesce({P}->>'plan_calls','0') "
        f"|| ' method=' || coalesce({P}->>'method_rounds','0') "
        f"|| ' stage=' || coalesce({P}->'chapter_plan'->>'stage_index','-') "
        f"|| ' reach=' || coalesce({P}->'chapter_plan'->>'reaches_stage','-') "
        f"|| ' show=' || coalesce({P}->'chapter_plan'->>'shows_rule','-') "
        f"|| E'\\n  review=' || coalesce(({P}->'arc_review')::text,'') "
        f"|| E'\\n  warn=' || coalesce(({P}->'plan_warnings')::text,'') "
        f"|| E'\\n  reject=' || coalesce({P}->>'plan_reject','') "
        f"|| E'\\n  choice=' || coalesce({P}->'chapter_plan'->>'choice','') "
        f"|| E'\\n  result=' || coalesce({P}->'chapter_plan'->>'result','') "
        f"FROM novel_chapter_runs c WHERE c.work_id='{work_id}' AND c.chapter_no >= {start} "
        "ORDER BY c.chapter_no, c.attempt",
    ) or "")
    lines.append(q(
        r,
        "SELECT string_agg(x->>'output', E'\\n---\\n') FROM novel_chapter_runs c, "
        f"jsonb_array_elements(({P}->'calls')::jsonb) x "
        f"WHERE c.work_id='{work_id}' AND c.chapter_no >= {start} "
        "AND x->>'purpose' = 'chapter_method' AND x->'output'->>'ignores_notes'='true'",
    ) or "")
    return lines


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: arc_reviews.py <start_chapter> <work_id> [<work_id> ...]")
    start = int(sys.argv[1])
    work_ids = sys.argv[2:]
    lines: list[str] = []
    with Remote() as r:
        for work_id in work_ids:
            lines.extend(dump(r, work_id, start))
    out = ART / f"arc_{work_ids[0][:8]}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out, out.stat().st_size)


if __name__ == "__main__":
    main()
