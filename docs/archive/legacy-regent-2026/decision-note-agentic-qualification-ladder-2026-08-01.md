# Decision Note — Agentic 资格晋级阶梯（废止失败 control 否决权）

> 状态：**ACCEPTED**（Owner 指令 2026-08-01）  
> 日期：2026-08-01  
> 取代/降级：[`decision-note-gq4-pending-2026-07-31.md`](./decision-note-gq4-pending-2026-07-31.md) 中「须先恢复 artifact-backed 漏斗再评判」条款；[`decision-note-gq3-window-2026-07-31.md`](./decision-note-gq3-window-2026-07-31.md) 相对对照晋级合同  
> 执行计划：[`agentic-qualification-executable-plan-2026-08-01.md`](./agentic-qualification-executable-plan-2026-08-01.md)  
> 关联：PRD §10.5 · Tech-Spec §13.7 · M6 观察窗（须按本 note **暂停扩流并评估回滚**）

## 1. 问题陈述

当前晋级协议隐含自锁：

```text
artifact-backed 必须先恢复生产漏斗
  → agentic 才能获得可比较验证流量
  → 才能谈 GQ-4 / 扩大 canary
```

但 **artifact-backed 生成器内部是单次 `generate_structured`**，无会话内 tool loop；生产漏斗验证成功率为 0 或接近 0 时，候选臂被饿死，实验只能得出「证据不足」，却永远无法验证 agentic 的绝对交付能力。  
**用失败的 control 否决候选的验证机会，是死循环，不是谨慎。**

既有 GQ-3 报告结论 `INSUFFICIENT_EVIDENCE`（及 35:4 等分流）**不得**再用于 GQ-4 或扩大流量决策。

## 2. 裁决

### 2.1 实验结论改判

将当前 GQ-3 / 同类生产对照窗结论从 `INSUFFICIENT_EVIDENCE` 改为：

```text
INVALID_BASELINE
reason:
- control_verified_success_rate_zero
- candidate_starved_of_traffic
- funnel_gate_depends_on_failed_control
- cost_and_freeze_metadata_incomplete
```

- 涉及实现与报告：`generation_strategy_experiment.py`、`gq3_production_report.py`、`generation_strategy_promotion.py`、本 DecisionRecord 及后续报告 JSON。  
- **禁止**用该窗样本讨论 `PROMOTE_AGENTIC_CANDIDATE` / GQ-4 ACCEPTED。

### 2.2 artifact-backed 角色重定义

```text
artifact-backed:
  role: FALLBACK_ONLY
  eligible_as_champion: false
  verified_delivery_claim: false
```

仅允许用于：

1. agentic 不可用时的诊断/降级返回；  
2. 已认证固定模板路径；  
3. 历史回归对照。

仍须过最低安全、完整性与可运行检查；**不再**作为 Agent loop / repair / Skill 等能力门禁的 champion，也**不再**要求其「先恢复漏斗」作为 agentic 获得验证流量的前提。

### 2.3 两阶段晋级（资格 ≠ 流量）

```text
Agentic Offline Qualification（绝对资格）
  → Internal Dogfood
  → 受控 Canary（5% → 25% → 50% → DEFAULT）
```

GQ-4（默认全量 agentic）**继续关闭**，直到阶梯走到 DEFAULT 且有独立 DecisionRecord。  
删除前置：`必须先由 artifact-backed 恢复生产漏斗`。

### 2.4 对已开 M6 5% 窗的立即动作

2026-08-01 已按旧协议打开生产 5% canary（[`m6-canary-window-2026-08-01.json`](./m6-canary-window-2026-08-01.json)）。在本裁决下：

1. **停止扩流**（禁止 EXPAND_10 / 提高 percent）；  
2. **默认建议立即 `clamp` 回 0%**，直至 Offline Qualification 出口绿；若 Owner 选择短暂保留 5% 仅作观察，须书面注明「非资格证明、不计入晋级样本」；  
3. M6 观察计划降级为 **HALTED_PENDING_QUALIFICATION**（见执行计划）。

## 3. 晋级状态机

```text
DISABLED
  → OFFLINE_QUALIFICATION
  → INTERNAL_DOGFOOD
  → CANARY_5
  → CANARY_25
  → CANARY_50
  → DEFAULT
```

任一级可回退至 `FALLBACK_ONLY`（流量回 artifact-backed / percent=0）。

分流原则（概念）：

```text
kill_switch → artifact-backed
qualification ∉ {DOGFOOD, CANARY_*, DEFAULT} → artifact-backed
else → assign_by_qualified_rollout(goal_id, state)
```

`funnel_degraded` **只**能阻止扩流或触发回滚评估，**不能**阻止已过离线资格的 agentic 进入 Dogfood / 首档 5%。

## 4. Offline Qualification 绝对门槛（摘要）

产品类型第一批冻结：**带持久化 CRUD、一个外部证据输入、一个机器可执行 Journey 的轻后端 Web App**（单一 Runtime Profile）。

固定链：冻结 GoalSpec → Agentic Runner → 隔离 Sandbox → 项目测试 → 启动/Smoke → 动态 Preview → Journey → `accepted_workspace_snapshot` → REVISE → V2 增量。

绝对门槛（不要求相对优于 artifact-backed）：截断/畸形 tool call 不得完成；manifest 完整；预算/token/成本/transcript 对账；预算耗尽不得晋级；沙箱无越权；Preview 与 verification 同 profile hash；V2 基于 V1 accepted snapshot；基础设施误失败 0；无假 `ACHIEVED`。

## 5. 禁止事项

1. 禁止用 `INVALID_BASELINE` 窗或双臂 pass≈0 的相对对照宣称 agentic 失败或成功。  
2. 禁止要求 artifact-backed verified success > 0 才给 agentic 验证流量。  
3. 禁止跳过 Offline Qualification 直接 DEFAULT / GQ-4。  
4. 禁止假 ACHIEVED、未验证产物发布、跨版本续跑在途 Run。

## 6. 记录

| 字段 | 值 |
|---|---|
| Decision | ACCEPTED |
| Scope | 废止失败 control 否决权；Agentic Qualification Ladder |
| Immediate ops | 停止扩流；建议 clamp 5%→0% 直至 Offline Qual |
| Author | rechaos（Owner 指令） |
| Supersedes | GQ-3 相对对照作为晋级唯一依据 |
