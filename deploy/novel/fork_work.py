"""把一本书在当前进度完整复制成一本新书，新书落在指定的执行器臂：同书对照用。

两个臂各写各的书时，书与书之间的差别盖过了臂的差别。复制后原书和副本从同一章、
同一份正史、记忆、规划台账出发，各用一个臂往下写，差别才归得到臂上。

复制范围：作品、目标、开书问答、人物与信息集、卷与弧节点、关键路径与节点、长期记忆、
已入正史的章节运行及其步骤与决策请求、正史提交、事件流。不复制模型调用、成本、配额、
导出、分享、审核、H2 改进库：副本从零记账，H2 副本没有本书覆盖层，从跨书经验起步。

所有行的主键（事件取 event_id）换成新 id，行内（含 JSON）引用到这些 id 的地方一起换；
新作品 id 反复抽签，直到 ``choose_executor`` 把它分进要的臂。整次复制在一个事务里。
先收 id 再逐行流式复制，不把整本书的生成上下文一次读进内存（服务器只有 1.6GB）。

用法：python deploy/novel/fork_work.py <源作品 id> <director_chapter|director_chapter_h2> [--dry-run]
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ssh import Remote  # noqa: E402

REMOTE_SCRIPT = r'''
import json, os, re, sys, uuid
import psycopg
from regent.novel.application import executor as ex

source, want, dry = sys.argv[1], sys.argv[2], "--dry-run" in sys.argv
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
RUNS = "SELECT id FROM novel_chapter_runs WHERE work_id = %(w)s AND chapter_no <= %(latest)s"
PLAN = [
    ("novel_works", "id = %(w)s"),
    ("novel_work_sequences", "work_id = %(w)s"),
    ("novel_goals", "work_id = %(w)s"),
    ("novel_onboarding_sessions", "work_id = %(w)s"),
    ("novel_personas", "work_id = %(w)s"),
    ("novel_information_sets", "work_id = %(w)s"),
    ("novel_volumes", "work_id = %(w)s"),
    ("novel_arc_nodes", "volume_id IN (SELECT id FROM novel_volumes WHERE work_id = %(w)s)"),
    ("novel_critical_paths", "work_id = %(w)s"),
    ("novel_critical_nodes", "path_id IN (SELECT id FROM novel_critical_paths WHERE work_id = %(w)s)"),
    ("novel_memory_items", "work_id = %(w)s"),
    ("novel_memory_edges", "work_id = %(w)s"),
    ("novel_chapter_runs", "work_id = %(w)s AND chapter_no <= %(latest)s"),
    ("novel_chapter_steps", f"run_id IN ({RUNS})"),
    ("novel_decision_requests", f"work_id = %(w)s AND (run_id IS NULL OR run_id IN ({RUNS}))"),
    ("novel_canon_commits", "work_id = %(w)s AND chapter_no <= %(latest)s"),
    ("novel_events", "work_id = %(w)s"),
]
SEQUENCE_ID = {"novel_events"}
# 除了 id 之外换新的键：事件按 event_id 去重；作品计数器的主键就是 work_id
KEYS = {"novel_events": "event_id", "novel_work_sequences": "work_id"}

url = os.environ["REGENT_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
with psycopg.connect(url) as conn:
    latest = conn.execute("SELECT latest_chapter_no FROM novel_works WHERE id = %s", (source,)).fetchone()[0]
    params = {"w": source, "latest": latest}
    while True:
        new_work = str(uuid.uuid4())
        if ex.choose_executor(new_work) == want:
            break
    mapping = {source: new_work}
    counts = {}
    for table, where in PLAN:
        key = KEYS.get(table, "id")
        rows = conn.execute(f"SELECT {key}::text FROM {table} WHERE {where}", params).fetchall()
        counts[table] = len(rows)
        for (old,) in rows:
            if old in mapping:
                continue
            mapping[old] = str(uuid.uuid4()) if UUID.fullmatch(old or "") else f"{old}:{new_work[:8]}"
    swap = lambda text: UUID.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)
    with conn.transaction():
        for table, where in PLAN:
            with conn.cursor(name=f"fork_{table}") as cur:
                cur.itersize = 1
                cur.execute(f"SELECT row_to_json(t)::text FROM {table} t WHERE {where}", params)
                for (text,) in cur:
                    row = json.loads(swap(text))
                    if table in SEQUENCE_ID:
                        row.pop("id", None)
                        row["event_id"] = mapping.get(json.loads(text)["event_id"], row["event_id"])
                    if table == "novel_works":
                        row["title"] = (row.get("title") or "") + "·对照"
                    cols = ", ".join(f'"{c}"' for c in row)
                    conn.execute(
                        f"INSERT INTO {table} ({cols}) SELECT {cols} FROM json_populate_record(NULL::{table}, %s::json)",
                        (json.dumps(row, ensure_ascii=False),),
                    )
        if dry:
            raise psycopg.Rollback()
    arm = ex.choose_executor(new_work)
    print(json.dumps({"source": source, "fork": new_work, "arm": arm, "latest": latest, "dry_run": dry, "rows": counts}, ensure_ascii=False))
'''


def fork(source: str, arm: str, *, dry_run: bool = False) -> dict:
    with Remote() as r:
        r.write_text("/opt/regent/sync/fork_remote.py", REMOTE_SCRIPT)
        r.run("docker cp /opt/regent/sync/fork_remote.py regent-api:/tmp/fork_remote.py", check=True)
        result = r.run(
            f"docker exec regent-api python /tmp/fork_remote.py {source} {arm}" + (" --dry-run" if dry_run else ""),
            timeout=900,
        )
    if not result.ok:
        raise SystemExit(f"[FAIL] 复制 {source}：{(result.err or result.out)[-1500:]}")
    return json.loads(result.text.splitlines()[-1])


def main() -> int:
    source, arm = sys.argv[1], sys.argv[2]
    print(json.dumps(fork(source, arm, dry_run="--dry-run" in sys.argv), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
