# DecisionNote: Session 工作清单（Work Plan）

**日期**：2026-08-03  
**状态**：ACCEPTED  
**相关**：[`execution-plan-session-work-plan-2026-08-03.md`](execution-plan-session-work-plan-2026-08-03.md)、A0 出口合同

---

## 0. 决策

Primary Agent 多步工作 **必须先有工作清单再逐步执行**。清单权威存 `ExecutionPlanItem`，挂 Goal/Session；服从 A0 COMPLETE/STOP/ASK。

吸收：OpenWork 计划批准；Claude Code Task/Todo 状态机。  
不吸收：默认 Hive 三角色；第三套 loop。

## 1. 不变量

| ID | 内容 |
|----|------|
| W-1 | 非 trivial 写操作前须已有 plan items（Step 0） |
| W-2 | 同时至多一条 Primary `in_progress`（软约束，写入时规范化） |
| W-3 | COMPLETE 时未完成项须进 result_bundle.open_items 或全部 completed |
| W-4 | 子 Agent 仅能领有 `item_key` 的清单项 |
| W-5 | 大改/首跑可 ASK `plan_approve`；小续跑默认可免 |

## 2. 拍板默认

Q1 首跑/重规划批准；Q2 单文件级豁免；Q3 COMPLETE 可带 open_items；Q4 子 Agent 领单本期落地；Q5 `todo_write` 兼容别名。
