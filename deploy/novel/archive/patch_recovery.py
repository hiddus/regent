"""一次性补丁：为 production.py 增加恢复清扫入口（幂等，断言 count==1）。"""
from __future__ import annotations

import pathlib

path = pathlib.Path("core/src/regent/novel/application/production.py")
text = path.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"count={text.count(old)} for {old[:60]!r}"
    text = text.replace(old, new)


# 1) 常量：对账静默期
sub(
    'DEFAULT_RECONCILE_ATTEMPTS = 3\n',
    'DEFAULT_RECONCILE_ATTEMPTS = 3\n'
    '# 对账静默期：调用刚失败时不立刻按“钱已花掉”结算，给供应商查询留出时间。\n'
    'DEFAULT_RECONCILE_GRACE = timedelta(seconds=60)\n',
)

# 2) recover_novel_calls：worker 重启后的自动恢复入口
anchor = '''

# ---------------------------------------------------------------------------
# 运行租约：让“事务外调用”期间别的 worker 不会并行推进同一章
# ---------------------------------------------------------------------------
'''
recover = '''
async def recover_novel_calls(
    session: AsyncSession,
    *,
    provider: ModelProvider | None = None,
    lease_owner: str = "recovery",
    limit: int = 50,
    grace: timedelta = DEFAULT_RECONCILE_GRACE,
    reconcile_attempts: int = DEFAULT_RECONCILE_ATTEMPTS,
) -> dict[str, int]:
    """恢复清扫：把崩溃留下的调用收口，供 worker 每次启动/周期 tick 调用（P0-1）。

    两步，顺序不能反：

    1. 租约过期仍停在 ``RESERVED`` 的调用 → ``UNKNOWN``，保留预留额等待对账；
    2. ``UNKNOWN`` 调用 → 对账：供应商可查就据实结算（成功结果之后会被复用），
       查不到就按**对账次数**有界终止，按“费用已发生”结算，允许后续 attempt 重跑。

    只处理静默期之前的调用，避免把刚刚失败、供应商还没来得及落账的调用提前结清。
    返回计数供 worker 打点；单个调用对账失败由调用方记录，不得拖垮整个 tick。
    """
    stats = {"reclaimed": 0, "succeeded": 0, "failed": 0, "pending": 0, "skipped": 0}
    reclaimed = await reclaim_expired_calls(session, limit=limit)
    stats["reclaimed"] = len(reclaimed)

    cutoff = datetime.now(UTC) - grace
    rows = (
        await session.scalars(
            select(ModelCallModel)
            .where(
                ModelCallModel.status == CALL_STATUS_UNKNOWN,
                or_(ModelCallModel.updated_at.is_(None), ModelCallModel.updated_at <= cutoff),
            )
            .order_by(ModelCallModel.updated_at)
            .limit(limit)
        )
    ).all()

    broker = CallBroker(lease_owner=lease_owner, reconcile_attempts=reconcile_attempts)
    bucket = {
        "SUCCEEDED": "succeeded",
        "FAILED": "failed",
        "PENDING": "pending",
    }
    for call in rows:
        outcome = await broker.reconcile(
            session, logical_call_id=str(call.logical_call_id), provider=provider
        )
        stats[bucket.get(outcome, "skipped")] += 1
    return stats

'''
sub(anchor, recover + anchor.lstrip("\n"))

# 3) 导入 or_
sub(
    'from sqlalchemy import select\n',
    'from sqlalchemy import or_, select\n',
)

# 4) 导出
sub(
    '    "reclaim_expired_calls",\n',
    '    "reclaim_expired_calls",\n    "recover_novel_calls",\n',
)
sub(
    '    "DEFAULT_LEASE_TTL",\n',
    '    "DEFAULT_LEASE_TTL",\n'
    '    "DEFAULT_RECONCILE_ATTEMPTS",\n'
    '    "DEFAULT_RECONCILE_GRACE",\n',
)

path.write_text(text, encoding="utf-8")
print("patched", path)
