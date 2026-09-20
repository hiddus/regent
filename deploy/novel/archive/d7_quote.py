"""取失败运行的 production，核对导演 evidence 与可引用原文。只读。

回答：``_quote_check`` 判死时，导演引的**原话**和场上**实际有的文本**差在哪。
"""

from __future__ import annotations

import json
import shlex
import sys

from _ssh import Remote

PSQL = 'docker exec -i regent-postgres psql -U regent -d regent -t -A -c'
WORK = sys.argv[1] if len(sys.argv) > 1 else ""


def q(r: Remote, sql: str) -> str:
    return r.run(f'{PSQL} {shlex.quote(" ".join(sql.split()))}', timeout=180).out


def main() -> int:
    r = Remote()
    where = f"WHERE work_id = '{WORK}'" if WORK else ""
    raw = q(
        r,
        f"""
        SELECT left(work_id::text,8) || '|' || state || '|' || generation_context::text
        FROM novel_chapter_runs {where}
        ORDER BY created_at DESC LIMIT 1
        """,
    ).strip()
    if not raw:
        print("(查不到运行)")
        return 1
    head, _, payload = raw.partition("|")
    state, _, payload = payload.partition("|")
    print(f"作品 {head}  状态 {state}")
    ctx = json.loads(payload)
    production = ctx.get("production") or {}
    take = (production.get("takes") or [{}])[-1]
    available = "\n".join(
        [e.get("statement", "") for e in take.get("events", [])]
        + [
            line
            for a in take.get("performances", [])
            for line in list(a.get("actions") or []) + list(a.get("dialogue") or [])
        ]
        + [str(i) for i in take.get("rule_issues") or []]
    )
    print(f"\n=== rule_issues（{len(take.get('rule_issues') or [])} 条）===")
    for i in take.get("rule_issues") or []:
        print("  -", i)
    print(f"\n=== 事件数 {len(take.get('events', []))}；表演数 {len(take.get('performances', []))} ===")
    print(f"\n=== 场上可引用原文（{len(available)} 字）===")
    print(available[:1500] or "(空)")

    print("\n=== 各次调用的 evidence 是否逐字命中 ===")
    for call in production.get("calls", []):
        out = call.get("output") or {}
        evidence = out.get("evidence")
        if not isinstance(evidence, list):
            continue
        for quote in evidence:
            hit = bool(quote) and quote in available
            print(f"  [{call.get('purpose')}] {'OK ' if hit else '✗'} {quote!r}")
        print(f"    action={out.get('action')} observation={(out.get('observation') or '')[:60]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
