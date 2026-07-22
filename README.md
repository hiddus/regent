# Regent

Regent 是一个可治理、可审计、可恢复的自主产品生成 Core。它从产品目标出发，获取证据、形成候选假设、修订需求、解析能力、生成应用、完成隔离构建与发布，并根据真实观测决定继续、修订或停止。

## 当前状态

- P0 已形成可运行闭环：目标、工作项、执行、审批、证据、观测、恢复与审计。
- P1 已完成至 `0022`：确认后由 Worker 持久化启动、生成、检查和发布 Preview；对话可查询进度、失败可重试，Outbox 不再无限空转。
- 首个验证项目是“AI 业内人员 App”，它只是验证合同，不会作为预置产品功能写入 Core。
- P1 仍保持整体交付，不拆成 P1A/P1B；开发批次只是实施顺序，不改变验收口径。

## 架构边界

1. Core Kernel：状态机、治理、证据、审计、恢复、预算和安全边界。
2. Certified Capability Pool：可声明、可验证、可替换的通用能力。
3. Generated Apps：由 Core 根据目标、证据与约束生成，不由 Core 预置各种业务页面。

## 开发入口

- [产品需求](./Regent-PRD.md)
- [交付计划](./Regent-Plan.md)
- [P1 Core 能力需求](./docs/p1-core-capability-requirements.md)
- [P1 最终技术规范](./docs/p1-core-final-technical-spec.md)
- [AI 业内人员 App 验证合同](./docs/p1-ai-practitioner-validation-contract.md)
- [文档索引](./docs/README.md)
- [本地开发](./core/README.md)

编码冲突时：产品语义以 PRD 为准，实现契约以 P1 最终技术规范为准，阶段顺序以 Plan 为准；任何冲突必须通过 ADR 或 DecisionRecord 解决。