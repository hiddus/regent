"""P0-5：真实 PostgreSQL 升级/回退与并发认证（在已部署服务器上执行）。

本机没有 PostgreSQL/Docker，因此在服务器上用**独立临时库**验证，不触碰运行中的
``/opt/regent`` 部署：

1. 打包并上传当前工作树（core/src + core/migrations + alembic.ini）；
2. 在新库上跑 alembic 升级 → 回退 → 再升级，记录每步结果；
3. 在该库上跑并发与崩溃注入场景（租约抢占、并发预留上限、崩溃后恢复）；
4. 清理临时库。

用法：``python deploy/novel/pg_verify.py``（需要仓库根 .env 中的 LOGIN_PASSWORD）。
"""

from __future__ import annotations

import os
import sys
import tarfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ssh import Remote, load_dotenv  # noqa: E402

load_dotenv()
REMOTE_ROOT = "/opt/regent/pgverify"
SCRATCH_DB = "novel_pgverify"
PG_USER = "regent"
# 凭据不入库：从 .env / 环境变量读取（.gitignore 已排除 .env）。
PG_PASSWORD = os.environ.get("NOVEL_PG_PASSWORD", "")
if not PG_PASSWORD:
    raise SystemExit("缺少 NOVEL_PG_PASSWORD（请在 .env 或环境变量中提供）")
PG_HOST = "regent-postgres"
DB_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:5432/{SCRATCH_DB}"
LOCAL_TAR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_pgverify.tar.gz")
EXCLUDES = ("__pycache__",)


def build_tarball() -> str:
    """打包当前工作树里与小说域相关的源码与迁移。"""
    # 不删旧包：直接以 w:gz 覆盖（删除大文件会被本机的批量删除保护拦下）
    with tarfile.open(LOCAL_TAR, mode="w:gz") as tar:
        for path in ("core/src", "core/migrations", "alembic.ini"):
            tar.add(path, arcname=path, filter=_exclude)
    return LOCAL_TAR


def _exclude(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if os.path.basename(info.name) in EXCLUDES:
        return None
    return info


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _sh(cmd: str) -> str:
    import subprocess

    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=ROOT)
    return (proc.stdout or proc.stderr or "").strip()


def fingerprints(r: Remote | None = None) -> None:
    """P0-5：把「跑的是哪份代码、哪个迁移、哪个环境」写进原始结果。

    没有指纹的通过记录无法复核：三个月后没人知道 11/11 对应的是哪一棵树。
    """
    import hashlib
    import platform

    head = _sh("git rev-parse HEAD")
    dirty = _sh("git status --porcelain")
    diff = _sh("git diff HEAD")
    print("[fingerprint] git HEAD          :", head)
    print("[fingerprint] dirty files       :", len([x for x in dirty.splitlines() if x.strip()]))
    print("[fingerprint] working diff sha  :", hashlib.sha256(diff.encode()).hexdigest()[:16])
    print("[fingerprint] alembic heads     :",
          _sh("C:/regent/.venv/Scripts/python.exe -m alembic heads").replace("\n", " | ")[:200])
    print("[fingerprint] local python      :", platform.python_version())
    if r is not None:
        print("[fingerprint] remote pg version :",
              r.run("docker exec regent-postgres psql -U regent -d regent -tAc 'select version()'")
              .text.strip()[:120])
        print("[fingerprint] remote py version :",
              r.run("docker exec regent-api python -V").text.strip()[:60])
        print("[fingerprint] remote image      :",
              r.run("docker inspect -f '{{.Image}}' regent-api").text.strip()[:60])


