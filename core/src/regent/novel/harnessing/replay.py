"""晋级前的零调用回放校验：用已入库运行的上下文重建首稿写作入参，核对 input_hash。

哈希格式必须与 ``directing_calls._call`` 记账一致：``json.dumps(payload,
ensure_ascii=False)``，不排序键，UTF-8 后取 sha256。

用法：``python -m regent.novel.harnessing.replay <run_id> [<run_id> ...]``
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
from typing import Any


def compute_payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def verify_replay_hash(stored_hash: str, payload: dict[str, Any]) -> bool:
    return compute_payload_hash(payload) == stored_hash


def rebuild_first_write_payload(context: dict[str, Any]) -> dict[str, Any]:
    """按钉住的配置重建这一章第一稿（不含重写、不含递归轮）的写作入参。"""
    from regent.novel.application import directing_chapter as chapter

    production = dict(context.get("production") or {})
    cast = production.get("cast") or {}
    ctx = dict(context)
    plan = production.get("chapter_plan")
    if isinstance(plan, dict) and plan.get("choice"):
        ctx["target_node"] = plan
    if chapter._is_h2(production):
        return chapter.h2_write_payload(ctx, cast, chapter.h2_config(ctx, {}))
    return chapter.build_write_payload(ctx, cast)


def check_write_replay(context: dict[str, Any]) -> dict[str, Any]:
    production = (context or {}).get("production") or {}
    calls = [c for c in production.get("calls") or [] if c.get("purpose") == "chapter_write"]
    if not calls:
        return {"ok": False, "reason": "没有正文调用记录"}
    stored = str(calls[0].get("input_hash") or "")
    rebuilt = compute_payload_hash(rebuild_first_write_payload(context))
    return {
        "ok": stored == rebuilt,
        "stored": stored,
        "rebuilt": rebuilt,
        "reason": "" if stored == rebuilt else "上下文在首稿之后被改写，或载荷装配已变",
    }


async def _check(run_ids: list[str], database_url: str) -> list[dict[str, Any]]:
    from regent.config import get_settings
    from regent.infrastructure.database import create_engine, create_session_factory
    from regent.novel.infrastructure.models import ChapterRunModel

    if database_url:
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(database_url, pool_pre_ping=True)
    else:
        engine = create_engine(get_settings())
    sessions = create_session_factory(engine)
    out: list[dict[str, Any]] = []
    try:
        async with sessions() as session:
            for run_id in run_ids:
                run = await session.get(ChapterRunModel, uuid.UUID(run_id))
                if run is None:
                    out.append({"run_id": run_id, "ok": False, "reason": "运行不存在"})
                    continue
                out.append({"run_id": run_id, **check_write_replay(run.generation_context or {})})
    finally:
        await engine.dispose()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="novel-harness-replay", description=__doc__)
    parser.add_argument("run_ids", nargs="+")
    parser.add_argument("--database-url", default="")
    args = parser.parse_args(argv)
    results = asyncio.run(_check(list(args.run_ids), args.database_url))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(item.get("ok") for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
