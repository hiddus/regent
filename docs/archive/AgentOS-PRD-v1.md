# AgentOS V1 产品需求文档

> 版本：1.0  
> 状态：评审定稿  
> 日期：2026-07-16  
> 产品性质：验证型产品（Evidence-seeking Product）

## 1. 产品结论

AgentOS V1 不是通用多 Agent 编排器，也不承诺为任意目标自主创建数字组织。

V1 的产品定义是：

> AgentOS 是一个面向明确目标的受治理能力生成与组织运行系统。它识别完成目标所需的能力，匹配已有能力，配置或组合专用 Agent，必要时构建受限工具或请求人类补充能力，并将这些主体组织为可验证、可调整的临时执行组织。

V1 必须验证两个核心命题：

1. 当现有能力不足时，系统能否识别并有效补充能力，而不是直接失败。
2. 动态组织相对同模型、同工具、同预算的强单 Agent 是否产生净增益。

动态多 Agent 是待验证机制，不是预设正确答案。若实验表明单 Agent 加动态 Skill/Tool 更优，产品应收敛为受治理能力工厂。

## 2. 首个验证场景

首个任务族限定为：

> 在现有 Python 数据处理仓库中，接入一种新的文件格式或本地数据源，交付经过独立测试的数据读取、转换和验证能力。

典型目标：

- 为现有处理流水线增加一种输入格式；
- 将一种新数据结构转换为仓库既有标准模型；
- 为现有转换逻辑补充校验、清洗或错误报告；
- 创建可复用的本地解析器、转换器或验证器。

选择该场景的原因：

- 目标和交付物明确；
- 输入输出容易结构化；
- 可建立不可修改的原测试和隐藏测试；
- 能真实触发能力缺口；
- 可安全验证 Agent 配置和本地 Tool 构建；
- 新能力可以在后续目标中复用。

## 3. V1 硬边界

- 单一已有 Python 代码仓库；
- 单 Goal、单活跃 Plan；
- 只允许修改授权工作区；
- 默认无公网访问；
- 无生产凭证和生产系统访问；
- 不自动合并、部署或发布；
- 原有验收测试不可由执行 Agent 修改；
- 隐藏测试不可被 Builder 或执行 Agent读取；
- 候选 Tool 仅允许本地文件或 JSON 输入输出；
- 候选 Tool 不得长期驻留，不得产生外部副作用；
- Agent、Tool、Prompt、Plan、Policy 和评价标准全部版本化；
- 人工可以随时暂停、接管或终止 Goal。

默认自治预算：

- 同时有效 Agent 最多 4 个；
- 候选 Tool 构建最多 2 次；
- 重规划最多 3 次；
- 单 Task 默认最多重试 3 次；
- 主动 Human Capability Request 最多 2 次；
- 连续 2 轮重规划无可验证进展时暂停。

以上是安全预算上限，不是系统必须使用的配额。

## 4. 目标用户

### 4.1 Goal Owner

- 提交目标；
- 确认成功标准与目标解释；
- 设置预算、期限和自治等级；
- 裁决需求范围变化；
- 最终关闭目标。

### 4.2 Repository Owner

- 授权仓库、分支和可修改目录；
- 指定构建、测试和静态检查命令；
- 审查最终 Change Set；
- 决定是否人工合并。

### 4.3 Approver

- 审批新增依赖、网络访问等超边界申请；
- 审批候选能力进入共享能力池；
- 处理高风险或范围变化决策。

### 4.4 Operator

- 观察运行状态、成本和阻塞；
- 处理基础设施故障与 UNKNOWN_OUTCOME；
- 暂停、恢复和终止运行。

同一个人可以承担多个角色，但系统必须保留责任类型和审计记录。

## 5. 产品不变量

1. 没有独立证据，不认定能力已获得。
2. 没有明确预期增益，不创建额外 Agent。
3. 没有授权，不产生外部副作用。
4. 没有可验证进展，不继续消耗自治预算。
5. 执行者自评不能单独使任务或能力通过。
6. Prompt 或角色名称变化不计为新增能力。
7. 人类是能力提供者和责任主体，不只是审批节点。
8. V1 的交付物是验证通过的变更，而不是“运行了一个多 Agent 组织”。

## 6. 分阶段产品范围

### 6.1 V1a：动态 Agent 配置与组织运行

目标：先证明能力识别、Agent 配置和动态组织是否产生价值。

