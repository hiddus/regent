# DecisionNote: PRD §4.4 对话式完整交付 — 编码批准

**日期**：2026-07-31  
**状态**：ACCEPTED  
**依据**：
- [Regent-PRD.md](../Regent-PRD.md) §4.4
- [conversational-delivery-plan-2026-07-31.md](./conversational-delivery-plan-2026-07-31.md) CD-4
- [decision-note-auto-start-journey-2026-07-31.md](./decision-note-auto-start-journey-2026-07-31.md)

## 决策

批准按 PRD §4.4 与统一计划 CD-4 编码「对话层升级为有界工具/命令循环」：

1. `AppGuidanceService.guide` 可多步链式执行（有界，非无限 chat+tools）。
2. `/v1/conversations/{id}/messages` 在绑定 AppProject 时可触发同一 guidance loop。
3. Evidence / 对话检索段可进入 agent 上下文组装。
4. capabilities→ToolSpec 发现模块可落地；仅对声明 `parameters` 的能力包生效。

## 不做

- 不删除 Permit / Outbox / Evidence / Audit。
- 不把本批准解释为 GQ-4 已晋级或 canary 已打开。
- 不要求本阶段实现 token 流式输出或完整 LISTEN/NOTIFY。
