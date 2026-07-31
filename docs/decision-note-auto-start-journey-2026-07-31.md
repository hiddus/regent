# DecisionNote: GoalSpec 快照自动冻结 + 事后纠偏（C1）

**日期**：2026-07-31  
**状态**：ACCEPTED  
**依据**：
- [conversational-delivery-architecture-review-2026-07-31.md](./conversational-delivery-architecture-review-2026-07-31.md) §9.2 C1
- 代码：`api/app_projects.py` drafts → auto-start；`goal_execution_service.py` `SNAPSHOT_GOAL_SPEC_FOR_EXECUTION`
- 历史出处（已归档，非 CURRENT）：`docs/archive/AgentOS-Implementation-Plan-v0.2.md`

## 决策

采纳代码已实现、CURRENT 文档此前未承认的旅程语义：

1. **主链路**：`POST /v1/app-projects/drafts` 在同一请求内创建草稿并 `auto-start`；系统以 `regent-core:auto-snapshot` 写入执行快照（Audit 动词为 `SNAPSHOT_GOAL_SPEC_FOR_EXECUTION`，`confirmation_required=False`）。
2. **确认卡角色**：不再作为「允许开始执行」的门闩；改为**随时纠偏**入口（修订 GoalSpec / 约束 / 停止）。`/confirm` 端点保留，供对话修订路径使用。
3. **字段语义**：`confirmed_by` 可承载机器身份前缀 `regent-core:*`；人类确认须使用可区分的人类 actor 标识。后续若拆字段，另开迁移，不阻塞本决策。
4. **非目标边界**：本决策**不**放宽 PRD §12「无审批的全自动生产发布」；发布审批仍独立（`require_release_human_approval` 默认 true）。

## 同步修订

- `Regent-PRD.md` §4.1 成功路径改为「快照启动 + 事后纠偏」。
- `Regent-Technical-Spec.md` §21 / §25 承认 App-projects 入口族与 Confirm/Start 产品语义。

## 不做

- 不在本 DecisionNote 范围内回退为「先确认再 Start」硬门闩（会破坏现网主链路）。
- 不把 auto-start 解释为人类已确认意图。
- 不因本决策打开 GQ-3 canary 或放宽沙箱要求。
