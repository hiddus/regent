"""回填 works.genre：按前提一次性归类到固定题材标签。

为什么需要：改进库按 ``genre_tag`` 检索同类型经验，而 ``works.genre`` 从来没被
澄清流程写过——LLM 动态澄清题整组替换写死模板，把题材题挤掉，题材根本没被问过。
真机 197 本书里 170 本是空的，这些书只能吃到 global 范围的经验。代码侧已修
（题材题必问 + ``answer_clarify`` 落 ``work.genre``），这里补历史数据。

归类走模型（每本一次结构化调用），只接受 ``GENRE_TAGS`` 里的值：自由文本会让标签
互相匹配不上，等于白回填。拿不准就归「其他」。

用法（本地）：
    python deploy/novel/backfill_work_genre.py              # 只看会改什么
    python deploy/novel/backfill_work_genre.py --apply      # 落库
    python deploy/novel/backfill_work_genre.py --limit 5    # 先跑几本看看
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402

REMOTE_PY = r'''
import asyncio, json, sys

APPLY = "--apply" in sys.argv
LIMIT = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0


async def main() -> None:
    from regent.config import get_settings
    from regent.infrastructure.database import create_engine, create_session_factory
    from regent.model.factory import build_model_provider
    from regent.novel.application.generation import GenreTag
    from regent.novel.application.works_constants import GENRE_TAGS
    from sqlalchemy import text

    settings = get_settings()
    provider = build_model_provider(settings)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    system = (
        "你是网文编辑。把给定的故事前提归到一个题材标签。\n"
        f"只能从这些标签里选一个：{'、'.join(GENRE_TAGS)}。\n"
        "判断不了就选「其他」。不要解释。"
    )
    out: dict = {"apply": APPLY, "changes": [], "errors": [], "tags": {}}
    try:
        async with sessions() as s:
            rows = (
                await s.execute(
                    text(
                        "select w.id, w.title, g.raw_intent from novel_works w "
                        "join lateral (select raw_intent from novel_goals g "
                        "              where g.work_id = w.id "
                        "              order by g.version desc limit 1) g on true "
                        "where coalesce(w.genre, '') = '' order by w.created_at"
                        + (f" limit {LIMIT}" if LIMIT else "")
                    )
                )
            ).all()
            out["candidates"] = len(rows)
            for work_id, title, raw_intent in rows:
                try:
                    res = await provider.generate_structured(
                        system_prompt=system,
                        user_prompt=str(raw_intent or title or "")[:1200],
                        response_model=GenreTag,
                        temperature=0,
                    )
                    tag = str(res.output.genre or "").strip()
                except Exception as exc:
                    out["errors"].append(
                        {"work_id": str(work_id), "error": f"{type(exc).__name__}: {exc}"[:200]}
                    )
                    continue
                if tag not in GENRE_TAGS:
                    out["errors"].append(
                        {"work_id": str(work_id), "error": f"标签越界: {tag[:40]}"}
                    )
                    continue
                out["changes"].append(
                    {"work_id": str(work_id), "title": str(title or "")[:24], "genre": tag}
                )
                out["tags"][tag] = out["tags"].get(tag, 0) + 1
                if APPLY:
                    await s.execute(
                        text("update novel_works set genre = :g where id = cast(:w as uuid)"),
                        {"g": tag, "w": str(work_id)},
                    )
            if APPLY and out["changes"]:
                await s.commit()
            out["updated"] = len(out["changes"]) if APPLY else 0
            out["still_empty"] = int(
                (
                    await s.execute(
                        text("select count(*) from novel_works where coalesce(genre, '') = ''")
                    )
                ).scalar()
                or 0
            )
    finally:
        await engine.dispose()
    print("###JSON###" + json.dumps(out, ensure_ascii=False))


asyncio.run(main())
'''


def main() -> int:
    apply = "--apply" in sys.argv
    remote_args = " ".join(sys.argv[1:])
    with Remote() as r:
        r.write_text("/opt/regent/sync/backfill_work_genre_remote.py", REMOTE_PY)
        r.run("docker cp /opt/regent/sync/backfill_work_genre_remote.py regent-api:/tmp/bf_genre.py")
        res = r.run(f"docker exec regent-api python /tmp/bf_genre.py {remote_args}", timeout=1800)
        if "###JSON###" not in res.out:
            print("[FAIL] 回填脚本没有输出：", res.out[-3000:], res.err[-3000:])
            return 1
        payload = json.loads(res.out.split("###JSON###", 1)[1].strip())

    print(f"候选（genre 为空）：{payload.get('candidates')}")
    for item in payload["changes"]:
        print(f"  {item['work_id'][:8]}  {item['title']:<24} → {item['genre']}")
    for item in payload["errors"]:
        print(f"  [跳过] {item['work_id'][:8]}  {item['error']}")
    print(f"分布：{payload.get('tags')}")
    if apply:
        print(f"[已落库] 更新 {payload['updated']} 本；仍为空 {payload['still_empty']} 本")
    else:
        print(f"[未落库] 将更新 {len(payload['changes'])} 本；加 --apply 执行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
