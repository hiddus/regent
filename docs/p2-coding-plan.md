# Regent P2 编码清单（CURRENT / P2Start 已开）

> 状态：ACTIVE  
> 日期：2026-07-23  
> 准入：`docs/P2StartDecisionRecord.json` = PASSED  
> 定义：`REGENT-DEFINITION-1.0`（不得改写）  
> 基线：PRD / Tech Spec / 附录 = CURRENT

## 承诺范围（PRD §8）

| 包 | 状态 | 说明 |
|---|---|---|
| P2-1 Scheduler | **已编码** | Worker tick + Permit + 安全抢占 + checkpoint |
| P2-2 Runtime Profiles | **已编码** | 4 bootstrap；CERTIFIED: python-web-v1 / static-web-v1 |
| P2-3 Memory | **已编码（条件）** | admit/verify/revoke；启用仍需 stage DecisionRecord |
| P2-4 Eval Harness | **已编码** | create→freeze→run/score→decide（不自动开多 Agent） |
| **delivery-review-v1** | **已编码** | 能力包；生成后/发布前 fail-closed 拒 demo 壳页 |
| P2-5 / P2-6 | **门禁** | 需 Eval DecisionRecord 后另批 |
| P2-7 / 8 / 9 | **候选** | 不自动编码 |

## 批次

### `p2-scheduler-01` — 完成

1. 表 + 迁移 `20260723_0025`
2. enqueue / aging / 多资源预留 / 可重放 Decision / release / ledger
3. API `/v1/scheduler/*`
4. Worker 循环 `tick`、安全 preempt（拒 DISPATCHING EO）、checkpoint/resume
5. schedule→Permit（`scheduler.dispatch`，有 active Run 时）

### `p2-runtime-02` / `p2-eval-04` / `p2-memory-03` — 完成（最小切片）

1. 迁移 `20260723_0026`：runtime_profiles / eval_runs / memory_records / preemption_records / scheduler_checkpoints
2. API：`/v1/runtime-profiles`、`/v1/eval-runs`、`/v1/memories`
3. API/Worker 启动 seed bootstrap profiles

## 禁止（仍有效）

- 无 P2-4 Eval DecisionRecord 前默认多 Agent / 自适应组织
- 跳过 G0 约束做不可审计副作用

## 结论

```text
DONE：P2 承诺包 1/2/4 + 条件包 3 最小实现
NEXT：Eval 出 DecisionRecord 后再开 P2-5/6
```
