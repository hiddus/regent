# Decision Note — D5 画像权威源（ACCEPTED）

> 状态：**ACCEPTED**  
> 日期：2026-07-31  
> 关联：CD-7.3 · [`conversational-delivery-next-plan-2026-07-31.md`](./conversational-delivery-next-plan-2026-07-31.md)

## 结论

两个 Settings 字段**分工明确，不作合并别名**：

| 字段 | 权威范围 |
|---|---|
| `REGENT_DELIVERY_PROFILE` | 交付恢复阶梯次数、Gate 重组预算、agentic `max_turns` 缩放 |
| `REGENT_DECISION_PREFERENCE` | 确认策略（ALLOW/ASK/DENY）、HumanTask 超时与高风险审批呈现 |

二者枚举相同（aggressive / balanced / conservative），但**不得**互相覆盖。运维若只改其一，另一保持独立。

## 实现落点

- `delivery_gap_recovery.recover` / `prepare_gate_reorganization` / `resolve_delivery_budget` → `delivery_profile`
- `decision_policy` / `confirmation_present` / HumanTask 超时 → `decision_preference`
- Gate：`gate_reorg_max(persona) = round(6 * recovery_budget_multiplier(persona))`

## 记录

| 字段 | 值 |
|---|---|
| Decision | ACCEPTED |
| Author | rechaos |