必须包含：

- Goal Qualification；
- Goal Interpretation，由用户确认；
- Goal、Plan、Task、Run 的持久化运行；
- 人工维护的 Capability Contract；
- Capability Requirement、Inventory 和候选 Gap；
- Single-Agent、Fixed-Team、Dynamic-Organization 三种运行模式；
- Agent Factory，只生成 CandidateAgentSpec；
- 能力测试和认证；
- Organization Candidate、Assignment 和 Coordination Contract；
- Artifact Workspace；
- HumanTask 与 Human Capability Request；
- 独立 Evaluator；
- Autonomy Budget；
- 暂停、恢复、重试、重规划和回退单 Agent；
- 审计、成本和协调开销统计。

V1a 只使用预置 Tool，不自动生成新 Tool。

### 6.2 V1b：受限 Tool Builder

进入条件：V1a 已证明动态配置或组织路径对部分任务产生增量价值。

增加：

- Capability Gap 自动分类；
- ToolSpec；
- 本地低风险 Tool Builder；
- 依赖白名单、静态扫描、Secret 扫描；
- 独立隐藏测试；
- 无网络、无凭证沙箱；
- ToolArtifact 签名、注册、停用和回滚；
- 候选能力的跨 Goal 复用验证。

## 7. 明确不在 V1 范围内

- 强制 Mission 建模；
- 任意领域、任意目标自治；
- 通用 World Model；
- 通用 Simulator；
- 自动构建 Capability Ontology；
- 自动修改治理、权限和评价标准；
- 无限制联网和外部写操作；
- 生产数据库、资金、消息发送和权限管理工具；
- 自主申请云资源；
- 自动部署 AgentOS 自身；
- 复杂矩阵组织；
- 无监督递归自我进化；
- 自动证明动态多 Agent 普遍优于单 Agent。

## 8. 核心领域模型

### 8.1 GoalInterpretation

```text
GoalInterpretation
├─ goalId
├─ desiredOutcome
├─ successCriteria[]
├─ assumptions[]
├─ unknowns[]
├─ unacceptableOutcomes[]
├─ confidence
├─ evidenceRefs[]
└─ version
```

低置信度、低风险目标先进入探索任务；低置信度且高风险时必须请求用户确认。

### 8.2 CapabilityContract

```text
CapabilityContract
├─ capabilityId
├─ domain
├─ capability
├─ variant
├─ inputSchema
├─ outputSchema
├─ preconditions[]
├─ requiredResources[]
├─ requiredAuthorities[]
├─ qualityCriteria[]
├─ benchmarkSuiteRef
├─ evidenceRequirement
├─ riskClass
├─ validScope
└─ version
```

首版采用 `Domain → Capability → Variant` 三级分类，不建立通用能力本体。

### 8.3 CapabilityRequirement

```text
CapabilityRequirement
├─ goalId
├─ taskScope
├─ contractRef
├─ minimumQuality
├─ maximumCost
├─ authorityRequirement
├─ evidenceRequirement
├─ criticality
└─ confidence
```

### 8.4 CapabilityProvider

```text
CapabilityProvider
├─ providerId
├─ providerType: AI_AGENT | HUMAN | TOOL | SERVICE
├─ capabilityClaims[]
├─ authorityScope
├─ availability
├─ costProfile
├─ reliabilityProfile
└─ status
```

不同主体只在任务接口上统一，不在语义上全部称为 Agent。

### 8.5 CapabilityGap

```text
CapabilityGap
├─ requirementId
├─ gapType
├─ missingAspects[]
├─ impact
├─ confidence
├─ evidenceRefs[]
├─ candidateAcquisitionModes[]
└─ status
```

Gap 类型至少区分：

- 能力缺失；
- 能力质量不足；
- 权限不足；
- 资源不可用；
- 输入信息不足；
- 协作失败；
- 偶发执行失败。

失败不自动等于能力缺口。

### 8.6 CapabilityAcquisition

```text
CapabilityAcquisition
├─ gapId
├─ mode: REUSE | CONFIGURE | COMPOSE | BUILD_TOOL | REQUEST_HUMAN
├─ candidateAssets[]
├─ validationPlan
├─ budget
├─ riskClass
└─ status
```

其中：

