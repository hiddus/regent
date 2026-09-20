"""把缺陷 4 从「未修复」改写为「已定位 + 已修复」，并补记新发现的缺陷 5。

一次读写完成，锚点唯一性全部断言（同一文件多次 Edit 会竞态）。
用法：python deploy/novel/patch_archive_d4.py
"""

from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TARGET = os.path.join(ROOT, "docs", "archive", "novel-m2-m4-2026-09-10.md")

NEW = '''### 缺陷 4：泄漏的 idle-in-transaction 静默死锁整条流水线（已定位、已修复）

J1b 观察 35 分钟发现章运行卡在 `RUNNING/ASSEMBLE`，worker 活着但 40 分钟内
**0 次模型调用**、日志只有 7 行。用 `pg_stat_activity` 定位到：

| pid | 状态 | 等待 | 事务已开 | 最后语句 |
|---|---|---|---|---|
| 83140 | idle in transaction | Client/ClientRead | **1h29m** | `SELECT novel_personas …` |
| 85989 | active | Lock/transactionid | **1h29m** | `SELECT novel_works.id … FOR UPDATE` |

清除后暴露第二层：`novel_model_calls` 里有一条 **90 分钟无人认领的 `RESERVED`
调用**，`reconcile_count=0`；后续推进一律返回 `CALL_UNKNOWN`，运行被判死。

#### 真正的根因（复现两次，第二次抓到活体）

不是"某次进程被杀"这种偶发，而是**结构性的**：worker 主循环只在整步走完后提交——

```python
async with self.sessions() as novel_session:
    progressed = await advance_background_run(novel_session, provider=...)
    if progressed is not None:
        await novel_session.commit()      # 提交发生在耗时数分钟的调用之后
```

`CallBroker.run` 有 `commit_before_call`，但它**只保护走 broker 的调用**；
`generation.py` 里 11 处直接 `provider.generate_structured(...)` 不受保护。
业务事务于是在整个 HTTP 往返期间开着，会话停在「事务中空闲」并持有 xid，
其他 worker 的 `FOR UPDATE` 排队等这个 xid——`skip_locked` 绕不过（等的是
**事务结束**，不是行锁）。库里 `idle_in_transaction_session_timeout=0`，
泄漏可永久存活，症状**完全静默**：无报错、无日志、健康检查 200。

第二次复现（`d4_poll.py` / `d4_lock.py`）拿到决定性证据：

- `172.20.0.6`（= `regent-worker-2`）的会话 `idle in transaction` 15 分钟，
  `has_xid=true`，`wait_event=Client/ClientRead`——**客户端 15 分钟没再发语句**；
  最后一条语句是 `direction.plan_chapter` 里的 personas 查询。
- **同一容器**的另一条连接阻塞在 `Lock/transactionid` 上等它：worker 自己等自己。
- 容器内 `ss`/`/proc/net/tcp` 只有 2 条 TCP 连接，**都通向 postgres**，
  没有任何外部 HTTP 连接 → 不是"调用慢"，是"锁死了"。
- `novel_model_calls` 当天只有 1 条 FAILED，**没有任何 RESERVED** →
  卡点在任何模型调用之前，`commit_before_call` 根本没机会执行。

#### 修复（四项，各自有守卫）

1. **事务不得跨越网络调用——统一收口，不靠每个调用点记得传参**
   （`production.TransactionFreeProvider`，在 `advance_background_run` 入口包一次）。
   漏一个调用点就整条线停摆的约束，不能靠自觉维持。
2. **数据库兜底**：`idle_in_transaction_session_timeout=300s`、
   `lock_timeout=120s`。让"永久静默"变成"有界且会报错"。
3. **重试必须有间隔**：可重试失败按住运行租约 90 秒（`_RETRY_BACKOFF`），
   不再一释放就被三个 worker 同瞬间抢光预算。
4. **步骤失败必须打日志**：失败此前只写数据库表，没人看表——这正是
   "静默"的根源。新增的 `logger.warning` 在下一轮排障里立刻给出了错误码。

反证：`deploy/novel/mutate_check_d4.py`（3 个变异，探针红 + 护栏绿）。

### 缺陷 5（调查中）：DIRECT 步骤 `lock timeout`

修完缺陷 4 后，章仍停在 DIRECT，错误码变成
`OperationalError: canceling statement due to lock timeout`（三次 attempt
分布在 worker-2 / worker-3，间隔约 3.5 分钟 ≈ 90s 后退 + 120s 锁等待）。
`事务中空闲 = 0`——不是缺陷 4 那种泄漏事务，是**真的有人在持锁 120 秒以上**。
用 `d4_lockwatch.py` 在章推进期间采样锁等待与持锁者，结论待补。

**注意：`lock_timeout=120s` 是我本轮加的。** 它可能只是把原本"永久等待"
变成了"120s 后报错"——也就是说，缺陷 4 与缺陷 5 可能是**同一个锁竞争的
两种表现**。在拿到现场数据前不下结论。

'''

START = "### 缺陷 4（未修复，需决策）：泄漏的 idle-in-transaction 静默死锁整条小说流水线"
END = "## M4：生长形状判定（24 章真实数据，零额外成本）"


def main() -> int:
    with open(TARGET, encoding="utf-8") as fh:
        lines = fh.readlines()

    starts = [i for i, ln in enumerate(lines) if ln.startswith(START)]
    ends = [i for i, ln in enumerate(lines) if ln.startswith(END)]
    assert len(starts) == 1, f"缺陷 4 起始锚点不唯一: {starts}"
    assert len(ends) == 1, f"M4 章节锚点不唯一: {ends}"
    i, j = starts[0], ends[0]
    assert i < j, "锚点顺序不对"

    new_lines = lines[:i] + [NEW] + lines[j:]
    with open(TARGET, "w", encoding="utf-8") as fh:
        fh.writelines(new_lines)

    # 校验：表格行之间不得插入空行（历史教训）
    with open(TARGET, encoding="utf-8") as fh:
        out = fh.readlines()
    bad = [
        n
        for n in range(1, len(out))
        if out[n].startswith("|")
        and out[n - 1].strip() == ""
        and n >= 2
        and out[n - 2].startswith("|")
    ]
    assert not bad, f"表格被空行截断于行: {bad}"
    print(f"[OK] 缺陷 4 章节已改写（原 {j - i} 行 → 新 {len(NEW.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
