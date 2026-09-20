"""定位 advance_background_run 返回 None 的确切原因（在目标容器内跑）。

逐层打印：候选查询命中几条 → 锁内复核是否拿到 → work 是否取到 →
直接调 advance_step 会发生什么。
"""

from __future__ import annotations

import asyncio
import os
import traceback
from datetime import UTC, datetime

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession


async def main() -> int:
    engine = create_async_engine(os.environ["REGENT_DATABASE_URL"], pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from regent.novel.domain.states import ChapterRunState, StoryWorkState
    from regent.novel.infrastructure.models import ChapterRunModel, StoryWorkModel
    from regent.novel.application import works
    from regent.model.factory import build_model_provider
    from regent.config import Settings

    provider = build_model_provider(Settings())
    print("provider:", type(provider).__name__)

    async with factory() as s:
        rows = (
            await s.execute(
                select(ChapterRunModel, StoryWorkModel.state)
                .join(StoryWorkModel, StoryWorkModel.id == ChapterRunModel.work_id)
                .where(
                    StoryWorkModel.state == StoryWorkState.RUNNING.value,
                    StoryWorkModel.deleted_at.is_(None),
                    ChapterRunModel.state.in_((
                        ChapterRunState.QUEUED.value,
                        ChapterRunState.RUNNING.value,
                        ChapterRunState.RETRYABLE_FAILED.value,
                    )),
                    or_(
                        ChapterRunModel.lease_expires_at.is_(None),
                        ChapterRunModel.lease_expires_at <= datetime.now(UTC),
                    ),
                )
                .order_by(ChapterRunModel.updated_at, ChapterRunModel.chapter_no)
                .limit(16)
            )
        ).all()
        print(f"[1] 候选查询命中 {len(rows)} 条")
        for run, wstate in rows:
            print(f"    ch={run.chapter_no} run={run.state} work={wstate} "
                  f"work_id={str(run.work_id)[:8]} updated={run.updated_at}")
        if not rows:
            # 反向排查：到底哪个条件不成立
            for run, in (await s.execute(select(ChapterRunModel).limit(5))).all():
                w = await s.get(StoryWorkModel, run.work_id)
                print(f"    样本 ch={run.chapter_no} run={run.state} work={w.state if w else None} "
                      f"deleted={w.deleted_at if w else None}")
            return 0

        run, _ = rows[0]
        work = await s.get(StoryWorkModel, run.work_id)
        print(f"[2] 直接调 advance_step(work={str(work.id)[:8]}, ch={run.chapter_no})")
        try:
            out = await works.advance_step(
                s, provider=provider, owner_id=work.owner_id,
                work_id=work.id, chapter_no=run.chapter_no,
            )
            await s.commit()
            print(f"[3] advance_step 返回: {None if out is None else (out.chapter_no, out.state.value, out.current_step.value if out.current_step else None)}")
        except Exception:
            print("[3] advance_step 抛异常：")
            traceback.print_exc()
            return 1
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