- CONFIGURE：改变 Prompt、模型、上下文或工具组合；
- COMPOSE：组合既有能力；
- BUILD_TOOL：真正扩展可执行能力边界；
- REQUEST_HUMAN：由人提供信息、判断、权限或制品。

### 8.7 AgentSpec

```text
AgentSpec
├─ purpose
├─ capabilityClaims[]
├─ modelProfile
├─ promptVersion
├─ contextPolicy
├─ allowedTools[]
├─ authorityScope
├─ executionPolicy
├─ evaluationContract
├─ budgetLimit
└─ version
```

Agent Factory 只能生成 CandidateAgentSpec。候选 Agent 通过能力测试后才能获得限定作用域认证。

### 8.8 OrganizationCandidate

```text
OrganizationCandidate
├─ goalId
├─ members[]
├─ assignments[]
├─ capabilityCoverage
├─ unresolvedGaps[]
├─ estimatedExecutionCost
├─ estimatedCoordinationCost
├─ riskEstimate
└─ confidence
```

### 8.9 CoordinationContract

```text
CoordinationContract
├─ sharedObjective
├─ responsibilities[]
├─ artifactOwnership[]
├─ handoffCriteria[]
├─ synchronizationPoints[]
├─ evaluationResponsibilities[]
├─ conflictPolicy
├─ escalationPath
└─ terminationCondition
```

### 8.10 CapabilityCertification

```text
CapabilityCertification
├─ providerId
├─ capabilityContractId
├─ benchmarkVersion
├─ evidenceRefs[]
├─ level: UNVERIFIED | PROVISIONAL | VERIFIED | RESTRICTED | REVOKED
├─ validScope
├─ validUntil
└─ evaluatorId
```

首个目标验证通过后最多授予 PROVISIONAL。只有在至少两个不同 Goal 中成功复用，才能晋级 VERIFIED。

## 9. 核心用户流程

### 9.1 创建并资格化 Goal

用户提交：

- 仓库和目标分支；
- 新输入格式或数据源说明；
- 预期标准输出；
- 示例输入输出；
- 不可修改范围；
- 构建、测试和静态检查命令；
- 预算和截止时间；
- 是否允许新增依赖；
- 人工联系人。

系统生成 GoalInterpretation，用户确认后激活。

### 9.2 能力分析

```text
GoalInterpretation
→ Candidate Capability Requirements
→ Inventory Match
→ Candidate Gaps
→ Gap Classification
```

系统必须展示假设、置信度和证据，不得宣称已准确识别全部能力。

### 9.3 选择执行方式

系统生成三种候选：

- 单 Agent；
- 固定团队；
- 动态组织。

生产运行按策略选择一种；验证期对同一任务执行 A/B/C 对照。

默认从单 Agent 开始，只有满足以下至少一项才允许 Spawn：

- 可以真正并行；
- 需要上下文隔离；
- 需要权限隔离；
- 需要独立评价；
- 存在独立可验收交付物；
- 专业化预期收益大于协调成本。

每次 Spawn 必须记录原因、预期收益和独立交付物。

### 9.4 配置并验证 Agent

```text
Capability Requirement
→ CandidateAgentSpec
→ Benchmark Trial
→ Independent Evaluation
→ PROVISIONAL Certification
→ Goal-level Assignment
```

### 9.5 组织运行

```text
Plan
→ Assign
→ Coordinate
→ Execute
→ Evaluate
→ Organization Review
```

Review 结果：

- KEEP；
- SHRINK；
- REPLACE；
- ADD；
- FALLBACK_SINGLE_AGENT；
- REPLAN；
- ESCALATE。

增加 Agent 不是默认修复动作。

### 9.6 人类能力请求

```text
HumanCapabilityRequest
├─ whyNeeded
├─ expectedOutcome
├─ requiredInputOrArtifact
├─ minimumAuthority
├─ acceptableFormat
├─ deadline
├─ acceptanceCriteria
├─ fallback
└─ expirationAction
```

默认一次提醒、一次升级；无响应后降级或暂停，不占用 Worker。

### 9.7 V1b Tool 构建

```text
CapabilityGap
→ ToolSpec
→ Risk Precheck
→ Generate
→ Dependency/Static/Secret Scan
→ Unit Test
→ Hidden Test
→ Sandbox Trial
→ Sign
→ PROVISIONAL Registration
```

Builder 不得访问隐藏测试。候选 Tool 默认仅限当前 Goal。

### 9.8 交付

