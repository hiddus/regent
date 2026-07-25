# GAC 开发需求与计划（Goal Attainment Convergence）

> 状态：ACTIVE  
> 批次 ID：`GAC-20260724`（**不与** PRD/P0/P1/P2/G0–G10 序号混用）  
> 日期：2026-07-24  
> 定义：`REGENT-DEFINITION-1.0`（不得改写）  
> 输入：诊断报告 `Regent-现状评估与问题诊断报告.md` + 「不达标→重组能力」缺口分析

## 0. 命名约定

| 前缀 | 含义 |
|---|---|
| **GAC-A*** | 收敛批：Goal 终态、门控、迭代自动推进、失败可观测 |
| **GAC-B*** | 能力批：交付失败统一恢复、gap 分流、BUILD 最小切片 |
| **GAC-C*** | 组织/运维批：Run 积压、磁盘、Scheduler 联调（后置） |

禁止使用 `P0/P1/P2`、`G0–G10`、`p2-*-0N` 作为本批次任务号。

## 1. 问题合并结论

| 现象 | 根因 |
|---|---|
| ACTIVE 永不结束 | Preview 后无 ACHIEVE/EXHAUST |
| 门控永远 INSUFFICIENT | 只认不可达的 product-analytics，不认 preview-smoke |
| REVISE 不转 | 决策写出后无人触发 Discovery |
| 中途静默 | exit 点只打日志 |
| 「重组能力」半圈 | 交付失败只在 generation 恢复；且一律 REUSE 文案重生 |

## 2. 开发需求（DoD）

### GAC-A1 — Goal 终态接线
- CONTINUE → `GoalCommand.ACHIEVE`
- STOP → `GoalCommand.EXHAUST`（或 FAILED，有明确 failure_code）
- 终态写 conversation EVENT + `execution_stage`

### GAC-A2 — Preview 门控可达
- 默认绑定 `smoke_pass`（source=`preview-smoke`，允许 internal）
- 保留 `product_rejection_count` 护栏（0 样本视为通过）
- smoke 失败 → 收敛终态，不无限 ACTIVE

### GAC-A3 — REVISE 自动重开 Discovery
- 决策为 REVISE 时自动调用 `IterationLoopService.handle_revise`

### GAC-A4 — 中途退出可观测收敛
- Discovery 非 SELECT、能力 WAITING_HUMAN、Build/Deploy 失败：更新 `execution_stage` + conversation；必要时 EXHAUST/FAILED

### GAC-A5 — 交付失败入口统一
- generation / static publish / preview deploy 失败均能触发 `DeliveryGapRecovery`（文案含 `delivery-review-v1`）

### GAC-B1 — gap 分流恢复
- 按 stylesheet / evidence / goal-intent 选择恢复策略（不只 product-surface）
- presentation → product-surface + CSS/layout guidance
- evidence → REUSE `allowlisted-http-source-v1` + outbound/observed guidance
- goal_intent → product-surface + first_deliverable / success_criteria guidance

### GAC-B2 — BUILD 最小切片
- 缺口可注册可验证 capability 再挂链（禁止空标 BUILD）
- `materialize_build_items` 为 BUILD 项写入 goal-scoped `GOAL_CERTIFIED` Capability

### GAC-C1 — CREATED Run 推进
- `advance_created_run` / worker `reclaim_stale_created_runs`
- `_ensure_work_and_run_for_goal` 创建后立即推进 CREATED→RUNNING

### GAC-C2 — INSUFFICIENT 超时 EXHAUST
- Gate `INSUFFICIENT_EVIDENCE` 武装 DurableTimer `goal.exhaust_insufficient`（30m）
- `TimerFired` → 仍卡在 `GATE_INSUFFICIENT_EVIDENCE` 则 EXHAUST

### GAC-C3 — Scheduler 表联调
- 真实表：`execution_queue_entries`（migration `20260723_0025`）
- 模型：`ExecutionQueueEntryModel`；禁止再写 `scheduler_queue_entries`

## 3. 执行顺序

```text
GAC-A* →（r52）→ GAC-B1 → GAC-B2 → GAC-C1 → GAC-C2 → GAC-C3 →（r53）
```

## 5. 本批进度

| ID | 状态 | 说明 |
|---|---|---|
| GAC-A1 | **完成** | Preview 后 CONTINUE→ACHIEVE / STOP→EXHAUST |
| GAC-A2 | **完成** | 默认门控改 `smoke_pass`（preview-smoke，SUM≥1） |
| GAC-A3 | **完成** | REVISE 自动 `handle_revise` |
| GAC-A4 | **完成** | Discovery 非 SELECT / Deploy 失败 halt + 终态 |
| GAC-A5 | **完成** | publish/deploy/generation 文案对齐 + deploy 走 DeliveryGapRecovery |
| GAC-B1 | **完成** | gap 分流：presentation / evidence / goal_intent + 能力与 guidance 路由 |
| GAC-B2 | **完成** | BUILD → 注册 GOAL_CERTIFIED capability（禁空标） |
| GAC-C1 | **完成** | CREATED Run 推进 + worker reclaim |
| GAC-C2 | **完成** | INSUFFICIENT 30m timer → EXHAUST |
| GAC-C3 | **完成** | 确认 `execution_queue_entries` 模型/迁移对齐 |

### GAC-D* — 不达标→重组能力/组织（定义 ATTRIBUTE_3/4）

| ID | 状态 | 说明 |
|---|---|---|
| GAC-D1 | **完成** | 恢复阶梯 REUSE→COMPOSE→BUILD→STOP（`capability_ladder`） |
| GAC-D2 | **完成** | BUILD 写入可验证 implementation package（guidance+acceptance） |
| GAC-D3 | **完成** | `OrganizationService.reorganize_for_gap` 挂入交付恢复 |
| GAC-D4 | **完成** | Gate FAILED / insufficient 超时先重组再 EXHAUST |
| GAC-D5 | **完成** | 阶梯耗尽 → 统一 `Goal.EXHAUST` |

### GAC-E* — 大型 Goal 强制里程碑拆分（按 Goal 推导，非固定模板）

| ID | 状态 | 说明 |
|---|---|---|
| GAC-E1 | **完成** | 从 Goal/GoalSpec 推导里程碑数量与内容（`goal-driven-v1`） |
| GAC-E2 | **完成** | 交付/验收只对当前里程碑；非终里程碑禁止 Goal.ACHIEVE |
| GAC-E3 | **完成** | 里程碑达成 → 推进下一里程碑并重启 Discovery |

原则：
- **大型任务不可能一次循环出最终结果**
- 里程碑数与内容由 Goal 决定（显式列表 / 分句 / 成功标准切片等），**不是**定死的 M1/M2/M3

Release：`…-d-r54` → `20260724-gac-e-r55` → `20260724-gac-e-r56`
