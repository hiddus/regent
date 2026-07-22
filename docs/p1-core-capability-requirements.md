# Regent P1 Core 功能需求

> 状态：唯一有效 P1 产品基线  
> 日期：2026-07-18  
> 目标：让 Core 能从普通长期 Goal 自主发现产品机会、生成 App 需求、创建并验证独立 App，并依据外部证据持续调整。

## 0. P1 最小范围

P1 只要求完成一次未知产品 Goal 到 Preview，并根据真实 Observation 形成一次 CONTINUE、REVISE 或 STOP 决策。多 Goal 调度、Champion/Challenger 平台、完整长期记忆、多 Runtime Profile 和自动生产发布延后至 P1.1/P2。具体编码范围以 p1-core-final-technical-spec.md 为准。
## 1. 纠偏原则

P1 交付物是 Core 的通用能力，不是人工预先定义的 AI 行业网站。

AI 从业者 App 只是第一个验证 Goal。验证工程只能冻结原始 Goal、资源、约束、评价口径和阶段 Gate，不得冻结：

- 产品形态；
- 目标细分用户；
- 功能列表；
- 页面结构；
- 内容类型；
- 技术栈；
- 增长策略；
- 收费方案；
- Agent、Tool 或 Workflow。

这些必须由 Core 在运行时产生，并以版本化 Artifact 和 Evidence 解释。

## 2. P1 目标能力

Core 接收长期 Goal 后，应完成：

1. 解释目标和不可修改约束；
2. 识别未知项和必须验证的产品假设；
3. 研究授权范围内的用户问题和候选机会；
4. 生成多个 ProductHypothesis；
5. 使用证据、成本、风险和可逆性排序；
6. 选择最小可验证候选；
7. 生成版本化 AppRequirement；
8. 推导所需能力并补齐低风险缺口；
9. 创建独立 App Workspace；
10. 生成、构建、测试并部署 Preview；
11. 经批准后发布；
12. 接收外部 Observation；
13. 判断继续、调整、回滚或停止；
14. 保存可复用能力和经过验证的经验。

## 3. Core 新增功能

### 3.1 Product Discovery

输入：GoalSpec、可用资源、授权边界、历史 Evidence。

输出：

- OpportunityResearch；
- ProblemCandidate 列表；
- ProductHypothesis 列表；
- HypothesisDecision。

每个 ProductHypothesis 至少包含：

- 假设的用户群；
- 用户问题；
- 价值主张；
- 候选解决方式；
- 最小验证方式；
- 成功和失败信号；
- 主要风险；
- 所需能力；
- 预计成本和时间；
- 可逆性；
- 证据引用；
- 未知项。

规则：

- 首轮至少比较两个实质不同的候选；
- 没有 Evidence 的内容必须标记为假设；
- 不得把模型自信度当成市场证据；
- 选择结果必须可解释；
- 所有候选都不值得验证时允许进入 BLOCKED 或 EXHAUSTED。

### 3.2 App Requirement Generation

Core 根据选中的 ProductHypothesis 生成 AppRequirement，而不是由用户预填。

AppRequirement 是版本化通用 Artifact，至少包含：

- product_outcome；
- target_users；
- problem_statement；
- value_proposition；
- user_journeys；
- functional_requirements；
- non_functional_requirements；
- data_requirements；
- external_integrations；
- success_metrics；
- event_definitions；
- risks；
- governance_requirements；
- release_gates；
- open_questions；
- assumptions；
- source_evidence；
- version。

规则：

- 原始 Goal 和硬约束不可被需求生成器修改；
- 不确定项必须保留来源和置信边界；
- 需求变更创建新版本；
- 已开始执行且依赖旧需求的 Work 需要阻塞或重规划；
- App 业务字段保存在 Artifact 中，不成为 Core 强类型业务模型。

### 3.3 Capability Growth

Core 必须通用化 P0 EVT 能力补齐：

- 从 AppRequirement 推导 CapabilityRequirement；
- 分类为已有能力、配置缺口、组合缺口、工具缺口、权限缺口、资源缺口或信息缺口；
- 优先复用，其次配置、组合，最后才构建；
- 构建低风险 Skill/Tool；
- 沙箱构建、供应链检查、公开和隐藏测试；
- Goal 级认证；
- 后续独立 Goal 验证后晋级；
- 失败、越权或供应链变化后撤销；
- 保存成本、成功率和复用证据。

### 3.4 Generic App Factory

Core 需要提供通用交付对象：

- AppRequirementRecord；
- AppWorkspace；
- AppBuild；
- ReleaseCandidate；
- Deployment；
- DeploymentObservation。

Core 可以理解工作区、构建和发布，但不得理解任何具体 App 的业务对象。

Workspace 生成要求：

- 从空目录开始；
- App 独立依赖、数据库、迁移、测试和 Dockerfile；
- App 不导入 Core；
- Core 不导入 App；
- 生成 source manifest 和内容 hash；
- 同一冻结输入可重建等价 Workspace；
- 路径只允许位于配置的 workspace root；
- 禁止目录穿越和符号链接逃逸。