系统交付：

- Change Set；
- 变更说明；
- 原测试、隐藏测试和静态检查结果；
- 未解决问题；
- 人工参与记录；
- 成本、耗时和组织调整记录；
- 新增或配置能力清单；
- 能力复用建议；
- 完整审计时间线。

V1 到 Outcome Verified 为止，不自动合并或部署。

## 10. 评价与证据

证据等级：

- E0：执行者自评；
- E1：独立模型评价；
- E2：确定性自动测试；
- E3：外部系统确认；
- E4：人工专家验收；
- E5：持续业务结果。

V1 代码与数据转换能力至少需要：

- 仓库原有不可修改测试；
- 独立隐藏测试；
- 静态与安全检查；
- 人工抽检。

Builder、Runner、Evaluator 在逻辑上分离。评价标准变更必须版本化、审计并重新执行基线。

## 11. 异常流程

### 11.1 目标不明确

- 低风险：创建探索 Task；
- 高风险：请求 Goal Owner 确认。

### 11.2 无合格 Provider

```text
重新配置已有 Agent
→ 组合已有能力
→ V1b 构建候选 Tool
→ 请求人类
→ 降级或暂停
```

### 11.3 测试失败

必须分类为：实现缺陷、需求误解、环境故障、能力缺口、协作失败或验收冲突，再决定重试、重规划或升级。

### 11.4 人工无响应

```text
提醒 → 升级备用处理人 → 降级方案 → 暂停 Goal
```

### 11.5 Tool 构建失败

最多尝试两次，之后必须复用替代能力、请求人类或重规划。

### 11.6 结果不确定

进入 UNKNOWN_OUTCOME，先检查实际状态；无法确认时请求人工，禁止盲目重试。

### 11.7 Agent 冲突

共享 Artifact 禁止最后写入覆盖。创建显式 Issue；涉及需求、范围和验收取舍时升级 Goal Owner。

### 11.8 无进展或预算耗尽

只能选择：缩减范围、申请新预算、请求人工、暂停或失败。执行 Agent 不得自行扩大预算。

## 12. 页面需求

### 12.1 Goal 工作台

显示 Goal、目标解释、成功标准、当前阶段、计划、能力缺口、当前组织、成本、自治预算、阻塞和风险。

支持暂停、恢复、终止、修改目标、触发重规划和人工接管。

### 12.2 组织与能力页

显示 Organization Candidate、成员、职责、创建原因、能力契约、认证证据、协作关系、协调成本和候选资产状态。

支持替换、移除、回退单 Agent、撤销能力和批准复用。

### 12.3 Task 与 Run 页

显示 Task Graph、Run、Artifact、Agent/模型/Prompt/Tool 版本、重试、交接和评价记录。

### 12.4 Human Inbox

显示审批、输入、领域判断和能力请求。每项必须说明原因、期望结果、截止时间、不响应后果、风险和证据。

### 12.5 交付与证据页

显示 Change Set、测试、静态检查、安全发现、Outcome Report、未完成项、新能力和审计时间线。

### 12.6 实验与运营页

显示 A/B/C 结果、Goal 成功率、Gap 判断、Agent 创建、Tool 构建、能力复用、协调成本、人工负担、安全拦截和资源消耗。

## 13. 非功能需求

### 13.1 可靠性

- 进程重启后 Goal、Task、Run、HumanTask 100% 恢复；
- 状态变更与 Outbox 同事务提交；
- Timer 持久化；
- 人工等待不占用 Worker；
- 所有关键操作幂等；
- 所有失败进入明确状态，不允许静默丢失。

### 13.2 安全

- Agent 默认无权限；
- 凭证不得进入 Prompt；
- 候选 Tool 默认断网、无宿主权限；
- 依赖白名单、静态扫描、Secret 扫描和制品签名；
- 受保护测试、Policy 和预算不可被执行 Agent 修改；
- 支持全局紧急暂停。

### 13.3 可追溯性

任一结果必须可追溯到 Goal、Interpretation、Plan、Task、AgentSpec、Prompt、模型、Tool、Policy、Evaluator 和 Evidence 版本。

### 13.4 验证版容量

- 支持 20 个并发 Active Goal；
- 支持 10 个并发 Agent Run；
- 普通状态操作 P95 小于 1 秒；
- READY Task 调度 P95 小于 10 秒；
- HumanTask 完成后 30 秒内恢复相关流程。

