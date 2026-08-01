# M6 Canary 观察窗执行计划（2026-08-01）

> 状态：READY（5% canary 已开窗；本计划覆盖观察 → 指标 → 闭环 → 窗末决策）
> 前置：`docs/m6-canary-window-2026-08-01.json`、`docs/m6-preflight-report-2026-08-01.json`
> 开窗时刻：`2026-08-01T14:38:33+00:00`
> 约束：默认策略保持 `artifact-backed`；不自动 GQ-4；不用 soft-pass 宣称 M6 出口达标

## 1. 目标

在 7 天或 100 个新 Goal（先到为准）内，用**开窗后切片**证明 agentic 臂相对对照臂是否可接受，并完成至少一条真实产品闭环；窗末给出 HOLD / EXPAND_10 / ROLLBACK，且不翻转默认策略。

## 2. 现状与风险

| 项 | 状态 |
|---|---|
| 配置 | `percent=5` + `gate=true` + 默认 artifact-backed |
| T0 探针 | 已落盘，但 24h 含开窗前历史 agentic（plan 级 share 失真） |
| 出口四指标 | 尚未按开窗 since 计算 |
| 真实闭环 | 未开始 |
| 主要噪声 | 历史 HTTPStatusError / GENERATION_EXECUTION_FAILED；changeset=0 需在开窗后重测 |

## 3. 批次

### A. 开窗后切片观测（立即）

1. 升级 `ops/probe_m6_canary.py`：支持 `--since 2026-08-01T14:38:33+00:00`
2. 日更：`python -B ops/probe_m6_canary.py --since … --out docs/m6-canary-daily-YYYY-MM-DD.json`
3. 关注：新 Goal 数、首 plan 臂分流、agentic run 状态/失败码、changeset 率、open gap、outbox

**硬回滚**（任一触发即 `python -B ops/clamp_generation_strategy_freeze.py`）：

- 严重安全事件或错误发布
- 开窗后 agentic 臂连续 ≥10 次硬失败且无 changeset
- outbox FAILED 激增或 delivery-gap OPEN 回潮失控
- 成本 / P95 相对对照臂明显失控（报告中量化后再钉阈值）

### B. M6 出口指标报告（本周内）

新增 `ops/m6_canary_report.py`（复用 `gq3_production_report` 观测模型）：

- `--since` = 开窗时刻；intent-to-treat = 窗口内 goal 的首 plan
- 输出双臂：`preview_ready_rate`、`first_runnable_rate`、`human_intervention_rate`、`mean_repair_rounds`
- 样本不足：只报点估计 + 区间备注，**不宣称达标**
- 落盘：`docs/m6-canary-report-YYYY-MM-DD.json`

出口门槛（计划原文）：

- `preview_ready_rate ≥ 60%`
- `first_runnable_rate ≥ 50%`
- `human_intervention_rate ≤ 0.3`
- `mean_repair_rounds ≤ 2.5`

### C. 一条真实产品闭环（与观察并行）

证据化完成并写入 `docs/m6-closed-loop-YYYY-MM-DD.md`：

1. 1 个真实 Goal
2. 3 位真实用户触达/使用
3. 1 条可归因反馈
4. 1 次基于 last-good 的增量 REVISE

无此条则窗末不得宣称 M6 出口 PASS。

### D. 窗末决策（7d 或 100 新 Goal）

产出 `docs/m6-canary-decision-YYYY-MM-DD.json`：

| 决策 | 条件 | 动作 |
|---|---|---|
| HOLD | 样本不足或指标临界 | 维持 5%，继续观察 |
| EXPAND_10 | 四指标过线 + 闭环成立 + 无护栏触发 | Owner 批准后 percent→10（默认仍不变） |
| ROLLBACK | 护栏触发或显著劣于对照臂 | clamp 回 0% / gate off |

## 4. 明确不做

- 不把默认 `generation_strategy` 改为 `agentic`（GQ-4 需单独 DecisionRecord）
- 不用开窗前历史计划计算 canary 成功率
- 不扩 Skill / Hive / 组织层投资
- 不以提高 temperature、token、repair 次数作为独立修复

## 5. 本计划成功标准

- [ ] 探针支持开窗后切片，且至少 1 份 post-open daily
- [ ] M6 四指标报告可复跑
- [ ] 闭环证据已完成或书面 blocker
- [ ] 窗末有书面 HOLD/EXPAND_10/ROLLBACK；默认策略仍为 artifact-backed