### 3.5 Sandboxed Build and Verification

Build 必须：

- Worker 异步执行；
- 非 root；
- 默认断网；
- 只读输入、独立输出；
- CPU、内存、磁盘和超时限制；
- 固定依赖源和锁文件；
- 无生产 Secret；
- 执行格式、类型、单元、集成、架构和安全测试；
- 生成 SBOM、测试报告、Artifact hash 和 Evidence；
- 失败不能生成 ReleaseCandidate；
- UNKNOWN 必须先对账，不能盲目重跑。

### 3.6 Preview, Release and Rollback

- PASSED Build 可生成不可变 ReleaseCandidate；
- Preview 发布使用一次性 Permit；
- Production 发布必须 HumanTask 批准；
- 相同幂等键不得重复部署；
- 保存外部 deployment ID 和实际制品摘要；
- 支持查询、UNKNOWN 对账和回滚；
- 回滚不能改写历史 Deployment；
- 发布后的 App 可脱离 Core 提供业务读取服务。

### 3.7 Observation, Experiment and Replan

Core 必须允许 App 自己定义版本化指标，不预置付费、DAU、内容或订阅业务模型。

要求：

- 事件具备幂等 ID、来源、定义版本、内部和 Bot 标记；
- App 服务端签名或使用等价防篡改机制；
- Observation 绑定 Goal、AppRequirement 和 Release 版本；
- 支持阶段 Gate；
- P1 只保存单一验证分配与归因；Champion/Challenger 平台延后；
- 记录实验分配和归因；
- 低于阈值产生诊断 Work，不直接修改生产；
- 每轮最多修改一个主要产品假设；
- 形成继续、调整、回滚或停止 DecisionRecord。

### 3.8 最小 Handoff 与验证经验

HandoffContract 至少包含：

- 发送和接收 Actor；
- 输入版本；
- 输出 Artifact；
- 验收标准；
- 未解决问题；
- 风险；
- 预算消耗；
- Evidence；
- 接收确认。

长期记忆只保存经过验证的 ExperienceRecord：

- 适用范围；
- 支撑 Evidence；
- 成功或失败条件；
- 能力版本；
- 过期条件；
- 可否跨 Goal 复用。

未经验证的模型陈述不能晋级为长期记忆。

### 3.9 P1.1 延后：Multi-goal Resource Governance

- Goal 优先级；
- 总模型、工具、构建和人工预算；
- 资源预留与消费；
- 公平调度；
- 高优先级抢占只能暂停低优先级可恢复工作；
- 不得中断已开始的不可逆副作用；
- 预算耗尽时产生可解释的 BLOCKED 或 EXHAUSTED。

## 4. 治理要求

以下动作必须经过 Policy 和 ExecutionPermit：

- 外部网络读取；
- 依赖下载；
- 代码执行；
- Preview 部署；
- Production 发布；
- 域名或基础设施变更；
- 批量通知；
- 支付接入；
- 数据删除；
- 回滚和公开内容撤回。

高风险动作必须 HumanTask 批准。Agent 不得修改根 Goal、硬约束、指标口径、权限上限和治理规则。

## 5. 明确非目标

- 预先设计 AI 行业 App；
- 通用 World Model；
- 任意语言和任意基础设施；
- 高风险 Tool 自动发布；
- 根 Goal 或治理规则自主修改；
- 无监督递归自改；
- Agent 市场；
- 默认动态组织；
- 微服务、Kafka、多区域和 Kubernetes。

## 6. P1 验收

### Core 功能验收

1. 普通长期 Goal 产生至少两个 ProductHypothesis；
2. Core 依据 Evidence 选择或拒绝候选；
3. 自动生成版本化 AppRequirement；
4. 自动推导并补齐至少一个非固定样例的低风险能力缺口；
5. 从空目录生成独立 App；
6. 完成沙箱 Build、测试和 Preview；
7. 生产发布受到 Permit 和审批控制；
8. App Observation 可以触发诊断和需求新版本；
9. 能力或经验可以晋级、撤销并在独立 Goal 复用；
10. 全过程可恢复、幂等并可审计。

### 外部 Challenge 验收

只向 Core 提交冻结的 AI 从业者长期 Goal、约束和资源。Core 自行决定产品方案。验证方检查：

- 是否真正比较候选；
- 需求是否引用证据；
- 是否生成可体验 App；
- 是否获得真实目标用户行为；
- 是否根据数据形成可追溯决策；
- 是否没有向 Core 加入该 App 的业务模型。

## 7. P1 完成定义

P1 完成不要求达到 100 名付费用户。P1 完成证明 Core 已具备产品发现、需求生成、能力增长、App 创建、受控发布和证据驱动迭代的通用闭环，并通过 AI 从业者 Goal 完成一次无预设产品方案的真实验证。
