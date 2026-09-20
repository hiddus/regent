"""把缺陷 5 从「调查中」改写为「已定位 + 已修复」。

用法：python deploy/novel/patch_archive_d5.py
"""

from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TARGET = os.path.join(ROOT, "docs", "archive", "novel-m2-m4-2026-09-10.md")

NEW = '''### 缺陷 5：预留与业务事务互相等待（同一 worker 自死锁）—— 已定位、已修复

修完缺陷 4 后章仍停在 DIRECT，错误码变成
`OperationalError: canceling statement due to lock timeout`，三次 attempt 分布在
不同 worker、间隔约 3.5 分钟（≈ 90s 后退 + 120s 锁等待），且**当天 0 次模型调用**
——卡点在调用**之前**。

用 `d4_lockwatch.py` 在章推进期间采样（`d4_probe_*` 先排除了"端点不通"和
"结构化调用坏了"：ping 返回 200，`generate_structured` 也能正常返回），抓到
决定性现场：

| pid | 来源 | 状态 | 持有 xid | 语句 |
|---|---|---|---|---|
| 87105 | `172.20.0.7`（worker-3） | idle in transaction 23s | **是** | `SELECT novel_personas …`（业务会话） |
| 87092 | `172.20.0.7`（**同一 worker-3**） | 等 `transactionid` 22s | 否 | `SELECT novel_works.id … FOR UPDATE`（会计会话） |

**同一个容器的两条连接互等。** 根因是 `CallBroker.run` 里的顺序：

```python
async with self._tx(session) as acc:      # 会计会话：另一条连接
    ticket = await self._prepare(acc, …)  #   → ledger.reserve() 对作品行 FOR UPDATE
if commit_before_call and _in_transaction(session):
    await session.commit()                # 业务事务到这一步才提交
```

预留要对作品行 `FOR UPDATE`，而业务会话此刻事务还开着（步骤已标 RUNNING、
事件已写入、租约已领）且**持有 xid**；会计会话是另一条连接，只能等业务会话
提交；业务会话又在等预留返回。两边互等，直到锁超时。

`commit_before_call` 这个开关本身就是设计意图的一部分（"事务外调用"），
但**它只挡住了 HTTP 往返，没挡住预留**——而预留也在事务里、也会加锁。
同理，缺陷 4 里加的 `TransactionFreeProvider` 也救不了这里：它在 HTTP 调用
前提交，可死锁发生在调用**之前**。

修复：把提交提到预留之前（`run()` 与 `run_batch()` 两处），让业务事务先落地，
会计会话不再等自己。`run_batch` 同样有这个问题（预留循环在提交之前）。

**为什么这条没有单测守卫**：它需要两个真实连接 + 真实行锁才会复现，SQLite
单测里 `with_for_update` 与跨连接 xid 等待都不成立。因此它的验收是**真机跑章**
（章能走到 CANONIZED）—— 这条限制已写在这里，别以为是"忘了写测试"。

'''

START = "### 缺陷 5（调查中）：DIRECT 步骤 `lock timeout`"
END = "## M4：生长形状判定（24 章真实数据，零额外成本）"


def main() -> int:
    with open(TARGET, encoding="utf-8") as fh:
        lines = fh.readlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith(START)]
    ends = [i for i, ln in enumerate(lines) if ln.startswith(END)]
    assert len(starts) == 1, f"缺陷 5 起始锚点不唯一: {starts}"
    assert len(ends) == 1, f"M4 锚点不唯一: {ends}"
    i, j = starts[0], ends[0]
    assert i < j, "锚点顺序不对"
    with open(TARGET, "w", encoding="utf-8") as fh:
        fh.writelines(lines[:i] + [NEW] + lines[j:])
    print(f"[OK] 缺陷 5 章节已改写（原 {j - i} 行 → 新 {len(NEW.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
