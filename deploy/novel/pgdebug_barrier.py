"""B-06 屏障异常诊断：复现场景 8 的 ticks=['claimed', KeyError: 'ASSEMBLE']。

不修改业务代码。做法：
1. 上传当前工作树到服务器临时库（复用 pg_verify 的打包逻辑）；
2. 注入插桩场景：monkeypatch ``works_app.advance_step`` 记录每次被调用的
   chapter_no 与时刻；worker tick 前后记录「当时数据库可见的 run 状态」与
   「ch4 屏障计数」；
3. 竞争跑 10 轮（每轮全新 work），外加 3 个无竞争确定性探针：
   A. 重演 QUEUED + ch4 QUEUED → 单 tick 必须领 ch1；
   B. 重演 RETRYABLE_FAILED + ch4 QUEUED → 单 tick 必须领 ch1（续跑）；
   C. ch1 CANONIZED + ch4 QUEUED（带完整步骤行）→ 单 tick 领 ch4，不抛 KeyError。

用法：``python deploy/novel/pgdebug_barrier.py``
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ssh import Remote  # noqa: E402
import pg_verify as pv  # noqa: E402

DEBUG_SCENARIO = r'''
import asyncio, os, sys, time, uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from regent.novel.application import memory as memory_app
from regent.novel.application import works as works_app
from regent.novel.infrastructure.models import (
    ChapterRunModel, ChapterStepModel, NovelPrincipalModel, StoryWorkModel,
)
from regent.novel.domain.models import ReportFactRequest

URL = os.environ["REGENT_DATABASE_URL"]
engine = create_async_engine(URL, pool_size=10, max_overflow=10, pool_pre_ping=True)
Sessions = async_sessionmaker(engine, expire_on_commit=False)

T0 = time.monotonic()
def stamp():
    return round(time.monotonic() - T0, 3)

def rec(*parts):
    print(f"[{stamp()}]", *parts, flush=True)

class _NoProvider:
    async def generate_structured(self, **kwargs):
        raise RuntimeError("no provider in certification run")

# --- 插桩：记录每次 advance_step 被谁调起 ---
_orig_advance_step = works_app.advance_step

async def _spy_advance_step(session, **kw):
    rec(">> advance_step CALLED chapter_no=", kw.get("chapter_no"))
    try:
        out = await _orig_advance_step(session, **kw)
        rec("<< advance_step OK chapter_no=", kw.get("chapter_no"),
            "progress=", None if out is None else (getattr(out, "chapter_no", None), str(getattr(out, "state", None))))
        return out
    except Exception as exc:
        rec("<< advance_step RAISED chapter_no=", kw.get("chapter_no"),
            type(exc).__name__, str(exc)[:100])
        raise

works_app.advance_step = _spy_advance_step

from regent.novel.application import executor as executor_app

_orig_pinned = executor_app.pinned_executor
def _spy_pinned(run):
    out = _orig_pinned(run)
    if run is None:
        rec("    [pinned_executor] run=None ->", out)
    else:
        rec("    [pinned_executor] run found: attempt=", run.attempt,
            "state=", run.state, "chapter_no=", run.chapter_no, "->", out)
    return out
executor_app.pinned_executor = _spy_pinned

_orig_lease = works_app.acquire_run_lease
async def _spy_lease(session, *, run, owner, **kw):
    rec("    [acquire_run_lease] run attempt=", run.attempt, "chapter_no=", run.chapter_no)
    return await _orig_lease(session, run=run, owner=owner, **kw)
works_app.acquire_run_lease = _spy_lease

_orig_owned = works_app._get_owned_work
async def _spy_owned(session, *, work_id, owner_id):
    work = await _orig_owned(session, work_id=work_id, owner_id=owner_id)
    rec("    [_get_owned_work] state=", work.state, "branch_id=", str(work.branch_id)[:8])
    return work
works_app._get_owned_work = _spy_owned

IN_FLIGHT = ("QUEUED", "RUNNING", "PENDING_DECISION", "AWAITING_INPUT", "RETRYABLE_FAILED")

async def dump_runs(s, work_id, label):
    rows = (await s.execute(
        select(ChapterRunModel.chapter_no, ChapterRunModel.attempt,
               ChapterRunModel.state, ChapterRunModel.lease_owner,
               ChapterRunModel.lease_expires_at)
        .where(ChapterRunModel.work_id == work_id)
        .order_by(ChapterRunModel.chapter_no, ChapterRunModel.attempt)
    )).all()
    rec(f"    runs@{label}:", [(r[0], r[1], r[2], r[3], str(r[4])[:19]) for r in rows])
    return rows

async def barrier_view(s, work_id, branch_id, ch):
    """与 advance_background_run 内部一致的屏障计数（<ch 的在途数）。"""
    return await s.scalar(
        select(func.count(ChapterRunModel.id)).where(
            ChapterRunModel.work_id == work_id,
            ChapterRunModel.branch_id == branch_id,
            ChapterRunModel.chapter_no < ch,
            ChapterRunModel.state.in_(IN_FLIGHT),
        )
    )

async def seed_work(i):
    work_id, branch_id = uuid.uuid4(), uuid.uuid4()
    async with Sessions() as s:
        owner = uuid.uuid4()
        s.add(NovelPrincipalModel(id=owner, subject=f"pgdbg{i}:{owner}"))
        s.add(StoryWorkModel(id=work_id, owner_id=owner, state="RUNNING",
                             genre="xuanhuan", branch_id=branch_id))
        await s.commit()
    return work_id, branch_id

async def setup_canonized(work_id, branch_id):
    async with Sessions() as s:
        work = await s.get(StoryWorkModel, work_id)
        work.latest_chapter_no = 3
        for ch in (1, 2, 3):
            s.add(ChapterRunModel(id=uuid.uuid4(), work_id=work_id, branch_id=branch_id,
                                  chapter_no=ch, attempt=1, state="CANONIZED"))
        await memory_app.record_chapter_memory(
            s, work=work, chapter_no=1,
            facts=[{"statement": "甲承诺明日归还钥匙", "quote": "甲承诺明日归还钥匙",
                    "known_by": ["甲"]}],
            cast=["甲"],
        )
        await s.commit()

async def report(statement, work_id):
    async with Sessions() as s:
        work = await s.get(StoryWorkModel, work_id)
        resp = await works_app.report_fact(
            s, owner_id=work.owner_id, work_id=work_id,
            payload=ReportFactRequest(statement=statement, chapter_no=1, subject="甲"),
        )
        await s.commit()
        return str(resp.ticket_id)

def make_worker(work_id, branch_id, tag):
    async def worker_tick():
        async with Sessions() as s:
            try:
                cnt = await barrier_view(s, work_id, branch_id, 4)
                rows = (await s.execute(
                    select(ChapterRunModel.chapter_no, ChapterRunModel.attempt,
                           ChapterRunModel.state, ChapterRunModel.updated_at,
                           ChapterRunModel.lease_owner, ChapterRunModel.lease_expires_at)
                    .where(ChapterRunModel.work_id == work_id)
                    .order_by(ChapterRunModel.chapter_no, ChapterRunModel.attempt)
                )).all()
                rec(f"  [{tag}] enter: view={[(r[0], r[1], r[2], str(r[3])[:23], r[4], str(r[5])[:23]) for r in rows]} barrier_ch4_count={cnt}")
                scan = (await s.execute(
                    select(ChapterRunModel.chapter_no, ChapterRunModel.attempt, ChapterRunModel.state)
                    .join(StoryWorkModel, StoryWorkModel.id == ChapterRunModel.work_id)
                    .where(
                        StoryWorkModel.state == "RUNNING",
                        ChapterRunModel.state.in_(("QUEUED", "RUNNING", "RETRYABLE_FAILED")),
                        ChapterRunModel.lease_expires_at.is_(None),
                    )
                    .order_by(ChapterRunModel.updated_at, ChapterRunModel.chapter_no)
                    .limit(16)
                )).all()
                rec(f"  [{tag}] scan-replica (updated_at order): {[(r[0], r[1], r[2]) for r in scan]}")
                t0 = time.monotonic()
                progress = await works_app.advance_background_run(s, provider=_NoProvider())
                rec(f"  [{tag}] abr returned chapter=", None if progress is None else progress.chapter_no,
                    "elapsed=", round(time.monotonic() - t0, 3))
                await s.commit()
                rec(f"  [{tag}] COMMITTED")
                return "claimed" if progress is not None else "none"
            except Exception as exc:
                await s.rollback()
                rec(f"  [{tag}] RAISED", type(exc).__name__, str(exc)[:120])
                return f"{type(exc).__name__}: {exc}"[:120]
    return worker_tick

async def insert_ch4(work_id, branch_id, with_steps=False):
    async with Sessions() as s:
        run = ChapterRunModel(
            id=uuid.uuid4(), work_id=work_id, branch_id=branch_id,
            chapter_no=4, attempt=1, state="QUEUED",
            generation_context={"architecture_version": "director_v2"},
        )
        s.add(run)
        await s.flush()
        if with_steps:
            for step in ("ASSEMBLE", "DIRECT", "PRODUCE", "REVIEW", "CANON"):
                s.add(ChapterStepModel(id=uuid.uuid4(), run_id=run.id, step=step,
                                       state="PENDING", input_version=1))
        await s.commit()
        return run.id

# ---------------------------------------------------------------------------
# 竞争轮：完整复刻场景 8，跑 N 轮
# ---------------------------------------------------------------------------
async def race_round(i):
    work_id, branch_id = await seed_work(i)
    await setup_canonized(work_id, branch_id)
    tix = await asyncio.gather(report("甲其实从未持有钥匙", work_id),
                               report("甲当晚根本不在场", work_id))
    await insert_ch4(work_id, branch_id, with_steps=False)
    rec(f"== ROUND {i} race ==")
    ticks = await asyncio.gather(make_worker(work_id, branch_id, "w1")(),
                                 make_worker(work_id, branch_id, "w2")())
    async with Sessions() as s:
        rows = (await s.execute(
            select(ChapterRunModel.chapter_no, ChapterRunModel.attempt, ChapterRunModel.state)
            .where(ChapterRunModel.work_id == work_id)
            .order_by(ChapterRunModel.chapter_no, ChapterRunModel.attempt)
        )).all()
        steps = (await s.execute(
            select(ChapterStepModel.run_id, ChapterStepModel.step, ChapterStepModel.state)
            .join(ChapterRunModel, ChapterRunModel.id == ChapterStepModel.run_id)
            .where(ChapterRunModel.work_id == work_id, ChapterRunModel.chapter_no == 1,
                   ChapterRunModel.attempt > 1)
        )).all()
    ch4 = [r for r in rows if r[0] == 4]
    replay = [r for r in rows if r[0] == 1 and r[1] > 1]
    ok = ch4 and ch4[0][2] == "QUEUED"
    rec(f"== ROUND {i} DONE ticks={ticks} ch4={ch4} replay={replay} replay_steps={steps}")
    print(("PASS " if ok else "FAIL ") + f"round{i} ch4 untouched", flush=True)
    return ok

# ---------------------------------------------------------------------------
# 探针 A：重演 QUEUED + ch4 QUEUED → 单 tick 必须领 ch1
# ---------------------------------------------------------------------------
async def probe_a():
    work_id, branch_id = await seed_work("A")
    await setup_canonized(work_id, branch_id)
    await report("甲其实从未持有钥匙", work_id)
    await insert_ch4(work_id, branch_id, with_steps=False)
    rec("== PROBE A (replay QUEUED + ch4 QUEUED, single tick) ==")
    tick = make_worker(work_id, branch_id, "solo")()
    result = await tick
    async with Sessions() as s:
        rows = (await s.execute(
            select(ChapterRunModel.chapter_no, ChapterRunModel.attempt, ChapterRunModel.state)
            .where(ChapterRunModel.work_id == work_id)
            .order_by(ChapterRunModel.chapter_no, ChapterRunModel.attempt)
        )).all()
    ch4 = [r for r in rows if r[0] == 4]
    replay = [r for r in rows if r[0] == 1 and r[1] > 1]
    ok = ch4[0][2] == "QUEUED" and replay[0][2] != "QUEUED"
    print(("PASS " if ok else "FAIL ") + f"probeA ch4={ch4[0][2]} replay={replay[0][2]} tick={result}", flush=True)

# ---------------------------------------------------------------------------
# 探针 B：重演 RETRYABLE_FAILED（已提交、租约已释放）+ ch4 QUEUED → 单 tick 领 ch1 续跑
# ---------------------------------------------------------------------------
async def probe_b():
    work_id, branch_id = await seed_work("B")
    await setup_canonized(work_id, branch_id)
    await report("甲其实从未持有钥匙", work_id)
    # 先跑一轮竞争把重演推进到 RETRYABLE_FAILED（或直接单 tick）
    await make_worker(work_id, branch_id, "pre")()
    await insert_ch4(work_id, branch_id, with_steps=False)
    rec("== PROBE B (replay RETRYABLE_FAILED + ch4 QUEUED, single tick) ==")
    result = await make_worker(work_id, branch_id, "solo")()
    async with Sessions() as s:
        rows = (await s.execute(
            select(ChapterRunModel.chapter_no, ChapterRunModel.attempt, ChapterRunModel.state)
            .where(ChapterRunModel.work_id == work_id)
            .order_by(ChapterRunModel.chapter_no, ChapterRunModel.attempt)
        )).all()
    ch4 = [r for r in rows if r[0] == 4]
    replay = [r for r in rows if r[0] == 1 and r[1] > 1]
    ok = ch4[0][2] == "QUEUED"
    print(("PASS " if ok else "FAIL ") + f"probeB ch4={ch4[0][2]} replay={replay[0][2]} tick={result}", flush=True)

# ---------------------------------------------------------------------------
# 探针 C：无在途 + ch4 QUEUED（带完整步骤行）→ 单 tick 领 ch4，无 KeyError
# ---------------------------------------------------------------------------
async def probe_c():
    work_id, branch_id = await seed_work("C")
    await setup_canonized(work_id, branch_id)
    await insert_ch4(work_id, branch_id, with_steps=True)
    rec("== PROBE C (ch1-3 CANONIZED + healthy ch4, single tick) ==")
    result = await make_worker(work_id, branch_id, "solo")()
    async with Sessions() as s:
        rows = (await s.execute(
            select(ChapterRunModel.chapter_no, ChapterRunModel.attempt, ChapterRunModel.state)
            .where(ChapterRunModel.work_id == work_id)
            .order_by(ChapterRunModel.chapter_no, ChapterRunModel.attempt)
        )).all()
    ch4 = [r for r in rows if r[0] == 4]
    ok = "KeyError" not in result
    print(("PASS " if ok else "FAIL ") + f"probeC ch4={ch4[0][2]} tick={result}", flush=True)

async def main():
    import sys as _sys
    mode = _sys.argv[1] if len(_sys.argv) > 1 else "race"
    if mode == "probe_c":
        await probe_c_only()
    else:
        await race_once()
    await engine.dispose()
    return 0

async def probe_c_only():
    work_id, branch_id = await seed_work("C")
    await setup_canonized(work_id, branch_id)
    await insert_ch4(work_id, branch_id, with_steps=True)
    rec("== PROBE C (healthy ch4, single tick) ==")
    result = await make_worker(work_id, branch_id, "solo")()
    async with Sessions() as s:
        rows = (await s.execute(
            select(ChapterRunModel.chapter_no, ChapterRunModel.attempt, ChapterRunModel.state)
            .where(ChapterRunModel.work_id == work_id)
            .order_by(ChapterRunModel.chapter_no, ChapterRunModel.attempt)
        )).all()
    ch4 = [r for r in rows if r[0] == 4]
    ok = "KeyError" not in result
    print(("PASS " if ok else "FAIL ") + f"probeC ch4={ch4[0][2]} tick={result}", flush=True)

async def race_once():
    work_id, branch_id = await seed_work("R")
    await setup_canonized(work_id, branch_id)
    tix = await asyncio.gather(report("甲其实从未持有钥匙", work_id),
                               report("甲当晚根本不在场", work_id))
    await insert_ch4(work_id, branch_id, with_steps=False)
    rec("== RACE (fresh DB, single work) ==")
    ticks = await asyncio.gather(make_worker(work_id, branch_id, "w1")(),
                                 make_worker(work_id, branch_id, "w2")())
    async with Sessions() as s:
        rows = (await s.execute(
            select(ChapterRunModel.chapter_no, ChapterRunModel.attempt, ChapterRunModel.state)
            .where(ChapterRunModel.work_id == work_id)
            .order_by(ChapterRunModel.chapter_no, ChapterRunModel.attempt)
        )).all()
    ch4 = [r for r in rows if r[0] == 4]
    replay = [r for r in rows if r[0] == 1 and r[1] > 1]
    ok = ch4 and ch4[0][2] == "QUEUED"
    rec(f"== RACE DONE ticks={ticks} ch4={ch4} replay={replay}")
    print(("PASS " if ok else "FAIL ") + f"race ch4 untouched ticks={ticks}", flush=True)
    # 竞后 drain tick：确认屏障在单 worker 下放行/拦住的最终行为
    drain = await make_worker(work_id, branch_id, "drain")()
    rec(f"== DRAIN tick={drain}")

sys.exit(asyncio.run(main()))
'''


def main() -> int:
    r = Remote()
    print("=== 1. 上传当前工作树 ===")
    r.run(f"rm -rf {pv.REMOTE_ROOT} && mkdir -p {pv.REMOTE_ROOT}", check=True)
    r.put(pv.build_tarball(), f"{pv.REMOTE_ROOT}/tree.tar.gz")
    r.run(f"tar -xzf {pv.REMOTE_ROOT}/tree.tar.gz -C {pv.REMOTE_ROOT} && rm -f {pv.REMOTE_ROOT}/tree.tar.gz")

    print("=== 2. 重建临时库 ===")
    for sql in (f"DROP DATABASE IF EXISTS {pv.SCRATCH_DB}", f"CREATE DATABASE {pv.SCRATCH_DB}"):
        out = r.run(f"docker exec regent-postgres psql -U {pv.PG_USER} -d regent -c '{sql}'")
        print(out.text[:200])

    print("=== 3. 复制到 api 容器 ===")
    cdir = f"/tmp/pgverify_{datetime.now().strftime('%H%M%S')}"
    print(r.run(f"docker cp {pv.REMOTE_ROOT} regent-api:{cdir}").text[:200])

    print("=== 4. alembic upgrade head ===")
    out = r.run(
        f"docker exec -w {cdir} -e REGENT_DATABASE_URL={pv.DB_URL} "
        f"regent-api python -m alembic upgrade head 2>&1 | tail -4",
        timeout=600,
    )
    print(out.text[:600])

    print("=== 5. 插桩诊断 ===")
    r.write_text(f"{pv.REMOTE_ROOT}/debug_barrier.py", DEBUG_SCENARIO)
    r.run(f"docker cp {pv.REMOTE_ROOT}/debug_barrier.py regent-api:{cdir}/debug_barrier.py")
    out = r.run(
        f"docker exec -w {cdir} -e REGENT_DATABASE_URL={pv.DB_URL} "
        f"-e PYTHONPATH={cdir}/core/src regent-api "
        f"python {cdir}/debug_barrier.py 2>&1",
        timeout=900,
    )
    print(out.text)
    return 0 if out.code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
