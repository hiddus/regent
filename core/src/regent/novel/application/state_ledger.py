"""状态账本的写入口：谁有资格进册、值从哪来、当前态怎么投影。

**为什么不在 CANON 事务里写。** 账本要发一次抽取调用，而调用要走 `CallBroker`
预留；预留在另一条连接上对作品行加锁，CANON 事务此刻正握着那把锁——两边互等到
超时（本地已被这个死锁坑过一次，见 2026-09-10 缺陷 5）。所以账本写在**提交之后**、
用**自己的会话**：主事务先落地，账本再作为一个独立事务跑，失败只回滚它自己。

这一条同时解决另外两件事：

- **没有半账**：一次处理 = 一个事务，要么一条流水不留，要么全部留下。
- **故障可留证**：抽取失败、额度不足、数据库异常分别记在 `novel_state_runs` 上，
  而不是混成一句「没写成功」。

**当前态由流水投影。** 不增量改 `slots`，而是在每次写入后对受影响的 (实体, 槽位)
按 (章, 序) 重算「最新一条未被作废的 verified 变更」。这样两件事自动成立：
旧章重放不会倒灌（第 5 章重算时，第 30 章那条还是最新的）；同章改稿只要把旧流水
标成 `superseded`，当前态自己就回到新稿的结论，不需要额外的补偿逻辑。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.novel.domain import state_ledger as rules
from regent.novel.infrastructure.models import (
    CanonCommitModel,
    ChapterRunModel,
    PersonaSpecModel,
    StateChangeModel,
    StateEntityModel,
    StateRunModel,
    StateSlotModel,
    StoryWorkModel,
)

_log = logging.getLogger(__name__)

MODE_KEY = "NOVEL_STATE_LEDGER"
WORKS_KEY = "NOVEL_STATE_LEDGER_WORK_IDS"
MODE_OFF = "off"
MODE_SHADOW = "shadow"
# 一章最多试两次：第一次失败多半是瞬时故障；两次都失败就是真的读不懂这一章，
# 继续重试只是重复计费。
MAX_ATTEMPTS = 2
# 失败要退避，不能每个 tick 重打一次：这一章的内容不会自己变好。
RETRY_BACKOFF_SECONDS = 300
# 额度不足不是这一章的错，等额度恢复就该继续；但也不能无限重试，故单独给上界与退避。
BUDGET_MAX_ATTEMPTS = 8
BUDGET_BACKOFF_SECONDS = 3600
RETRYABLE = ("failed", "unknown")

# 单章处理时限。没有上限时，一个卡住的抽取调用会把整个 tick 挂死，而账本钩子
# 挂在导演链之后——tick 挂死等于后面的章节生产也停了。超时按失败处理并退避，
# 不是「跳过」：跳过会让这一章永远排在队首反复重来。
CHAPTER_TIMEOUT_KEY = "NOVEL_LEDGER_CHAPTER_TIMEOUT"
DEFAULT_CHAPTER_TIMEOUT = 180
# 一轮 tick 的总时限。到点就停，把控制权还给主循环——账本是旁路，不能把
# 正文生产挤到超时。
TICK_BUDGET_KEY = "NOVEL_LEDGER_TICK_BUDGET"
DEFAULT_TICK_BUDGET = 240


def _env_seconds(key: str, default: int) -> int:
    """读一个正整数秒数。坏值退回缺省而不是报错：配置写错不该让整条链路停摆。"""
    raw = (os.environ.get(key) or "").strip()
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default

STATE_EXTRACT_SYSTEM = (
    "读这一章正文，记下这一章里东西换了手、或者被用掉、毁掉的地方。\n"
    "每条写四样：entity 是那个东西的名字，用正文里的叫法；"
    "slot 写 holder（东西现在在谁手上）或 lifecycle（它还是完好的，还是被用掉、被毁掉了）；"
    "value 在 holder 时写那个人名，在 lifecycle 时写 intact、consumed 或 destroyed；"
    "quote 从正文里原样连续抄一句，不要改写、不要拼接。\n"
    "东西交给另一个人算 holder，不算 lifecycle。这一章没写到的不要补，推断出来的不要写。"
    "没有就返回空列表。"
)


class StateChangeItem(BaseModel):
    entity: str = Field(description="那个东西的名字，用正文里的叫法")
    slot: Literal["holder", "lifecycle"] = Field(description="holder 或 lifecycle")
    value: str = Field(
        description="holder 写那个人名；lifecycle 写 intact / consumed / destroyed"
    )
    quote: str = Field(description="正文里逐字连续的一句")


class StateChanges(BaseModel):
    changes: list[StateChangeItem] = Field(default_factory=list)


def ledger_mode() -> str:
    """当前模式。缺省 off——开关不在坡道上，就在坡底。"""
    raw = (os.environ.get(MODE_KEY) or "").strip().lower()
    return MODE_SHADOW if raw in (MODE_SHADOW, "on", "1", "true") else MODE_OFF


def allowed_work_ids() -> set[str]:
    """影子期允许写入的作品（空 = 不限）。扩大批次前必须先在这里点名。"""
    raw = (os.environ.get(WORKS_KEY) or "").strip()
    return {item.strip() for item in raw.split(",") if item.strip()}


def content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _backoff_seconds(status: str, attempts: int) -> int:
    """再试之前要等多久。预算不足等得更久——额度不会几秒就恢复。"""
    if status == "budget":
        # 越等越久，但不超过一小时的一次重试间隔。
        return min(BUDGET_BACKOFF_SECONDS * max(1, attempts), 4 * BUDGET_BACKOFF_SECONDS)
    return RETRY_BACKOFF_SECONDS * max(1, int(attempts))


def retry_due(row: Any, *, now: int | None = None) -> bool:
    """这条处理记录还该不该再跑。

    「跑过就不跑」对空结果是对的（重算=重复计费），对失败和额度不足是错的：
    前者等退避，后者等额度。两者都不该被永久钉死，也不该每 tick 重打一次。
    """
    status = str(getattr(row, "status", "") or "")
    attempts = int(getattr(row, "attempts", 0) or 0)
    if status in RETRYABLE:
        if attempts >= MAX_ATTEMPTS:
            return False
    elif status == "budget":
        if attempts >= BUDGET_MAX_ATTEMPTS:
            return False
    else:
        return False
    due_at = int(getattr(row, "retry_after", 0) or 0)
    return due_at <= int(time.time() if now is None else now)


def run_reasons(settled: rules.Settled) -> dict[str, int]:
    """处理记录里的原因计数。

    拒收与降级分开记：前者是「这条连引文都对不上」，后者是「引文对得上但方向判不了」。
    两个都归进「候选」就再也分不开了——而它们要改的地方完全不同（改抽取 / 补判据）。
    """
    out: dict[str, int] = dict(settled.reasons)
    for key, count in settled.downgrades.items():
        out[f"downgrade:{key}"] = int(count)
    return out


def _next_retry_at(status: str, attempts: int) -> int:
    """成功/空结果/全被拒 → 0（不再重跑）；失败与额度不足 → 一个绝对时间点。

    用 epoch 秒而不是 timestamp：这条判据要在 sqlite 单测和 Postgres 上给出同一个答案，
    而两边的时区处理方式不一样——拿时间戳比大小会在其中一边悄悄错位。
    """
    if status in RETRYABLE or status == "budget":
        return int(time.time()) + _backoff_seconds(status, attempts)
    return 0


async def _persons(session: AsyncSession, work_id: uuid.UUID) -> dict[str, str]:
    """卡司归一表：归一名 → 卡司里的写法。人物只能从角色表来。"""
    rows = list(
        (
            await session.scalars(
                select(PersonaSpecModel.name).where(PersonaSpecModel.work_id == work_id)
            )
        ).all()
    )
    table: dict[str, str] = {}
    for name in rows:
        key = rules.normalize_name(name)
        if key:
            table.setdefault(key, str(name))
    return table


async def _registered(
    session: AsyncSession, *, work_id: uuid.UUID, branch_id: uuid.UUID
) -> set[str]:
    rows = list(
        (
            await session.scalars(
                select(StateEntityModel.entity).where(
                    StateEntityModel.work_id == work_id,
                    StateEntityModel.branch_id == branch_id,
                )
            )
        ).all()
    )
    return {rules.normalize_name(name) for name in rows if name}


def _register(
    session: AsyncSession,
    *,
    work_id: uuid.UUID,
    branch_id: uuid.UUID,
    chapter_no: int,
    known: set[str],
    candidates: list[tuple[str, str, str]],
) -> int:
    """登记新名字。``candidates`` 是 ``(名字, 来源, 原句)``。返回登记条数。

    **按名字排序后再插。** 真机并发实测：两个 worker 各自插一批有新有旧的名字，
    插入顺序不同，各自握住对方要的那个唯一索引项，撞成 ``DeadlockDetected``
    （`uq_novel_state_entity`）。顺序一致时最坏只是等，不会互等成环。
    作品级互斥已经让这种情况很难发生，这里是第二道：排序的代价是一次 sort。
    """
    added = 0
    for entity, origin, quote in sorted(candidates, key=lambda item: item[0]):
        if not entity or entity in known:
            continue
        session.add(
            StateEntityModel(
                id=uuid.uuid4(),
                work_id=work_id,
                branch_id=branch_id,
                entity=entity,
                kind="object",
                origin=origin,
                first_chapter=int(chapter_no),
                source_quote=str(quote or ""),
            )
        )
        known.add(entity)
        added += 1
    return added


async def _register_safe(
    session: AsyncSession,
    *,
    work_id: uuid.UUID,
    branch_id: uuid.UUID,
    chapter_no: int,
    known: set[str],
    candidates: list[tuple[str, str, str]],
) -> int:
    """登记实体；撞名不算这一章失败。

    并发处理同一本书的不同章时，两个 worker 会各自查一次 `known`——都看不到对方
    还没提交的行——然后同时插入同一个实体名，撞 `uq_novel_state_entity`。单 worker
    跑多少章都不会出现，只有多 worker 才暴露：真机三 worker 跑 6 章时冒出 10 条
    整章失败，note 全是 UniqueViolation。

    撞了就回滚这一批（savepoint），重读一次已存在的名字，再补插剩下的。
    代价是撞名时多两次查询；换来的是「不因为一个重复的名字丢掉整章」——
    丢掉的那一章会被记成 failed 并退避，而它其实完全可以处理。
    """
    items = [item for item in candidates if item[0]]
    if not items:
        return 0
    try:
        async with session.begin_nested():
            added = _register(
                session,
                work_id=work_id,
                branch_id=branch_id,
                chapter_no=chapter_no,
                known=known,
                candidates=items,
            )
            await session.flush()
        return added
    except IntegrityError:
        # 别人抢先登记了同名的名字。重读之后再补剩下的。
        fresh = await _registered(session, work_id=work_id, branch_id=branch_id)
        known.clear()
        known.update(fresh)
        rest = [item for item in items if item[0] not in fresh]
        if not rest:
            return 0
        async with session.begin_nested():
            added = _register(
                session,
                work_id=work_id,
                branch_id=branch_id,
                chapter_no=chapter_no,
                known=known,
                candidates=rest,
            )
            await session.flush()
        return added


async def _reproject(
    session: AsyncSession,
    *,
    work_id: uuid.UUID,
    branch_id: uuid.UUID,
    entity: str,
    slot: str,
) -> int:
    """按流水重算一个槽位的当前值。返回 1=当前态确有改动，0=无值或未变。

    取「最新一条未被作废的 verified 变更」：章号大者胜，同章内按 seq。
    这就是「当前态是流水的投影」——不维护增量，也就不存在增量写歪。

    「未变也返回 1」会把 `projected` 变成「重算了几个槽位」，那是个没有含义的数；
    这里要求**值、章号、来源流水、争议章**四者都相同才算未变，冲突章因此不会虚增投影数。

    **争议章要比值更新才记。** 冲突行不投影，于是当前态会留着上一章的旧值；
    如果不对「这一章有争议」留标记，读起来就和「上一章之后确实没变」完全一样——
    而这两件事必须分得开，否则往后的比对会拿一个已知有争议的值当现状。
    """
    latest = await session.scalar(
        select(StateChangeModel)
        .where(
            StateChangeModel.work_id == work_id,
            StateChangeModel.branch_id == branch_id,
            StateChangeModel.entity == entity,
            StateChangeModel.slot == slot,
            StateChangeModel.status == rules.STATUS_VERIFIED,
        )
        .order_by(StateChangeModel.chapter_no.desc(), StateChangeModel.seq.desc())
        .limit(1)
    )
    disputed = int(
        await session.scalar(
            select(func.coalesce(func.max(StateChangeModel.chapter_no), 0)).where(
                StateChangeModel.work_id == work_id,
                StateChangeModel.branch_id == branch_id,
                StateChangeModel.entity == entity,
                StateChangeModel.slot == slot,
                StateChangeModel.status == rules.STATUS_CONFLICT,
            )
        )
        or 0
    )
    # 只记「比当前值更新」的争议：更早那一章的争议已经被后来的值回答了。
    conflict_chapter = disputed if disputed > int(latest.chapter_no if latest else 0) else 0
    row = await session.scalar(
        select(StateSlotModel).where(
            StateSlotModel.work_id == work_id,
            StateSlotModel.branch_id == branch_id,
            StateSlotModel.entity == entity,
            StateSlotModel.slot == slot,
        )
    )
    if latest is None:
        if row is not None:
            row.value = ""
            row.as_of_chapter = 0
            row.conflict_chapter = conflict_chapter
            row.source_quote = ""
            row.source_change_id = None
            return 0
        if conflict_chapter:
            # 从来没有过 verified 值、但这一章有争议：也要留一行。否则这条槽位在
            # 当前态里根本不存在，读的人连「这里有个说不清的东西」都看不到。
            session.add(
                StateSlotModel(
                    id=uuid.uuid4(),
                    work_id=work_id,
                    branch_id=branch_id,
                    entity=entity,
                    slot=slot,
                    value="",
                    as_of_chapter=0,
                    conflict_chapter=conflict_chapter,
                    source_quote="",
                    origin="settled",
                    source_change_id=None,
                )
            )
            return 1
        return 0
    if row is None:
        session.add(
            StateSlotModel(
                id=uuid.uuid4(),
                work_id=work_id,
                branch_id=branch_id,
                entity=entity,
                slot=slot,
                value=latest.to_value,
                as_of_chapter=latest.chapter_no,
                conflict_chapter=conflict_chapter,
                source_quote=latest.source_quote,
                origin="settled",
                source_change_id=latest.id,
            )
        )
        return 1
    if (
        row.value == latest.to_value
        and int(row.as_of_chapter) == int(latest.chapter_no)
        and row.source_change_id == latest.id
        and int(row.conflict_chapter) == conflict_chapter
    ):
        return 0
    row.value = latest.to_value
    row.as_of_chapter = latest.chapter_no
    row.conflict_chapter = conflict_chapter
    row.source_quote = latest.source_quote
    row.source_change_id = latest.id
    row.origin = "settled"
    return 1


async def _supersede(session: AsyncSession, *, work_id, branch_id, chapter_no, keep_hash) -> list:
    """同章旧稿的流水作废（保留行，改状态）。返回受影响的 (实体, 槽位)。"""
    rows = list(
        (
            await session.scalars(
                select(StateChangeModel).where(
                    StateChangeModel.work_id == work_id,
                    StateChangeModel.branch_id == branch_id,
                    StateChangeModel.chapter_no == int(chapter_no),
                    StateChangeModel.source_hash != keep_hash,
                    StateChangeModel.status != rules.STATUS_SUPERSEDED,
                )
            )
        ).all()
    )
    touched = []
    for row in rows:
        row.status = rules.STATUS_SUPERSEDED
        row.projected = False
        touched.append((row.entity, row.slot))
    return touched


async def record_chapter(
    session: AsyncSession,
    *,
    provider: Any,
    run: ChapterRunModel,
    work: StoryWorkModel,
) -> dict[str, Any]:
    """处理一章。调用方负责给它一个**独立会话**，这里不 commit。

    幂等由 ``novel_state_runs`` 保证：同一 (作品, 分支, 章, 稿件哈希) 处理过一次
    就不会再调用模型——空结果也一样，否则「没有状态变化」的章会被反复重算。
    """
    mode = ledger_mode()
    if mode == MODE_OFF:
        return {"status": "off"}
    if allowed_work_ids() and str(work.id) not in allowed_work_ids():
        return {"status": "skipped", "note": "work-not-allowed"}

    chapter_no = int(run.chapter_no)
    # 写入必须用**这一章自己的分支**。作品上有一个「当前分支」，但被处理的这一章
    # 可能属于分叉后的另一条线：拿作品的分支写，等于把分叉里的状态记进主线，
    # 而两条线此后都按自己的当前态去判后续章节——错了也查不出是哪一步错的。
    branch_id = run.branch_id
    content = str(run.content or "")
    digest = content_hash(content)
    if not content.strip():
        return {"status": "empty", "note": "no-prose"}

    previous = await session.scalar(
        select(StateRunModel).where(
            StateRunModel.work_id == work.id,
            StateRunModel.branch_id == branch_id,
            StateRunModel.chapter_no == chapter_no,
            StateRunModel.source_hash == digest,
        )
    )
    if previous is not None and not retry_due(previous):
        return {"status": "skipped", "note": f"already-{previous.status}"}

    # 改稿：新稿一进来，旧稿的结论就不再描述现在这本书，**先失效、再重算**。
    # 顺序反过来就有个窗口——旧稿的值还挂在当前态上，而它描述的那段文字已经不存在了，
    # 这期间任何比对都会拿一个假的「现状」去判新章。重算失败也不回填旧值：
    # 那时候诚实的答案是「不知道」，不是「还是老样子」。
    stale = await _supersede(
        session,
        work_id=work.id,
        branch_id=branch_id,
        chapter_no=chapter_no,
        keep_hash=digest,
    )
    for stale_entity, stale_slot in sorted(stale):
        await _reproject(
            session,
            work_id=work.id,
            branch_id=branch_id,
            entity=stale_entity,
            slot=stale_slot,
        )

    production = (run.generation_context or {}).get("production") or {}
    plan = production.get("chapter_plan") or (run.generation_context or {}).get(
        "target_node"
    ) or {}
    facts = list((run.generation_context or {}).get("verified_facts") or [])

    persons = await _persons(session, work.id)
    known = await _registered(session, work_id=work.id, branch_id=branch_id)

    # ① 章纲声明打底。声明不是状态，所以 source_quote 留空——不伪造证据。
    declared_new = await _register_safe(
        session,
        work_id=work.id,
        branch_id=branch_id,
        chapter_no=chapter_no,
        known=known,
        candidates=[
            (name, "plan", "") for name in rules.declared_objects(plan) if name not in persons
        ],
    )
    # ② 结算事件兜底：名字必须出现在它自己的原句里。
    facts_new = await _register_safe(
        session,
        work_id=work.id,
        branch_id=branch_id,
        chapter_no=chapter_no,
        known=known,
        candidates=[
            (entity, "settled", quote)
            for entity, quote in rules.facts_entities(facts, persons=set(persons))
            if entity not in known
        ],
    )

    status = "ok"
    note = ""
    cost_minor = 0
    raw_changes: list[Any] = []
    cost_before = int(production.get("actual_minor", 0) or 0)
    try:
        from regent.novel.application.directing_calls import _call

        output = await _call(
            session,
            provider,
            work,
            run,
            production,
            StateChanges,
            STATE_EXTRACT_SYSTEM,
            {"chapter": content},
            "state_ledger",
            f"v{int(getattr(run, 'input_version', 1) or 1)}:ledger:{digest[:16]}",
            # 这条会话不持有作品行锁（账本跑在正史提交之后），所以不必先提交再预留，
            # 反过来用它保住了「一次处理 = 一个事务」。
            commit_before_call=False,
        )
        raw_changes = [item.model_dump() for item in (output.changes or [])]
        # 只记这一次抽取的成本：production 里的是整章累计。
        cost_minor = int(production.get("actual_minor", 0) or 0) - cost_before
    except Exception as exc:  # noqa: BLE001 — 三类故障分开留证，不混成一句
        kind = type(exc).__name__
        if _is_lock_conflict(exc):
            # 配额预留要作品行的 FOR UPDATE，而正文生产正握着它——等锁超时不是
            # 这一章的错，也不该留处理记录：留下就会进退避、消耗 attempts，
            # 退避到上限把这一章永久放弃。原话是「还轮不到我」。
            _log.info(
                "state ledger extract contended work=%s chapter=%s（让给下一轮）",
                work.id,
                chapter_no,
            )
            return {"status": "contended", "note": "lock-conflict", "changes": 0}
        if kind == "BudgetExhausted":
            status = "budget"
        elif kind in ("CallUnknown",):
            status = "unknown"
        else:
            status = "failed"
        note = f"{kind}: {str(exc)[:180]}"
        _log.warning(
            "state ledger extract %s work=%s chapter=%s", status, work.id, chapter_no
        )

    settled = rules.settle_changes(raw_changes, content=content, persons=persons)

    # 写流水之前先取一次当前态：``from_value`` 要记「改之前是什么」，
    # 事后重算出来的值给不了这个信息。
    entity_names = sorted({c["entity"] for c in settled.changes})
    before = {
        (row.entity, row.slot): row.value
        for row in (
            (
                await session.scalars(
                    select(StateSlotModel).where(
                        StateSlotModel.work_id == work.id,
                        StateSlotModel.branch_id == branch_id,
                        StateSlotModel.entity.in_(entity_names),
                    )
                )
            ).all()
            if entity_names
            else []
        )
    }

    written = 0
    for change in settled.changes:
        session.add(
            StateChangeModel(
                id=uuid.uuid4(),
                work_id=work.id,
                branch_id=branch_id,
                chapter_no=chapter_no,
                source_hash=digest,
                entity=change["entity"],
                slot=change["slot"],
                from_value=before.get((change["entity"], change["slot"]), ""),
                to_value=change["value"],
                source_quote=change["quote"],
                origin="settled",
                status=change["status"],
                projected=False,
                evidence=change["evidence"],
                downgrade=str(change.get("downgrade") or ""),
                seq=int(change["seq"]),
                value_hash=change["value_hash"],
            )
        )
        written += 1

    projected = 0
    # 传入**全部**变更（含 candidate / conflict），由 `_reproject` 里的 verified 过滤
    # 单独把关。「当前态只能来自 verified」这条只留一个闸口：多一个闸口，就多一条
    # 以后能被绕过、且绕过时没人发现的路。
    touched = {(c["entity"], c["slot"]) for c in settled.changes}
    touched.update(stale)
    for entity, slot in sorted(touched):
        projected += await _reproject(
            session, work_id=work.id, branch_id=branch_id, entity=entity, slot=slot
        )

    # 标记「哪条流水是当前态的来源」。projected 只是审计字段，不参与判断；
    # 但它让「当前值从哪来」在表里一眼可见。
    if touched:
        await session.flush()
        sources = {
            (row.entity, row.slot): row.source_change_id
            for row in (
                await session.scalars(
                    select(StateSlotModel).where(
                        StateSlotModel.work_id == work.id,
                        StateSlotModel.branch_id == branch_id,
                        StateSlotModel.entity.in_([item[0] for item in touched]),
                    )
                )
            ).all()
        }
        written_rows = list(
            (
                await session.scalars(
                    select(StateChangeModel).where(
                        StateChangeModel.work_id == work.id,
                        StateChangeModel.branch_id == branch_id,
                        StateChangeModel.chapter_no == chapter_no,
                        StateChangeModel.source_hash == digest,
                    )
                )
            ).all()
        )
        for row in written_rows:
            row.projected = sources.get((row.entity, row.slot)) == row.id

    # 「这一章真没有状态变化」与「抽到的全被拒」是两件事：前者是正常结果，
    # 后者说明这一章的状态没进账（可能要改抽取），混成一个数会让覆盖率看起来更好。
    if status == "ok" and not settled.changes:
        status = "rejected" if settled.rejected else "empty"

    if previous is None:
        session.add(
            StateRunModel(
                id=uuid.uuid4(),
                work_id=work.id,
                branch_id=branch_id,
                chapter_no=chapter_no,
                source_hash=digest,
                status=status,
                attempts=1,
                entities=declared_new + facts_new,
                changes=written,
                projected=projected,
                rejected=settled.rejected,
                conflicts=settled.conflicts,
                reasons=run_reasons(settled),
                cost_minor=cost_minor,
                note=note,
                retry_after=_next_retry_at(status, 1),
            )
        )
    else:
        previous.status = status
        previous.attempts = int(previous.attempts) + 1
        previous.entities = declared_new + facts_new
        previous.changes = written
        previous.projected = projected
        previous.rejected = settled.rejected
        previous.conflicts = settled.conflicts
        previous.reasons = run_reasons(settled)
        previous.cost_minor = cost_minor
        previous.note = note
        previous.retry_after = _next_retry_at(status, int(previous.attempts))
        previous.updated_at = func.now()

    return {
        "status": status,
        "entities": declared_new + facts_new,
        "changes": written,
        "projected": projected,
        "rejected": settled.rejected,
        "conflicts": settled.conflicts,
        "reasons": run_reasons(settled),
        "note": note,
    }


def _pending_query(
    *, work_id: uuid.UUID | None = None, allowed: set[uuid.UUID] | None = None
):
    """「该处理」的章节集合（不含排序与截断），供列举与计数共用。"""
    latest = (
        select(
            CanonCommitModel.work_id,
            CanonCommitModel.branch_id,
            CanonCommitModel.chapter_no,
            CanonCommitModel.source_hash,
            func.row_number()
            .over(
                partition_by=(
                    CanonCommitModel.work_id,
                    CanonCommitModel.branch_id,
                    CanonCommitModel.chapter_no,
                ),
                order_by=CanonCommitModel.version.desc(),
            )
            .label("rn"),
        )
        .subquery()
    )
    now = int(time.time())
    run = StateRunModel
    retryable = or_(
        and_(
            run.status.in_(RETRYABLE),
            run.attempts < MAX_ATTEMPTS,
            run.retry_after <= now,
        ),
        and_(
            run.status == "budget",
            run.attempts < BUDGET_MAX_ATTEMPTS,
            run.retry_after <= now,
        ),
    )
    stmt = (
        select(latest.c.work_id, latest.c.branch_id, latest.c.chapter_no, latest.c.source_hash)
        .select_from(latest)
        .outerjoin(
            run,
            and_(
                run.work_id == latest.c.work_id,
                run.branch_id == latest.c.branch_id,
                run.chapter_no == latest.c.chapter_no,
                run.source_hash == latest.c.source_hash,
            ),
        )
        .where(latest.c.rn == 1)
        .where(or_(run.id.is_(None), retryable))
    )
    if work_id is not None:
        stmt = stmt.where(latest.c.work_id == work_id)
    if allowed:
        stmt = stmt.where(latest.c.work_id.in_(list(allowed)))
    return stmt


async def pending_chapters(
    session: AsyncSession,
    *,
    limit: int = 1,
    work_id: uuid.UUID | None = None,
    allowed: set[uuid.UUID] | None = None,
) -> list[tuple[uuid.UUID, uuid.UUID, int, str]]:
    """还没处理（或到了重试时间）的章节：按 canon commit 的 source_hash 对齐。

    用 commit 的哈希而不是自己算正文哈希，是因为**同章改稿**会产生一条新 commit：
    按 commit 对齐，改稿自然变成一条新的待处理记录，不需要另写失效逻辑。

    ``work_id`` 用于按作品回放：不给就是全库最早的一条（worker 的用法），
    给了就只在该作品内推进——不能拿全库顺序去凑「某部作品的前 20 章」。

    ``allowed`` 是影子期的作品白名单。**必须在 limit 之前生效**：先取全库最早一条、
    再在写入时判「不在白名单」，会让 tick 永远停在别人的第一章上，一步也不往前。

    ``work_id`` 不给时**每部作品只返回一条**（它自己最早那章）。按章号全局排序会让
    候选池整池落在一部作品上：三个 worker 抢同一部作品的相邻三章，而账本对同一部
    作品是互斥的（见 `_work_guard`），于是两个必然空手——真机实测就是这样退化成一
    个 worker 在干活。每作品一条，候选池才有得分散。
    """
    stmt = _pending_query(work_id=work_id, allowed=allowed)
    if work_id is None:
        inner = stmt.subquery()
        ranked = (
            select(
                inner.c.work_id,
                inner.c.branch_id,
                inner.c.chapter_no,
                inner.c.source_hash,
                func.row_number()
                .over(
                    partition_by=inner.c.work_id,
                    order_by=inner.c.chapter_no,
                )
                .label("wrn"),
            )
            .subquery()
        )
        stmt = (
            select(ranked.c.work_id, ranked.c.branch_id, ranked.c.chapter_no, ranked.c.source_hash)
            .where(ranked.c.wrn == 1)
            .order_by(ranked.c.chapter_no)
        )
    else:
        stmt = stmt.order_by(stmt.selected_columns.chapter_no)
    stmt = stmt.limit(limit)
    rows = (await session.execute(stmt)).all()
    return [(row[0], row[1], int(row[2]), str(row[3])) for row in rows]


async def _latency(
    session: AsyncSession,
    *,
    work_id: uuid.UUID | None = None,
    work_ids: set[uuid.UUID] | None = None,
    sample: int = 200,
) -> dict[str, int]:
    """最近若干章的处理耗时。只取 ``duration_ms > 0``：0 是「没测过」，不是「很快」。

    用最近 200 条而不是全量：判据改过、模型换过之后，三个月前的耗时没有参考价值，
    把它算进 P95 会让一条正在发生的退化被历史数据抹平。
    """
    stmt = (
        select(StateRunModel.duration_ms)
        .where(StateRunModel.duration_ms > 0)
        .order_by(StateRunModel.chapter_no.desc())
        .limit(sample)
    )
    if work_id is not None:
        stmt = stmt.where(StateRunModel.work_id == work_id)
    elif work_ids:
        stmt = stmt.where(StateRunModel.work_id.in_(list(work_ids)))
    values = sorted(int(v) for v in (await session.scalars(stmt)).all())
    if not values:
        return {"p50": 0, "p95": 0, "max": 0, "samples": 0}
    return {
        "p50": values[len(values) // 2],
        "p95": values[min(len(values) - 1, int(len(values) * 0.95))],
        "max": values[-1],
        "samples": len(values),
    }


async def _superseded(
    session: AsyncSession,
    *,
    work_id: uuid.UUID | None = None,
    work_ids: set[uuid.UUID] | None = None,
) -> int:
    """改稿作废掉的流水条数。这个数在涨，说明这本书在返工，不是在推进。"""
    stmt = select(func.count()).where(
        StateChangeModel.status == rules.STATUS_SUPERSEDED
    )
    if work_id is not None:
        stmt = stmt.where(StateChangeModel.work_id == work_id)
    elif work_ids:
        stmt = stmt.where(StateChangeModel.work_id.in_(list(work_ids)))
    return int(await session.scalar(stmt) or 0)


async def ledger_stats(
    session: AsyncSession,
    *,
    work_id: uuid.UUID | None = None,
    work_ids: set[uuid.UUID] | None = None,
) -> dict:
    """看一眼就知道要不要管：积压多少、卡在哪一类、花了多少。

    没有这组数，影子期只能靠「日志里有没有 exception」判断健康——而账本最典型的
    不健康不是报错，是**安静地什么都不做**（白名单挡住、全部被拒、额度耗尽）。

    ``work_ids`` 是影子期的白名单口径：**四个数必须和「这次能处理的范围」一致**，
    否则日志上写着「积压 1600 章」，看起来毫无进展，其实那 1600 章根本不在允许范围。
    """
    scope_work = work_id
    base = _pending_query(work_id=work_id, allowed=work_ids)
    backlog = int(
        await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    )
    runs = select(StateRunModel.status, func.count()).group_by(StateRunModel.status)
    if scope_work is not None:
        runs = runs.where(StateRunModel.work_id == scope_work)
    elif work_ids:
        runs = runs.where(StateRunModel.work_id.in_(list(work_ids)))
    by_status = {
        str(status): int(count) for status, count in (await session.execute(runs)).all()
    }
    cost_stmt = select(func.coalesce(func.sum(StateRunModel.cost_minor), 0))
    if scope_work is not None:
        cost_stmt = cost_stmt.where(StateRunModel.work_id == scope_work)
    elif work_ids:
        cost_stmt = cost_stmt.where(StateRunModel.work_id.in_(list(work_ids)))
    cost = int(await session.scalar(cost_stmt) or 0)
    waiting_stmt = select(func.count()).where(
        StateRunModel.retry_after > int(time.time()),
        StateRunModel.status.in_(RETRYABLE + ("budget",)),
    )
    if scope_work is not None:
        waiting_stmt = waiting_stmt.where(StateRunModel.work_id == scope_work)
    elif work_ids:
        waiting_stmt = waiting_stmt.where(StateRunModel.work_id.in_(list(work_ids)))
    waiting = int(await session.scalar(waiting_stmt) or 0)
    return {
        "mode": ledger_mode(),
        "backlog": backlog,
        "by_status": by_status,
        "cost_minor": cost,
        "waiting_retry": waiting,
        "latency_ms": await _latency(session, work_id=scope_work, work_ids=work_ids),
        "superseded": await _superseded(session, work_id=scope_work, work_ids=work_ids),
    }


async def reproject_pairs(
    session: AsyncSession,
    *,
    work_id: uuid.UUID,
    branch_id: uuid.UUID,
    pairs: Any,
) -> int:
    """按流水重算一批槽位（``(实体, 槽位)``）。返回真正变了的行数。

    判据改过之后必须跑一次：当前态是**按当时的判据**投影出来的，判据收紧了，
    旧投影还在，读的人看到的还是旧答案。这不是「重建缓存」，是让当前态追上判据。
    """
    moved = 0
    for entity, slot in sorted({(str(e), str(s_)) for e, s_ in pairs}):
        moved += await _reproject(
            session, work_id=work_id, branch_id=branch_id, entity=entity, slot=slot
        )
    return moved


async def list_conflicts(
    session: AsyncSession, *, work_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    """把待裁决的冲突列出来：一条争议 = 同章同槽位的两个值，谁也没被投影。

    没有这个出口，冲突只是一堆躺在表里的行：`conflicts` 计数会涨，但没人知道
    该裁决哪一条、裁决了有没有生效。
    """
    stmt = (
        select(StateChangeModel)
        .where(StateChangeModel.status == rules.STATUS_CONFLICT)
        .order_by(
            StateChangeModel.work_id,
            StateChangeModel.chapter_no,
            StateChangeModel.entity,
            StateChangeModel.slot,
            StateChangeModel.seq,
        )
    )
    if work_id is not None:
        stmt = stmt.where(StateChangeModel.work_id == work_id)
    out: list[dict[str, Any]] = []
    for row in (await session.scalars(stmt)).all():
        out.append(
            {
                "id": str(row.id),
                "work_id": str(row.work_id),
                "branch_id": str(row.branch_id),
                "chapter_no": int(row.chapter_no),
                "entity": row.entity,
                "slot": row.slot,
                "value": row.to_value,
                "quote": row.source_quote,
                "seq": int(row.seq),
            }
        )
    return out


async def resolve_conflict(
    session: AsyncSession,
    *,
    work_id: uuid.UUID,
    branch_id: uuid.UUID,
    entity: str,
    slot: str,
    chosen_value: str,
    actor: str,
) -> dict[str, Any]:
    """裁决一条冲突：采纳其中一个值，其余标作废，然后重投影。

    为什么必须有这条路：冲突隔离的是「两个都站得住」，不是「永远不定」。隔离了
    却没有裁决口，那条槽位就会永远停在上一章的旧值上，而且没人有机会改。裁决要
    留人（``origin='corrected'`` + 谁裁的写在 evidence），因为这一步是人在改机器
    不敢定的账——它是**不可逆的**，事后必须能答出「谁改的、改成什么、依据是什么」。

    只处理「该槽位最新那一章的冲突」：更早章节的争议已经被后来的章回答了，
    对它裁决等于往回改历史。
    """
    rows = list(
        (
            await session.scalars(
                select(StateChangeModel).where(
                    StateChangeModel.work_id == work_id,
                    StateChangeModel.branch_id == branch_id,
                    StateChangeModel.entity == entity,
                    StateChangeModel.slot == slot,
                    StateChangeModel.status == rules.STATUS_CONFLICT,
                )
            )
        ).all()
    )
    if not rows:
        return {"status": "none", "note": "no-conflict"}
    latest_chapter = max(int(row.chapter_no) for row in rows)
    rows = [row for row in rows if int(row.chapter_no) == latest_chapter]
    winners = [row for row in rows if row.to_value == chosen_value]
    if not winners:
        raise ValueError(
            f"conflict has no value {chosen_value!r}; choices="
            + ", ".join(sorted({row.to_value for row in rows}))
        )
    winner = winners[0]
    for row in rows:
        if row is winner:
            row.status = rules.STATUS_VERIFIED
            row.origin = "corrected"
            row.evidence = f"resolved-by:{actor}"[:200]
        else:
            row.status = rules.STATUS_SUPERSEDED
            row.projected = False
    await session.flush()
    moved = await _reproject(
        session, work_id=work_id, branch_id=branch_id, entity=entity, slot=slot
    )
    return {
        "status": "resolved",
        "chapter_no": latest_chapter,
        "value": chosen_value,
        "dropped": len(rows) - 1,
        "reprojected": moved,
    }


async def _claim(
    session: AsyncSession,
    *,
    work_id: uuid.UUID,
    branch_id: uuid.UUID,
    chapter_no: int,
    digest: str,
) -> bool:
    """认领这一章：拿 canon commit 那行的行锁，拿不到说明别的 worker 正在处理。

    三个 worker 同时勾这个函数时，没有认领就会并发抽同一章：重复计费，而且两边都写
    `novel_state_runs` 会撞唯一键——输的那一方还会把赢的那条 `ok` 记录改成 `failed`。
    锁随事务结束释放，进程崩溃也不会留下永久占位，所以不需要另做租约超时。

    sqlite 没有行锁（单测串行跑），此时退化为「不做并发保护」，见 `_supports_row_lock`。
    """
    stmt = (
        select(CanonCommitModel.id)
        .where(
            CanonCommitModel.work_id == work_id,
            CanonCommitModel.branch_id == branch_id,
            CanonCommitModel.chapter_no == chapter_no,
            CanonCommitModel.source_hash == digest,
        )
        .limit(1)
    )
    if _supports_row_lock(session):
        stmt = stmt.with_for_update(skip_locked=True)
    return await session.scalar(stmt) is not None


def _supports_row_lock(session: AsyncSession) -> bool:
    dialect = getattr(getattr(session, "bind", None), "dialect", None)
    return getattr(dialect, "name", "") == "postgresql"


# 同一部作品同一时刻只允许一个账本 worker 在处理。命名空间取 "LEDG" 的 ASCII，
# 与业务行锁完全无关——这一点是刻意的，见 `_work_guard`。
_ADVISORY_NS = 0x4C454447


def _advisory_key(work_id: uuid.UUID) -> int:
    """把作品 id 收成 int4 咨询锁键（Postgres 的双 int4 形式）。"""
    raw = int.from_bytes(uuid.UUID(str(work_id)).bytes[:4], "big", signed=False)
    return raw - 2**32 if raw >= 2**31 else raw


def _engine_of(factory: async_sessionmaker) -> Any:
    bind = getattr(factory, "kw", {}).get("bind") if hasattr(factory, "kw") else None
    return bind or getattr(factory, "bind", None)


@asynccontextmanager
async def _work_guard(factory: async_sessionmaker, work_id: uuid.UUID) -> AsyncIterator[bool]:
    """作品级互斥：拿不到就返回 False，让调用方换一部作品，而不是排队等。

    **为什么必须是作品粒度**（真机 2026-10-28 三 worker 实测）：

    - 配额预留会对作品行 ``FOR UPDATE``（见 `ledger.reserve` 的 P0-5），而账本事务
      跨越分钟级模型调用。两个 worker 处理**同一部作品的不同章节**时，后者的预留
      要等前者的事务提交，等到 ``lock_timeout``（线上 2min）被判失败。
      一章就这样被记成 `failed` 并退避——可这一章本身没有任何问题。
    - 更要紧的是**顺序**：``from_value`` 与当前态投影都假定同作品的章节按章号推进。
      按章节粒度并发，会让第 5 章在第 3 章之前写进流水，``from_value`` 记成错的值。

    **为什么用咨询锁而不是行锁**：行锁会和 ``reserve()`` 抢同一把锁——业务会话握住
    作品行、会计会话在另一条连接上等它，正是 2026-09-10 那个互锁。咨询锁是另一个
    命名空间，与行锁互不影响；进程被杀时随连接自动释放，不会留下永久占位。

    用**独占连接**而不是会话：会话在 commit 后会把连接还回池子，解锁可能落到另一条
    连接上，锁就永远不释放。独占连接保证这把锁从头到尾在同一条连接上。
    """
    engine = _engine_of(factory)
    if engine is None or getattr(getattr(engine, "dialect", None), "name", "") != "postgresql":
        # sqlite 没有咨询锁，单测串行跑——退化为不做互斥，与 `_supports_row_lock` 同源。
        yield True
        return
    key = _advisory_key(work_id)
    conn = await engine.connect()
    try:
        got = bool(
            await conn.scalar(
                text("SELECT pg_try_advisory_lock(:ns, :key)"),
                {"ns": _ADVISORY_NS, "key": key},
            )
        )
        if not got:
            yield False
            return
        try:
            yield True
        finally:
            await conn.execute(
                text("SELECT pg_advisory_unlock(:ns, :key)"),
                {"ns": _ADVISORY_NS, "key": key},
            )
            await conn.commit()
    finally:
        await conn.close()


def _is_lock_conflict(exc: BaseException) -> bool:
    """锁冲突不是这一章的错：等锁超时（55P03）/ 死锁（40P01）。"""
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None) or ""
    return str(code) in ("55P03", "40P01")


async def process_pending(
    factory: async_sessionmaker,
    *,
    provider: Any,
    limit: int = 1,
    work_id: uuid.UUID | None = None,
    chapter_timeout: int | None = None,
    tick_budget: int | None = None,
    stats: dict[str, Any] | None = None,
) -> int:
    """worker 勾子：把已入正史、账本还没处理的章补上。返回**真正写过账本**的条数。

    ``work_id`` 只给回放/补跑用；worker 不传，处理全库最早的一条。

    两个时限都必须有：
    - ``chapter_timeout`` 防一次卡住的调用挂死整轮（挂死等于后续正文生产也停）；
    - ``tick_budget`` 防一轮吃太久——账本是旁路，不能把主循环挤到饿死。
    到点停止不是故障，所以不打 failed，只记一行 warning 让人知道「还没追完」。
    """
    if ledger_mode() == MODE_OFF:
        return 0
    chapter_timeout = chapter_timeout or _env_seconds(
        CHAPTER_TIMEOUT_KEY, DEFAULT_CHAPTER_TIMEOUT
    )
    tick_budget = tick_budget or _env_seconds(TICK_BUDGET_KEY, DEFAULT_TICK_BUDGET)
    allowed = {uuid.UUID(item) for item in allowed_work_ids()} or None
    # 候选池必须比 limit 大。按 limit 取候选时，三个 worker 拿到的是**同一批**
    # （永远从最早那一章开始），于是只有一个抢得到、另外两个反复空转——真机实测
    # 三 worker 跑 4 章，全部由同一个 worker 完成，落空 68 次。多取几倍之后，
    # 抢输的 worker 会顺着候选往下试，才真的并行。
    pool = max(limit * 4, 8)
    async with factory() as session:
        pending = await pending_chapters(
            session, limit=pool, work_id=work_id, allowed=allowed
        )
    if not pending:
        return 0
    done = 0
    contended = 0
    failed = 0
    started_at = time.monotonic()
    for target_work, branch_id, chapter_no, digest in pending:
        if done >= limit:
            break
        if time.monotonic() - started_at > tick_budget:
            _log.warning(
                "state ledger tick budget %.0fs reached (pool=%d, done=%d), rest next tick",
                tick_budget,
                len(pending),
                done,
            )
            break
        # 作品级互斥：抢不到说明别的 worker 正在处理这部作品，换下一部（候选池里
        # 每部作品都有一条），不要排队等——等就是 2 分钟锁超时。
        async with _work_guard(factory, target_work) as owned:
            if not owned:
                contended += 1
                continue
            result = await _process_one(
                factory,
                provider=provider,
                work_id=target_work,
                branch_id=branch_id,
                chapter_no=chapter_no,
                digest=digest,
                chapter_timeout=chapter_timeout,
            )
        if result == "done":
            done += 1
        elif result == "failed":
            failed += 1
        else:
            contended += 1
    if stats is not None:
        stats.update(
            {
                "done": done,
                "failed": failed,
                "contended": contended,
                "pool": len(pending),
                "elapsed_s": round(time.monotonic() - started_at, 1),
            }
        )
    if contended:
        _log.info(
            "state ledger: %d 章因作品被占用/锁冲突让给了别人（不是失败）", contended
        )
    return done


async def _process_one(
    factory: async_sessionmaker,
    *,
    provider: Any,
    work_id: uuid.UUID,
    branch_id: uuid.UUID,
    chapter_no: int,
    digest: str,
    chapter_timeout: int,
) -> str:
    """处理一章，返回 ``done`` / ``failed`` / ``contended``。

    ``contended`` 单独成一类：锁冲突与抢锁失败都是「这一章还轮不到我」，
    **不能记成 failed**——记了就会退避，退避到 `MAX_ATTEMPTS` 会把这一章永久放弃，
    而它本身没有任何问题。
    """
    async with factory() as session:
        if not await _claim(
            session,
            work_id=work_id,
            branch_id=branch_id,
            chapter_no=chapter_no,
            digest=digest,
        ):
            return "contended"
        work = await session.get(StoryWorkModel, work_id)
        if work is None:
            return "contended"
        run = await _run_for(session, work_id, branch_id, chapter_no, digest)
        if run is None:
            _log.warning(
                "state ledger: no run matches canon hash work=%s chapter=%s",
                work_id,
                chapter_no,
            )
            return "contended"
        began = time.monotonic()
        try:
            outcome = await asyncio.wait_for(
                record_chapter(session, provider=provider, run=run, work=work),
                timeout=chapter_timeout,
            )
            # 耗时写进**账本处理记录**（StateRunModel），不是章节运行（ChapterRunModel）。
            # 这两个对象都叫 run，写错一个不会报错——Python 允许给实例挂任意属性，
            # 于是值落进一个没有这列的映射，界面上永远是 0。用 UPDATE 按四元组定位，
            # 谁持有那条记录都不会写偏。
            await session.execute(
                update(StateRunModel)
                .where(
                    StateRunModel.work_id == work_id,
                    StateRunModel.branch_id == branch_id,
                    StateRunModel.chapter_no == chapter_no,
                    StateRunModel.source_hash == digest,
                )
                .values(duration_ms=int((time.monotonic() - began) * 1000))
            )
            await session.commit()
            # 「跑了一圈」不等于「处理了一章」：开关关着、作品不在白名单里都会
            # 直接返回 skipped，把它算成进度会让回放统计凭空多出几十章。
            #
            # 「返回了」也不等于「处理成功」：`record_chapter` 自己在内部把抽取故障
            # 记成 failed/budget/unknown 后正常返回。只看「没抛异常」就记 done，
            # 报表会写「0 失败」而库里躺着三行 failed——真机实测过这个假绿。
            status = str(outcome.get("status"))
            if status in ("off", "skipped", "contended"):
                return "contended"
            if status in ("failed", "budget", "unknown"):
                return "failed"
            return "done"
        except asyncio.TimeoutError:
            # 超时按失败处理并退避：跳过会让这一章永远排在队首反复重来。
            elapsed = int((time.monotonic() - began) * 1000)
            await session.rollback()
            _log.error(
                "state ledger chapter timed out after %ss work=%s chapter=%s",
                chapter_timeout,
                work_id,
                chapter_no,
            )
            await _record_failure(
                factory,
                work_id=work_id,
                branch_id=branch_id,
                chapter_no=chapter_no,
                digest=digest,
                exc=TimeoutError(f"chapter timeout {chapter_timeout}s"),
                duration_ms=elapsed,
            )
            return "failed"
        except Exception as exc:  # noqa: BLE001 — 一章的账本失败不能拖住整个 tick
            elapsed = int((time.monotonic() - began) * 1000)
            await session.rollback()
            if _is_lock_conflict(exc):
                # 等锁超时/死锁：不是这一章的错，也不消耗它的重试次数。
                _log.warning(
                    "state ledger lock conflict work=%s chapter=%s（让给下一轮）",
                    work_id,
                    chapter_no,
                )
                return "contended"
            _log.exception("state ledger chapter failed work=%s chapter=%s", work_id, chapter_no)
            await _record_failure(
                factory,
                work_id=work_id,
                branch_id=branch_id,
                chapter_no=chapter_no,
                digest=digest,
                exc=exc,
                duration_ms=elapsed,
            )
            return "failed"


async def _record_failure(
    factory: async_sessionmaker,
    *,
    work_id: uuid.UUID,
    branch_id: uuid.UUID,
    chapter_no: int,
    digest: str,
    exc: Exception,
    duration_ms: int = 0,
) -> None:
    """故障留证。写不进去就只剩日志——这时也不能再抛，否则整个 tick 会被一章拖死。"""
    try:
        async with factory() as session:
            row = await session.scalar(
                select(StateRunModel).where(
                    StateRunModel.work_id == work_id,
                    StateRunModel.branch_id == branch_id,
                    StateRunModel.chapter_no == chapter_no,
                    StateRunModel.source_hash == digest,
                )
            )
            note = f"{type(exc).__name__}: {str(exc)[:180]}"
            if row is None:
                session.add(
                    StateRunModel(
                        id=uuid.uuid4(),
                        work_id=work_id,
                        branch_id=branch_id,
                        chapter_no=chapter_no,
                        source_hash=digest,
                        status="failed",
                        attempts=1,
                        note=note,
                        retry_after=_next_retry_at("failed", 1),
                        duration_ms=duration_ms,
                    )
                )
            else:
                row.status = "failed"
                row.attempts = int(row.attempts) + 1
                row.retry_after = _next_retry_at("failed", int(row.attempts))
                row.note = note
                # 失败也要记耗时：超时了多久、在哪一段耗掉的时间，只能靠这个数说清。
                row.duration_ms = max(int(row.duration_ms or 0), duration_ms)
            await session.commit()
    except Exception:  # noqa: BLE001
        _log.exception("state ledger could not even record the failure")


async def _run_for(
    session: AsyncSession,
    work_id: uuid.UUID,
    branch_id: uuid.UUID,
    chapter_no: int,
    digest: str,
) -> ChapterRunModel | None:
    rows = list(
        (
            await session.scalars(
                select(ChapterRunModel)
                .where(
                    ChapterRunModel.work_id == work_id,
                    ChapterRunModel.branch_id == branch_id,
                    ChapterRunModel.chapter_no == chapter_no,
                )
                .order_by(ChapterRunModel.attempt.desc())
            )
        ).all()
    )
    for row in rows:
        if content_hash(row.content or "") == digest:
            return row
    return None


__all__ = [
    "MAX_ATTEMPTS",
    "MODE_KEY",
    "MODE_OFF",
    "MODE_SHADOW",
    "STATE_EXTRACT_SYSTEM",
    "StateChangeItem",
    "StateChanges",
    "allowed_work_ids",
    "content_hash",
    "ledger_mode",
    "ledger_stats",
    "list_conflicts",
    "pending_chapters",
    "process_pending",
    "record_chapter",
    "reproject_pairs",
    "resolve_conflict",
    "retry_due",
    "run_reasons",
]