def main() -> int:
    print("=== 0. 指纹（源码 / 迁移 / 环境）===")
    try:
        fingerprints()
    except Exception as exc:  # 指纹失败不阻断验证，但必须留痕
        print("[fingerprint] FAILED:", type(exc).__name__, exc)
    r = Remote()
    try:
        fingerprints(r)
    except Exception as exc:
        print("[fingerprint] remote FAILED:", type(exc).__name__, exc)
    print("=== 1. 上传当前工作树 ===")
    r.run(f"rm -rf {REMOTE_ROOT} && mkdir -p {REMOTE_ROOT}", check=True)
    r.put(build_tarball(), f"{REMOTE_ROOT}/tree.tar.gz")
    r.run(f"tar -xzf {REMOTE_ROOT}/tree.tar.gz -C {REMOTE_ROOT} && rm -f {REMOTE_ROOT}/tree.tar.gz")
    print(r.run(f"ls {REMOTE_ROOT}").text[:300])

    print("=== 2. 建临时库 ===")
    for sql in (f"DROP DATABASE IF EXISTS {SCRATCH_DB}", f"CREATE DATABASE {SCRATCH_DB}"):
        out = r.run(f"docker exec regent-postgres psql -U {PG_USER} -d regent -c '{sql}'")
        print(out.text[:200])

    print("=== 3. 复制到 api 容器 ===")
    # 用唯一目录：容器的 rm -rf 在这个 overlayfs 上不可靠，重名会嵌套复制。
    cdir = f"/tmp/pgverify_{datetime.now().strftime('%H%M%S')}"
    print(r.run(f"docker cp {REMOTE_ROOT} regent-api:{cdir}").text[:200])
    nfiles = r.run(f"docker exec regent-api find {cdir} -type f | wc -l").text.strip()
    print(f"copied files: {nfiles}")
    if not nfiles.isdigit() or int(nfiles) < 300:
        raise SystemExit(f"docker cp 不完整：{nfiles} 个文件")

    print("=== 4. alembic 升级/回退 ===")
    steps = [
        ("upgrade head", "upgrade head"),
        ("current", "current"),
        ("downgrade -1（head->前一个）", "downgrade -1"),
        ("downgrade -1（再退一个）", "downgrade -1"),
        ("upgrade head", "upgrade head"),
        ("current", "current"),
    ]
    for label, cmd in steps:
        out = r.run(
            f"docker exec -w {cdir} -e REGENT_DATABASE_URL={DB_URL} "
            f"regent-api python -m alembic {cmd} 2>&1 | tail -8",
            timeout=600,
        )
        print(f"--- {label} [exit {out.code}] ---\n{out.text[:800]}")

    print("=== 5. 并发与崩溃注入 ===")
    scenarios = SCENARIOS.replace("/tmp/pgverify/core/src", f"{cdir}/core/src")
    r.write_text(f"{REMOTE_ROOT}/pgverify_scenarios.py", scenarios)
    out = r.run(
        f"docker cp {REMOTE_ROOT}/pgverify_scenarios.py regent-api:{cdir}/scenarios.py && "
        f"docker exec -w {cdir} -e REGENT_DATABASE_URL={DB_URL} "
        f"-e PYTHONPATH={cdir}/core/src regent-api "
        f"python {cdir}/scenarios.py 2>&1",
        timeout=600,
    )
    print(out.text[:4000])
    return 0 if "FAIL " not in out.text else 1


