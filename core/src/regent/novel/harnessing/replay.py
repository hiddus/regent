"""晋级前的零调用回放校验：用已入库运行的上下文重建首稿写作入参与系统提示，
核对 ``input_hash`` 与 ``prompt_hash``。两个都对得上，才说明钉住的配置真能复原。

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


def rebuild_first_write(context: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """按钉住的配置重建这一章第一稿（不含重写、不含递归轮）的入参与系统提示。"""
    from regent.novel.application import directing_chapter as chapter

    production = dict(context.get("production") or {})
    cast = production.get("cast") or {}
    ctx = dict(context)
    plan = production.get("chapter_plan")
    if isinstance(plan, dict) and plan.get("choice"):
        ctx["target_node"] = plan
    if chapter._is_h2(production):
        config = chapter.h2_config(ctx, {})
        return (
            chapter.h2_write_payload(ctx, cast, config),
            chapter.h2_writer_system(ctx, cast, config, production),
        )
    return chapter.build_write_payload(ctx, cast), chapter._writer_system(ctx, cast)


def check_write_replay(context: dict[str, Any], *, stored_prompt_hash: str = "") -> dict[str, Any]:
    """首稿的入参与系统提示都要能零调用复原，才算配置真被钉住了。

    只核 ``input_hash`` 证明不了覆盖层：第 4 章的参照稿与两个探针入参完全相同，
    只有系统提示不同，而覆盖层改的正是提示词。``stored_prompt_hash`` 取自
    ``novel_model_calls.prompt_hash``，不给就不做这一半校验。
    """
    production = (context or {}).get("production") or {}
    calls = [c for c in production.get("calls") or [] if c.get("purpose") == "chapter_write"]
    if not calls:
        return {"ok": False, "reason": "没有正文调用记录"}
    payload, system = rebuild_first_write(context)
    stored = str(calls[0].get("input_hash") or "")
    rebuilt = compute_payload_hash(payload)
    rebuilt_prompt = hashlib.sha256(system.encode("utf-8")).hexdigest()
    input_ok = stored == rebuilt
    prompt_ok = stored_prompt_hash == rebuilt_prompt if stored_prompt_hash else None
    reasons: list[str] = []
    if not input_ok:
        reasons.append("入参对不上：上下文在首稿之后被改写，或载荷装配已变")
    if prompt_ok is False:
        reasons.append("系统提示对不上：钉住的配置没能复原")
    elif prompt_ok is None:
        reasons.append("缺 prompt_hash，配置这一半没校验")
    return {
        "ok": input_ok and prompt_ok is True,
        "input_hash_ok": input_ok,
        "prompt_hash_ok": prompt_ok,
        "stored": stored,
        "rebuilt": rebuilt,
        "stored_prompt": stored_prompt_hash,
        "rebuilt_prompt": rebuilt_prompt,
        "reason": "；".join(reasons),
    }


async def _check(run_ids: list[str], database_url: str) -> list[dict[str, Any]]:
    from regent.config import get_settings
    from regent.infrastructure.database import create_engine, create_session_factory
    from regent.novel.infrastructure.models import ChapterRunModel, ModelCallModel
    from sqlalchemy import select

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
                stored_prompt = await session.scalar(
                    select(ModelCallModel.prompt_hash)
                    .where(
                        ModelCallModel.run_id == run.id,
                        ModelCallModel.purpose == "chapter_write",
                    )
                    .order_by(ModelCallModel.created_at)
                    .limit(1)
                )
                out.append(
                    {
                        "run_id": run_id,
                        **check_write_replay(
                            run.generation_context or {},
                            stored_prompt_hash=str(stored_prompt or ""),
                        ),
                    }
                )
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