## 14. 实验设计

### 14.1 任务集

冻结 30 个任务：

- 10 个能力已充足，正确行为是不创建 Agent/Tool；
- 10 个可通过 Agent 配置或能力组合解决；
- 10 个存在真实解析、转换或验证工具缺口。

另设独立后续任务集，用于验证能力迁移和复用。

### 14.2 对照组

- A：强单 Agent；
- B：固定多 Agent；
- C：AgentOS 动态组织。

三组使用相同模型、初始工具、输入、预算、时间和验收标准。每个任务每种方案至少运行 3 次。

实验另设 1、2、4 Agent 档位观察边际收益；4 Agent 仍是安全上限，不是目标数量。

### 14.3 核心指标

- 端到端成功率；
- 独立质量评分；
- 完成时间；
- Token 与费用；
- 人工耗时和请求次数；
- Gap Precision/Recall；
- 候选能力测试通过率；
- 错误能力注册率；
- 协调消息、交接失败、重复劳动和返工；
- 新能力跨 Goal 复用率；
- 安全违规和回滚。

### 14.4 核心假设

H1：系统能够区分能力缺口与普通执行失败。  
H2：配置或构建的新能力能通过独立验证并解决原任务。  
H3：动态组织在部分任务上相对单 Agent 产生净增益。  
H4：新能力可在后续 Goal 中复用并降低成本或时间。  
H5：系统能够在组织无收益时收缩或回退单 Agent。  

这些是实验假设，不是预设成立的功能事实。

## 15. 产品决策门槛

### 15.1 继续 AgentOS 动态组织方向

满足以下主要条件：

- C 相对 A 的端到端成功率提高至少 15 个百分点；或在成功率下降不超过 3 个百分点时完成时间降低至少 25%；
- C 的成本不超过 A 的 2 倍；
- 人工负担不高于 A；
- 严重安全违规为 0；
- 能力缺口识别和能力补充达到预先冻结的门槛；
- 至少一个候选能力在第二个 Goal 成功复用。

### 15.2 转向“单 Agent + 受治理能力工厂”

若 Tool/Skill/Agent 配置产生明显价值，但动态组织连续两轮实验未优于单 Agent，则停止投资复杂 Organization Designer，将能力工厂作为主产品。

### 15.3 转向固定模板组织

若固定多 Agent 与动态组织表现接近，则采用少量固定组织模板，不继续扩展动态组织设计。

### 15.4 停止通用 AgentOS 扩展

若能力缺口无法稳定识别、新能力无法独立验证、复用率长期低于 20%，或人工维护成本高于节省价值，则停止通用化，转向垂直数据工具产品。

## 16. 里程碑

### M1：可靠目标内核

Goal Interpretation、Plan/Task/Run、状态机、Worker、Timer、Outbox、Artifact、审计、暂停恢复。

验收：系统重启后可以继续一个多步骤数据接入任务。

### M2：可验证能力系统

Capability Contract、Inventory、Candidate Gap、Agent Factory、Builder/Runner/Evaluator 分离、能力认证。

验收：动态配置的 Agent 能通过独立能力测试。

### M3：最小自治组织

Organization Candidate、Assignment、Coordination Contract、Handoff、Organization Review、Autonomy Budget。

验收：系统能解释为何增员、替换失败 Agent，并在无收益时回退单 Agent。

### M4：V1a 对照验证

运行 A/B/C 冻结任务集，形成阶段性产品决策。

### M5：V1b 受限 Tool Builder

ToolSpec、构建、安全扫描、独立测试、沙箱、签名、注册与撤销。

验收：系统发现真实本地工具缺口后，构建候选能力并通过独立验证。

### M6：能力复用验证

使用独立后续任务集验证 PROVISIONAL 能力；成功跨两个 Goal 后晋级 VERIFIED。

## 17. 完成定义

V1 完成不等于证明通用 AgentOS 已成立。

V1 完成意味着：

> 在受限的 Python 数据接入与转换任务中，系统能够持久推进目标，生成带假设的能力需求，匹配或配置 Agent，形成有明确协作契约和自治预算的临时组织，并通过独立证据完成交付；在 V1b 中还能构建经过安全验证的本地低风险工具。系统已通过与单 Agent、固定多 Agent 的对照实验，并据证据决定继续动态组织、转向能力工厂或采用固定模板。

