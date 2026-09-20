"""看失败运行的"硬失败"到底由什么构成，并用**仓库里的真实判据**复核。只读。"""

from __future__ import annotations

import json
import shlex
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from _ssh import Remote  # noqa: E402
from regent.novel.application import direction as d  # noqa: E402

PSQL = 'docker exec -i regent-postgres psql -U regent -d regent -t -A -c'
WORK = sys.argv[1] if len(sys.argv) > 1 else ""


def q(r: Remote, sql: str) -> str:
    return r.run(f'{PSQL} {shlex.quote(" ".join(sql.split()))}', timeout=180).out


def _nearest(quote: str, content: str) -> str:
    """给出引文里最长的一段逐字片段，便于肉眼判断是"改写了"还是"凭空写"。"""
    for size in range(len(quote), 5, -1):
        for start in range(0, len(quote) - size + 1):
            frag = quote[start : start + size]
            if frag in content:
                return frag
    return "(无 6 字以上重合)"


def main() -> int:
    r = Remote()
    where = f"WHERE work_id = '{WORK}'" if WORK else ""
    raw = q(
        r,
        f"""
        SELECT generation_context::text FROM novel_chapter_runs {where}
        ORDER BY created_at DESC LIMIT 1
        """,
    ).strip()
    if not raw:
        print("(查不到)")
        return 1
    ctx = json.loads(raw)
    production = ctx.get("production") or {}
    takes = production.get("takes") or []
    for i, take in enumerate(takes):
        val = take.get("validation") or {}
        print(f"--- take[{i}] passed={val.get('passed')} issues={val.get('issues')}")

    content = (takes[-1] if takes else {}).get("content", "")
    print(f"\n当前 take 正文 {len(content)} 字")
    for call in production.get("calls", []):
        out = call.get("output") or {}
        if "facts" not in out:
            continue
        print(f"\n=== 核验调用 {call.get('purpose')} 模型自评 passed={out.get('passed')}")
        for fact in out.get("facts") or []:
            quote = fact.get("quote", "")
            if d._is_grounded(quote, content):
                print(f"  OK  {quote[:45]}")
            else:
                run = d._longest_common_run(quote.strip(), content)
                need = min(
                    len(quote.strip()),
                    max(d.QUOTE_MIN_RUN, int(d.QUOTE_MIN_RATIO * len(quote.strip()))),
                )
                print(f"  ✗   {quote[:45]}")
                print(f"      重合 {run}/{need}；最长逐字段落：{_nearest(quote.strip(), content)}")
        for change in out.get("state_changes") or []:
            quote = change.get("quote", "")
            if not d._is_grounded(quote, content):
                print(f"  ✗   state_change[{change.get('key')}] {quote[:45]}")
                print(f"      最长逐字段落：{_nearest(quote.strip(), content)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
