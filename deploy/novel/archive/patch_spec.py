"""一次性补丁：技术规范同步 P0-1~P0-3 落地的语义变更。"""
from __future__ import annotations

import pathlib

path = pathlib.Path("Novel-Engine-Tech-Spec.md")
text = path.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"count={text.count(old)} for {old[:70]!r}"
    text = text.replace(old, new)


# 1) §4.4 补充恢复与预算语义
sub(
    """提供方结果不确定时记录 UNKNOWN 并优先查询/对账，不能承诺跨外部服务绝对 exactly-once；没有提供方幂等能力时，盲重试可能产生重复费用，须计入恢复设计。
""",
    """提供方结果不确定时记录 UNKNOWN 并优先查询/对账，不能承诺跨外部服务绝对 exactly-once；没有提供方幂等能力时，盲重试可能产生重复费用，须计入恢复设计。

**UNKNOWN 的终止必须按对账次数，不按模型 attempt**：attempt 只在新一次模型调用时递增，拿它当上限会让 UNKNOWN 永远挂起。每次对账递增 `ModelCall.reconcile_count`（迁移 `20260908_0052`），达到 `reconcile_attempts`（默认 3）后按“费用已发生”结算预留额并置 FAILED，允许后续 attempt 以新的 attempt 号重跑；重复费用留在账上，不抹除。供应商可查时据实结算并写回 `output_json`，之后同键恢复直接复用、不再调用。

**超时类异常若携带供应商 `request_id` 必须落库**，否则对账没有可查对象，只能按估价结清。

**恢复清扫（`production.recover_novel_calls()`）**是 worker 的启动动作与周期任务（默认 30 秒）：先把租约过期仍停在 `RESERVED` 的调用判定为 `UNKNOWN`（保留预留额，不猜成功也不猜失败），再对静默期（默认 60 秒）之前的 `UNKNOWN` 逐条对账。静默期用于避免把供应商尚未落账的调用提前按“钱已花掉”结清。

**预算判据是已结算金额，不是累计预留**：`direction._call` 用 `committed_minor` + 本次估价与章级上限比较，超额在调用发出前停止；预留会被释放，把历史累计预留当成已花掉的钱会高估消耗并放过越界。`CallBroker.budget_limit_minor` 在 `ledger.reserve()` 内按章做原子上限检查，并发预留不得共同突破同一上限。实际费用超过预留时补记 `_top_up`，不得因超限丢账。
""",
)

# 2) §5 幂等键含 attempt 维度
sub(
    "| 配额结算 | `logical_call_id:funding_pool` |",
    "| 配额结算 | `logical_call_id:attempt:funding_pool`（消费/释放/补记均按 attempt 独立记账） |",
)

# 3) §14 实现边界
sub(
    """生产调用协议已部分接入（R0）：`application/production.py` 包含版本化价格本、独立事务预留、事务外调用、正常成功结算、成功结果复用与 UNKNOWN 挂账。**以下仍是实现缺口，不能按目标协议推定已经生效**：小说 `reclaim_expired_calls`/`reconcile` 尚未接入后台调度，供应商查询能力未接；默认对账逻辑使用模型 attempt 判定上限，重复查询不推进对账次数；查询成功分支没有完成预留结算；跨 attempt 消费幂等导致漏记第二次费用；章节预算检查未纳入下一次预留，能够越界。SQLite 补充探测已复现后四项，见开发计划 §4、§10。""",
    """生产调用协议已接入（R0，2026-09-08）：`application/production.py` 包含版本化价格本、独立事务预留、事务外调用、统一终态结算（`_finalize` 覆盖正常成功、供应商查询成功与放弃对账三条路径）、按 attempt 独立的消费/释放/补记幂等、成功结果复用与 UNKNOWN 挂账；对账按 `reconcile_count` 有界终止；`recover_novel_calls()` 已接入 `Worker` 启动与周期 tick。章节预算按已结算金额校验，预留按章做原子上限检查。

**仍未闭合，不能按目标协议推定已经生效**：真实 PostgreSQL 下的并发抢占与崩溃注入未验证（P0-5），迁移 0051/0052 只做过 SQLite 升级；提交/释放路径尚未按 fencing token 与 input_version 做数据库条件校验（P0-4）；供应商查询能力依赖 provider 实现 `lookup_call`，默认无查询能力时只能按估价结清。""",
)

# 4) 修订记录
sub(
    "| 2026-09-07 | v5.3 | 按当前代码、定向回归与补充探测校正 §14：区分调用/账本/租约及命令协议的已接入部分与未闭合行为，开发顺序同步 Plan v6.4。 |",
    "| 2026-09-07 | v5.3 | 按当前代码、定向回归与补充探测校正 §14：区分调用/账本/租约及命令协议的已接入部分与未闭合行为，开发顺序同步 Plan v6.4。 |\n"
    "| 2026-09-08 | v5.4 | §4.4 补 UNKNOWN 按对账次数终止、失败保留 `request_id`、恢复清扫与静默期、预算按已结算金额校验；§5 配额结算键加 attempt 维度；§14 同步 P0-1~P0-3 已落地与剩余 P0-4/P0-5 边界。 |",
)

path.write_text(text, encoding="utf-8")
print("patched", path)
