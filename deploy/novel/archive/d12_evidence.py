"""看失败运行的引文到底长什么样，并用**仓库里的真实判据**复核。只读。

要回答的问题只有一个：模型是"引用时把输入字段名一起抄进去了"（可机械剥离），
还是"确实改写了正文"（那才是要模型改主意的事）。

用法：python deploy/novel/d12_evidence.py [work_id]
"""
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


# 诊断期先在脚本里试算，验证"是不是只多了个字段名前缀"再决定要不要改产品代码。
import re  # noqa: E402

_PREFIX = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_ ]{0,24}\s*[:：]\s*")


def strip_prefix(quote: str) -> str:
    out = _PREFIX.sub("", quote, count=1).strip()
    if len(out) >= 2 and out[0] in "'\"" and out[-1] == out[0]:
        out = out[1:-1].strip()
    return out


def nearest(quote: str, content: str) -> str:
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

    calls = production.get("calls") or []
    print(f"共 {len(calls)} 次调用；只看 purpose 含 WATCH/审阅 的导演判断\n")
    for call in calls:
        purpose = str(call.get("purpose") or "")
        if "WATCH" not in purpose and "审阅" not in purpose and "PROSE" not in purpose:
            continue
        out = call.get("output") or {}
        evidence = out.get("evidence") or []
        if not evidence:
            continue
        print(f"=== {purpose} action={out.get('action')} ===")
        for quote in evidence:
            print(f"  引用({len(quote)}字): {quote[:90]}")
        print()

    # 用正文复核最后一条 WATCH_PROSE 的引用
    stage = ""
    for take in production.get("takes") or []:
        if take.get("content"):
            stage = take["content"]
    if not stage:
        print("(没有正文)")
        return 0
    print(f"正文 {len(stage)} 字\n")
    last = None
    for call in calls:
        out = call.get("output") or {}
        if out.get("evidence") and "PROSE" in str(call.get("purpose") or ""):
            last = out
    if not last:
        print("(没有 WATCH_PROSE 判断)")
        return 0
    for quote in last["evidence"]:
        run = d._longest_common_run(quote, stage)
        need = min(len(quote), max(d.QUOTE_MIN_RUN, int(d.QUOTE_MIN_RATIO * len(quote))))
        print(f"原样判据：{run}/{need} -> {'OK' if run >= need else 'FAIL'}")
        stripped = strip_prefix(quote)
        if stripped != quote:
            run2 = d._longest_common_run(stripped, stage)
            need2 = min(
                len(stripped), max(d.QUOTE_MIN_RUN, int(d.QUOTE_MIN_RATIO * len(stripped)))
            )
            print(f"剥离字段名后：{run2}/{need2} -> {'OK' if run2 >= need2 else 'FAIL'}")
            print(f"  剥离后：{stripped[:90]}")
        print(f"  正文里最长逐字片段：{d._nearest_fragment(quote, stage)[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
