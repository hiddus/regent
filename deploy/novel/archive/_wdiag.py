"""在 worker 容器内用**它自己的代码和环境**跑一次小说推进循环。

目的：分清「worker 代码推不动」还是「worker 根本没进小说分支」。
- 若这里能推进 → worker 主循环没调用它（装配/门条件问题）；
- 若这里返回 None → 候选查询选不中这条 run（查询条件问题）；
- 若这里抛异常 → 真缺陷，看堆栈。

用法：docker cp 进容器后 `python /tmp/wdiag.py`
"""

from __future__ import annotations

import asyncio
import os
import traceback


async def main() -> int:
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    url = os.environ["REGENT_DATABASE_URL"]
    engine = create_async_engine(url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from regent.novel.application import works
    from regent.novel.application.works import advance_background_run

    try:
        provider = None
        try:
            from regent.model.factory import build_model_provider
            from regent.config import Settings

            provider = build_model_provider(Settings())
            print("provider:", type(provider).__name__ if provider else None)
        except Exception as exc:  # 装配失败也要继续：先看纯推进
            print("provider 装配失败:", type(exc).__name__, exc)

        for i in range(3):
            async with factory() as s:
                out = await advance_background_run(s, provider=provider)
                await s.commit()
                if out is None:
                    print(f"[{i}] advanced: None（候选查询没选中任何 run）")
                else:
                    step = out.current_step.value if out.current_step else None
                    print(f"[{i}] advanced: ch={out.chapter_no} state={out.state.value} step={step}")
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
