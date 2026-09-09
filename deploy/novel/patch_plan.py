"""一次性补丁：把 P0-1~P0-3 的完成状态写进开发计划。"""
from __future__ import annotations

import pathlib

path = pathlib.Path("Novel-Engine-Plan.md")
text = path.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"count={text.count(old)} for {old[:70]!r}"
    text = text.replace(old, new)


sub(
    "- [ ] **P0-1 UNKNOWN 恢复闭环**：把小说调用回收与对账接入后台调度；区分对账次数和模型 attempt，支持供应商查询或明确的未知费用终止策略。验收：默认配置能有界结束；worker 重启后自动恢复；成功结果复用；查询无结果不能盲重试。落点：`production.py`、Worker。",
    "- [x] **P0-1 UNKNOWN 恢复闭环**（2026-09-08 完成）：新增 `reconcile_count` 列与迁移 `20260908_0052`，对账按**对账次数**而非模型 attempt 终止；新增 `production.recover_novel_calls()` 并由 `Worker` 启动时与每 30 秒周期调用（先回收过期 `RESERVED` → `UNKNOWN`，再逐条对账）。超时类异常若带 `request_id` 会落库，供供应商查询复用。验收证据：`test_recovery_ledger.py` 中 D1 有界终止、重启回收后无悬空预留、对账命中后同键复用且不重发调用、静默期内不提前结清共 12 例。`P0-5` 真实 PostgreSQL 并发验证仍待做。",
)

sub(
    "- [ ] **P0-2 每次尝试独立结算**：统一正常成功、查询成功和未知费用结算；reservation/consume/release 按 attempt 幂等，保留每次已发生费用。验收：上述两种账本探测转为回归用例；重复对账不重复扣费，所有终态无悬空预留，调用金额与账本一致。落点：`production.py`、账本及迁移约束。",
    "- [x] **P0-2 每次尝试独立结算**（2026-09-08 完成）：`CallBroker._finalize()` 统一正常成功、供应商查询成功与放弃对账三条路径；consume/release/top-up 幂等键含 `:attempt` 维度，实际费用超过预留时补记 `_top_up` 而非丢账。验收证据：`test_d2_provider_lookup_settles_the_reservation`、`test_d3_each_attempt_is_settled_independently`、`test_d4_unknown_call_cost_is_never_dropped`。",
)

sub(
    "- [ ] **P0-3 预算原子预留**：用已消费加未结清预留核验下一调用估价；定义实际费用超过预留时的记录与停止行为，不把累计历史预留当可用额度。验收：仅余 1 单位时超额调用在发出前拒绝；并发预留不能突破同一上限；真实已发生费用不能因超限丢账。落点：`direction._call`、CallBroker、账本。",
    "- [x] **P0-3 预算原子预留**（2026-09-08 完成）：预算判据改为**已结算金额** `committed_minor` + 估价，不再用累计历史预留；`CallBroker.budget_limit_minor` 在 `ledger.reserve()` 内做按章原子上限检查。验收证据：`test_d4_budget_is_checked_against_outstanding_not_cumulative`（仅余 1 单位时在发出前拒绝）、`test_d4_reservation_never_exceeds_the_chapter_ceiling`（并发预留不破上限、跨章独立核算）。",
)

sub(
    "| 2026-09-07 | v6.4 | 重新核验当前工作树：记录 111 项定向回归、前端构建及迁移图证据；校正 R0/R1/R2/M2 完成边界，加入四个可复现缺口及 P0/P1 后续批次。 |",
    "| 2026-09-07 | v6.4 | 重新核验当前工作树：记录 111 项定向回归、前端构建及迁移图证据；校正 R0/R1/R2/M2 完成边界，加入四个可复现缺口及 P0/P1 后续批次。 |\n"
    "| 2026-09-08 | v6.5 | P0-1~P0-3 落地：新增迁移 `20260908_0052`（`reconcile_count`）、`recover_novel_calls()` 与 Worker 启动/周期恢复、失败异常保留 `request_id`、预算改按已结算金额校验并做按章原子预留。小说域回归 99 例通过。 |",
)

path.write_text(text, encoding="utf-8")
print("patched", path)
