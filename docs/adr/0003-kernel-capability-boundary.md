# ADR 0003：最小 Core、能力池与生成 App 边界

状态：Accepted  
日期：2026-07-18

## 决策

Regent 分为三层：

1. Core Kernel：状态、治理、证据、任务、预算、可信执行原语；
2. Certified Capability Pool：可版本化、认证、晋级和撤销的 Skill、Tool、Connector、Runtime Profile；
3. Generated Apps：由 Goal 运行时产生的独立业务工程。

Core 预置机制，不预置业务功能。

## Core Kernel 允许内容

- Goal、Work、Run、Artifact、Evidence、Audit、Outbox、Timer；
- Policy、Permit、HumanTask、Secret Broker；
- 结构化模型 Provider；
- 受限 Evidence Connector 端口；
- WorkspaceWriter；
- SandboxDriver；
- ArtifactStore；
- PreviewDeploymentProvider 端口；
- 能力注册与认证框架；
- 通用 Decision 与资源记录。

## Capability Pool 内容

- Product Discovery 启动能力；
- App Requirement 生成能力；
- Capability Resolver；
- 一个 Bootstrap Runtime Profile；
- 通用代码生成能力；
- 来源 Connector；
- 构建、测试和部署 Adapter。

它们可以随 Goal 增长，但必须经过认证。它们不是 Core 领域业务模型。

## Generated App 内容

用户、内容、订单、订阅、页面、业务指标和行业流程只存在于生成 App。Core 不 import App，App 不 import Core。

## 自举边界

P1 的可信种子由工程人员实现并版本化。Core 可以生成候选 Core 修改，但不得运行中自改；候选必须在独立 Workspace 完整回归并经人工审批。

## 延后

多 Goal 资源竞争、完整长期记忆、Champion/Challenger 平台、通用自我升级和生产自动发布延后至 P1.1 或 P2。
