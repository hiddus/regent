"""把存量「试用中的 global 条目」降级为本书范围。

为什么需要：`record_verified_round` 曾经只按卡司人名筛专有性，第 4 章那条
「同字不同齿距」实际依赖本书的齿轮设定，却以 `scope=global` 入了库。代码侧已修
（`book_terms` 扩到世界设定的人物地名别名，再加一次 SCREEN 调用判改写过的说法），
但修的是**新入库**的条目——存量那条还挂在 `provisional`。

`library.search` 现在要求试用条目 `source_work_id == 本书`，所以它眼下对别的书
不可见。可 `settle_trial` 只改 `status`、不改 `scope`：三章试用一过，它就变成
`adopted` 的 global 条目，对所有书可见，而那时已经没人再判它通不通用。

用法（本地）：
    python deploy/novel/demote_provisional_global.py            # 只看会改什么
    python deploy/novel/demote_provisional_global.py --apply    # 落库
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402

REMOTE_PY = r'''
import asyncio, json, sys

APPLY = "--apply" in sys.argv


async def main() -> None:
    from regent.config import get_settings
    from regent.infrastructure.database import create_engine, create_session_factory
    from regent.novel.infrastructure.models import HarnessLibraryModel
    from sqlalchemy import select

    engine = create_engine(get_settings())
    sessions = create_session_factory(engine)
    changes, skipped = [], []
    try:
        async with sessions() as s:
            rows = (
                await s.scalars(
                    select(HarnessLibraryModel).where(
                        HarnessLibraryModel.status == "provisional",
                        HarnessLibraryModel.scope == "global",
                    )
                )
            ).all()
            for row in rows:
                if row.source_work_id is None:
                    skipped.append({"id": str(row.id), "reason": "没有 source_work_id，降不成 book 范围"})
                    continue
                changes.append(
                    {
                        "id": str(row.id),
                        "work": str(row.source_work_id),
                        "since_chapter": row.since_chapter,
                        "experience": (row.experience or "")[:60],
                    }
                )
                if APPLY:
                    row.scope = "work"
                    row.scope_key = str(row.source_work_id)
            if APPLY:
                await s.commit()
    finally:
        await engine.dispose()
    print("###JSON###" + json.dumps({"applied": APPLY, "changes": changes, "skipped": skipped},
                                    ensure_ascii=False))


asyncio.run(main())
'''


def main() -> int:
    apply = "--apply" in sys.argv
    remote_args = " ".join(sys.argv[1:])
    with Remote() as r:
        r.write_text("/opt/regent/sync/demote_provisional_global_remote.py", REMOTE_PY)
        r.run("docker cp /opt/regent/sync/demote_provisional_global_remote.py regent-api:/tmp/demote.py")
        res = r.run(f"docker exec regent-api python /tmp/demote.py {remote_args}", timeout=300)
        if "###JSON###" not in res.out:
            print("[FAIL] 脚本没有输出：", res.out[-2000:], res.err[-2000:])
            return 1
        payload = json.loads(res.out.split("###JSON###", 1)[1].strip())

    for item in payload["changes"]:
        print(f"  {item['id'][:8]}  book={item['work'][:8]}  since_ch={item['since_chapter']}"
              f"  global → work   {item['experience']}")
    for item in payload["skipped"]:
        print(f"  [跳过] {item['id'][:8]}  {item['reason']}")
    if not payload["changes"] and not payload["skipped"]:
        print("  没有试用中的 global 条目")
    if payload["applied"]:
        print(f"[已落库] 降级 {len(payload['changes'])} 条")
    else:
        print(f"[未落库] 将降级 {len(payload['changes'])} 条；加 --apply 执行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