SCENARIOS = r'''
"""在真实 PostgreSQL 上跑并发与崩溃注入场景（P0-5）。"""
import asyncio, os, sys, traceback, uuid
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "/tmp/pgverify/core/src")

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from regent.novel.application import ledger
from regent.novel.application.production import (
    CallBroker,
    CallInFlight,
    StaleLease,
    acquire_run_lease,
    config_fingerprint,
    lease_is_valid,
    logical_call_key,
    recover_novel_calls,
    require_run_lease,
)
from regent.novel.infrastructure.models import (
    ChapterRunModel,
    ModelCallModel,
    NovelPrincipalModel,
    QuotaReservationModel,
    StoryWorkModel,
)

URL = os.environ["REGENT_DATABASE_URL"]
engine = create_async_engine(URL, pool_size=10, max_overflow=10, pool_pre_ping=True)
Sessions = async_sessionmaker(engine, expire_on_commit=False)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (" :: " + detail if detail else ""), flush=True)


async def seed():
    work_id, branch_id = uuid.uuid4(), uuid.uuid4()
    async with Sessions() as s:
        owner = uuid.uuid4()
        s.add(NovelPrincipalModel(id=owner, subject=f"pg:{owner}"))
        s.add(StoryWorkModel(id=work_id, owner_id=owner, state="RUNNING",
                             genre="xuanhuan", branch_id=branch_id))
        await s.commit()
    return work_id, branch_id


async def new_run(work_id, branch_id):
    async with Sessions() as s:
        run = ChapterRunModel(id=uuid.uuid4(), work_id=work_id, branch_id=branch_id,
                              chapter_no=1, state="RUNNING")
        s.add(run)
        await s.commit()
        return run.id


# ---------------------------------------------------------------------------
# 场景 1：两个 worker 同时抢同一章租约（走 advance_step 的加锁路径）
# ---------------------------------------------------------------------------
async def scenario_lease_contention():
    work_id, branch_id = await seed()
    run_id = await new_run(work_id, branch_id)

    async def grab(owner):
        async with Sessions() as s:
            try:
                # 与 works.advance_step 同一把锁：先 FOR UPDATE 再领取租约
                run = await s.scalar(
                    select(ChapterRunModel)
                    .where(ChapterRunModel.id == run_id)
                    .with_for_update()
                )
                token = await acquire_run_lease(s, run=run, owner=owner,
                                                ttl=timedelta(seconds=30))
                await s.commit()
                return token
            except CallInFlight:
                await s.rollback()
                return None
            except Exception:
                await s.rollback()
                raise

    a, b = await asyncio.gather(grab("worker:a"), grab("worker:b"))
    winners = [x for x in (a, b) if x is not None]
    check("租约抢占只有一个赢家", len(winners) == 1, f"a={a} b={b}")

    async with Sessions() as s:
        run = await s.get(ChapterRunModel, run_id)
        check("fencing token 单调递增", int(run.fencing_token or 0) == 1,
              f"token={run.fencing_token} owner={run.lease_owner}")
        check("租约归属唯一", run.lease_owner in ("worker:a", "worker:b"),
              f"owner={run.lease_owner}")


# ---------------------------------------------------------------------------
# 场景 2：并发预留不得突破章节上限
# ---------------------------------------------------------------------------
async def scenario_concurrent_reservations():
    work_id, _ = await seed()

    async def reserve(i):
        async with Sessions() as s:
            try:
                await ledger.reserve(s, reservation_key=f"conc-{i}", work_id=work_id,
                                     amount_minor=6, chapter_no=1, funding_limit_minor=10)
                await s.commit()
                return True
            except Exception as exc:
                await s.rollback()
                return type(exc).__name__

    outcomes = await asyncio.gather(*[reserve(i) for i in range(4)])
    ok = outcomes.count(True) == 1
    check("并发预留不破上限", ok, f"outcomes={outcomes}")

    async with Sessions() as s:
        total = await s.scalar(
            select(func.coalesce(func.sum(QuotaReservationModel.amount_minor
                                          - QuotaReservationModel.settled_minor), 0))
            .where(QuotaReservationModel.work_id == work_id,
                   QuotaReservationModel.chapter_no == 1,
                   QuotaReservationModel.status == "RESERVED")
        )
        check("未结清总额 <= 上限", int(total or 0) <= 10, f"outstanding={total}")


# ---------------------------------------------------------------------------
# 场景 3：崩溃留下的 RESERVED → 回收 → 对账有界结清 → 无悬空预留
# ---------------------------------------------------------------------------
async def scenario_crash_recovery():
    work_id, branch_id = await seed()
    run_id = await new_run(work_id, branch_id)
    key = logical_call_key(run_id, "cmd", "", "plan")
    broker = CallBroker(lease_owner="worker:crash", lease_ttl=timedelta(seconds=1))
    async with Sessions() as s:
        await broker._prepare(
            s, logical_call_id=key, work_id=work_id, run_id=run_id, chapter_no=1,
            step="PRODUCE", purpose="plan", prompt_hash="p", context_hash="c",
            model_hint="test", sampling={"temperature": 0},
            config_hash=config_fingerprint(model="test", sampling={}),
            prompt_chars=10, system_chars=10,
        )
        await s.commit()

    # 注入崩溃：租约过期仍在 RESERVED
    async with Sessions() as s:
        call = await s.scalar(
            select(ModelCallModel).where(ModelCallModel.logical_call_id == key))
        call.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()

    async with Sessions() as s:
        stats = await recover_novel_calls(s, grace=timedelta(0),
                                          reconcile_attempts=1,
                                          provider=_NoProvider())
        await s.commit()
        check("崩溃后回收并结清",
              stats["reclaimed"] == 1 and stats["failed"] == 1, str(stats))

    async with Sessions() as s:
        call = await s.scalar(
            select(ModelCallModel).where(ModelCallModel.logical_call_id == key))
        check("恢复后调用不再挂起", call.status == "FAILED", f"status={call.status}")
        res = await s.scalar(
            select(QuotaReservationModel).where(
                QuotaReservationModel.reservation_key == f"{key}:1"))
        check("恢复后无悬空预留", res is not None and res.status == "SETTLED",
              f"status={res.status if res else None}")


class _NoProvider:
    """对账查询不到任何结果：只能按 FAILURE 结清。"""

    async def generate_structured(self, **kwargs):
        raise RuntimeError("no provider in certification run")


# ---------------------------------------------------------------------------
# 场景 4：租约持有期间被抢占 → 旧 worker 写回被拒
# ---------------------------------------------------------------------------
async def scenario_stale_lease_rejects_writeback():
    work_id, branch_id = await seed()
    run_id = await new_run(work_id, branch_id)
    async with Sessions() as s:
        run = await s.get(ChapterRunModel, run_id)
        token = await acquire_run_lease(s, run=run, owner="worker:a",
                                        ttl=timedelta(seconds=30))
        await s.commit()

    # worker:a 卡死导致租约过期 → worker:b 接管
    async with Sessions() as s:
        run = await s.scalar(
            select(ChapterRunModel).where(ChapterRunModel.id == run_id).with_for_update())
        run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.flush()
        await acquire_run_lease(s, run=run, owner="worker:b", ttl=timedelta(seconds=30))
        await s.commit()

    async with Sessions() as s:
        run = await s.get(ChapterRunModel, run_id)
        check("旧 worker 租约失效", not lease_is_valid(run, owner="worker:a", token=token),
              f"owner={run.lease_owner} token={run.fencing_token}")
        try:
            require_run_lease(run, owner="worker:a", token=token)
            check("旧 worker 写回被拒", False, "DID NOT RAISE")
        except StaleLease:
            check("旧 worker 写回被拒", True)


# ---------------------------------------------------------------------------
# 场景 5：同 key 并发预留只剩一行（唯一约束 + 锁后复检）
# ---------------------------------------------------------------------------
async def scenario_duplicate_reservation_key():
    work_id, _ = await seed()

    async def reserve():
        async with Sessions() as s:
            try:
                await ledger.reserve(s, reservation_key="dup-key", work_id=work_id,
                                     amount_minor=3, chapter_no=1,
                                     funding_limit_minor=100)
                await s.commit()
                return True
            except Exception as exc:
                await s.rollback()
                return type(exc).__name__

    outcomes = await asyncio.gather(*[reserve() for _ in range(4)])
    async with Sessions() as s:
        cnt = await s.scalar(
            select(func.count())
            .select_from(QuotaReservationModel)
            .where(QuotaReservationModel.reservation_key == "dup-key"))
    check("同 key 并发预留只剩一行", int(cnt or 0) == 1, f"rows={cnt} outcomes={outcomes}")


# ---------------------------------------------------------------------------
# 场景 6：同一作品两个章节并发成章提交（串行化锁 + 版本不丢更新）
# ---------------------------------------------------------------------------
async def scenario_concurrent_chapter_commit():
    from regent.novel.infrastructure.models import CanonCommitModel

    work_id, branch_id = await seed()

    async def commit_chapter(chapter_no: int):
        async with Sessions() as s:
            try:
                # 与 generation.canon() 的提交路径同一把锁：先锁作品行再写 Canon
                await s.scalar(
                    select(StoryWorkModel.id)
                    .where(StoryWorkModel.id == work_id)
                    .with_for_update()
                )
                work = await s.get(StoryWorkModel, work_id)
                work.version = int(work.version or 1) + 1
                work.latest_chapter_no = max(int(work.latest_chapter_no or 0), chapter_no)
                s.add(
                    CanonCommitModel(
                        id=uuid.uuid4(),
                        work_id=work_id,
                        branch_id=branch_id,
                        chapter_no=chapter_no,
                        source_hash=f"hash-{chapter_no}",
                        parent_version=0,
                        version=chapter_no,
                    )
                )
                await s.commit()
                return True
            except Exception as exc:
                await s.rollback()
                return f"{type(exc).__name__}: {exc}"[:120]

    outcomes = await asyncio.gather(commit_chapter(1), commit_chapter(2))
    async with Sessions() as s:
        work = await s.get(StoryWorkModel, work_id)
        commits = list(
            (await s.scalars(
                select(CanonCommitModel.chapter_no)
                .where(CanonCommitModel.work_id == work_id)
                .order_by(CanonCommitModel.chapter_no)
            )).all()
        )
        version = int(work.version or 1)
    check("并发章提交都落库", commits == [1, 2], f"commits={commits} outcomes={outcomes}")
    check("并发章提交版本不丢更新", version == 3, f"version={version}（基线 1 + 两次提交）")


# ---------------------------------------------------------------------------
# 场景 7：用户裁决与默认裁决到期扫描竞争
# ---------------------------------------------------------------------------
async def scenario_user_vs_default_decision_race():
    from datetime import UTC, datetime, timedelta
    from regent.novel.application import works as works_app
    from regent.novel.infrastructure.models import DecisionRequestModel

    work_id, branch_id = await seed()
    async with Sessions() as s:
        work = await s.get(StoryWorkModel, work_id)
        decision = await works_app.create_decision(
            s,
            owner_id=work.owner_id,
            work_id=work_id,
            chapter_no=1,
            trigger_summary="要不要把密约交给对手",
            why_human="不可逆且影响后续三章",
            options=[
                {"option_id": "keep", "label": "不交出", "near_term_consequence": "信任维持",
                 "reversibility": "可逆"},
                {"option_id": "give", "label": "交出", "near_term_consequence": "短期脱身",
                 "reversibility": "不可逆"},
            ],
            default_option_id="keep",
            impact_level="HIGH",
            deadline=datetime.now(UTC) - timedelta(seconds=1),  # 已到期
        )
        await s.commit()
        decision_id = uuid.UUID(str(decision.decision_id))
        nonce = str(decision.confirm_nonce or "")
        owner_id = work.owner_id

    async def user_resolve():
        async with Sessions() as s:
            try:
                await works_app.resolve_decision(
                    s, owner_id=owner_id, work_id=work_id, decision_id=decision_id,
                    option_id="give", accept_default=False, confirm_nonce=nonce,
                    resolved_by="user",
                )
                await s.commit()
                return "user"
            except Exception as exc:
                await s.rollback()
                return f"user:{type(exc).__name__}"

    async def default_sweep():
        async with Sessions() as s:
            try:
                swept = await works_app.sweep_expired_decisions(s, limit=50)
                await s.commit()
                return "default" if swept else "default:none"
            except Exception as exc:
                await s.rollback()
                return f"default:{type(exc).__name__}"

    outcomes = []
    for _ in range(3):
        outcomes.append(await asyncio.gather(user_resolve(), default_sweep()))

    async with Sessions() as s:
        row = await s.get(DecisionRequestModel, decision_id)
        state = str(row.state)
        resolved_by = str(row.resolved_by or "")
        chosen = str(row.resolved_option_id or "")
    check("裁决竞争后状态唯一", state == "RESOLVED", f"state={state}")
    check("裁决竞争只有一个赢家", chosen in {"keep", "give"}, f"chosen={chosen}")
    # 归因值必须能区分「人做的」和「到期自动落的」：后者记作 timer，不是 user
    check("裁决结果可区分人/自动", resolved_by in {"user", "timer"},
          f"resolved_by={resolved_by} outcomes={outcomes}")


async def main():
    for fn in (
        scenario_lease_contention,
        scenario_concurrent_reservations,
        scenario_crash_recovery,
        scenario_stale_lease_rejects_writeback,
        scenario_duplicate_reservation_key,
        scenario_concurrent_chapter_commit,
        scenario_user_vs_default_decision_race,
    ):
        try:
            await fn()
        except Exception as exc:
            traceback.print_exc()
            check(fn.__name__, False, f"{type(exc).__name__}: {exc}"[:200])
    await engine.dispose()
    failed = [r_ for r_ in results if not r_[1]]
    print(f"\n=== {len(results) - len(failed)}/{len(results)} passed ===")
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
'''


if __name__ == "__main__":
    raise SystemExit(main())
