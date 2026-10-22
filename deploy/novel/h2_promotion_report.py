"""在服务器容器里列改进库的晋级候选，并对候选与近期章节做零调用回放校验。

晋级候选（第六节）：共享范围、已在本书采纳、又被别的书引用后帮一章过了硬门的条目。
本脚本只列出与校验，不晋级：晋级还要人工盲评（eval_cli），按冻结阈值裁决。

用法（本地）：python deploy/novel/h2_promotion_report.py [近期回放章数，默认 20]
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])

from _ssh import Remote  # noqa: E402

REMOTE_PY = r'''
import asyncio, json, sys, uuid

RECENT = int(sys.argv[1]) if len(sys.argv) > 1 else 20


async def replay_run(s, run):
    from regent.novel.harnessing import replay
    from regent.novel.infrastructure.models import ModelCallModel
    from sqlalchemy import select

    stored = await s.scalar(
        select(ModelCallModel.prompt_hash)
        .where(ModelCallModel.run_id == run.id, ModelCallModel.purpose == "chapter_write")
        .order_by(ModelCallModel.created_at)
        .limit(1)
    )
    ctx = run.generation_context or {}
    res = replay.check_write_replay(ctx, stored_prompt_hash=str(stored or ""))
    return {
        "run_id": str(run.id), "work": str(run.work_id)[:8], "chapter_no": int(run.chapter_no),
        "executor": ctx.get("executor"), "harness_id": ctx.get("harness_id") or "",
        "write_canon": ctx.get("write_canon") or "",
        "ok": res.get("ok"), "input_hash_ok": res.get("input_hash_ok"),
        "prompt_hash_ok": res.get("prompt_hash_ok"), "reason": res.get("reason") or "",
    }


async def main() -> None:
    from regent.config import get_settings
    from regent.infrastructure.database import create_engine, create_session_factory
    from regent.novel.harnessing import library
    from regent.novel.infrastructure.models import ChapterRunModel, HarnessLibraryModel
    from sqlalchemy import select

    out: dict = {}
    engine = create_engine(get_settings())
    sessions = create_session_factory(engine)
    try:
        async with sessions() as s:
            rows = (await s.scalars(select(HarnessLibraryModel).order_by(HarnessLibraryModel.created_at))).all()
            out["library"] = [
                {"id": str(r.id)[:8], "scope": r.scope, "status": r.status, "genre": r.genre_tag,
                 "work": str(r.source_work_id or "")[:8], "since": r.since_chapter,
                 "effective": r.effective_count, "baseline": r.baseline,
                 "reason": r.rollback_reason, "prompt_keys": sorted((r.config_diff or {}).get("prompts") or {})}
                for r in rows
            ]
            out["candidates"] = await library.promotion_candidates(s)
            for item in out["candidates"]:
                entry = await s.get(HarnessLibraryModel, uuid.UUID(item["id"]))
                run = await s.get(ChapterRunModel, entry.source_run_id) if entry.source_run_id else None
                item["source_replay"] = await replay_run(s, run) if run is not None else None
            recent = (await s.scalars(
                select(ChapterRunModel)
                .where(ChapterRunModel.state == "CANONIZED")
                .order_by(ChapterRunModel.updated_at.desc())
                .limit(RECENT)
            )).all()
            out["recent_replay"] = [
                await replay_run(s, run) for run in recent
                if (run.generation_context or {}).get("executor") in ("director_chapter", "director_chapter_h2")
            ]
    finally:
        await engine.dispose()
    print("###JSON###" + json.dumps(out, ensure_ascii=False, default=str))


asyncio.run(main())
'''


def main(argv: list[str]) -> int:
    recent = argv[0] if argv else "20"
    with Remote() as r:
        r.write_text("/opt/regent/sync/h2_promotion_remote.py", REMOTE_PY)
        r.run("docker cp /opt/regent/sync/h2_promotion_remote.py regent-api:/tmp/h2promo.py")
        res = r.run(f"docker exec regent-api python /tmp/h2promo.py {int(recent)}", timeout=300)
    if "###JSON###" not in res.out:
        print("[FAIL] 没有输出：", res.out[-2000:], res.err[-2000:])
        return 1
    payload = json.loads(res.out.split("###JSON###", 1)[1].strip())
    print("改进库：")
    for row in payload["library"]:
        print("  ", json.dumps(row, ensure_ascii=False))
    print(f"晋级候选 {len(payload['candidates'])} 条：")
    for item in payload["candidates"]:
        print("  ", json.dumps(item, ensure_ascii=False))
    bad = [item for item in payload["recent_replay"] if not item["ok"]]
    print(f"近期章节回放：{len(payload['recent_replay'])} 章，未复原 {len(bad)} 章")
    for item in payload["recent_replay"]:
        print("  ", json.dumps(item, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
