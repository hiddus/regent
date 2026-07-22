# AgentOS V1 技术框架与技术需求

> **已停止作为编码基线。** 本文将 `ChallengeContract` 误设为产品输入，并混合了 Core、场景应用与验证工程。修正后的编码基线见 [AgentOS-Implementation-Plan-v0.2.md](./AgentOS-Implementation-Plan-v0.2.md)：用户输入为自然语言 Goal；`GoalSpec` 是 Core 内部解释结果；Challenge 仅属于独立验证工程。
>
> 版本：1.0  
> 状态：三方技术评审收敛稿  
> 日期：2026-07-16

## 1. 技术定位

AgentOS V1 是一个有界自治组织运行时：接收不携带解决方案的 `ChallengeContract`，在稳定治理内核提供的权限和资源边界内，自主解释目标、提出假设、识别和获取能力、形成临时人机组织、构建并运营数字产品，并依据独立外部结果持续调整。

技术形态：

```text
确定性控制内核
+ 概率性决策 Agent
+ 可版本化能力资产
+ 隔离执行环境
+ 外部事实与 KPI 评价
```

不得实现为单个超级 Agent 的无限循环。

## 2. Challenge 边界

### 2.1 输入契约

```text
ChallengeContract
├─ id / version
├─ goalStatement
├─ successCriteria[]
├─ constraints[]
├─ initialResources[]
├─ authorityBoundary
├─ autonomyBudget
├─ evaluatorBindings[]
├─ reportingCadence
└─ terminationPolicy
```

Challenge 只提供目标、成功口径、边界、资源和独立评价，不得携带：

- AgentSpec；
- ToolSpec；
- 产品功能清单；
- 技术架构；
- 组织结构；
- Workflow；
- 内容、定价和增长策略。

### 2.2 热加载

```text
Discover
→ Schema Validate
→ Signature/Provenance Check
→ Authority Check
→ Freeze Version
→ Create Goal
```

运行中的 Goal 固定 Challenge 快照。Challenge 更新必须创建新版本并触发 Goal Review，不能静默替换。

## 3. 资源模型

服务器和 AI 算力已提供，只说明现金计算成本接近零，不表示资源无限。

```text
ResourceEnvelope
├─ computeQuota
├─ modelQuota
├─ concurrencyQuota
├─ storageQuota
├─ networkQuota
├─ externalServiceQuota
├─ externalAccountQuota
├─ humanAttentionQuota
├─ publishingQuota
├─ timeBudget
├─ riskExposureLimit
└─ cashLimit
```

必须记录影子成本：模型调用、算力时长、存储、带宽、人工工时和外部服务等价成本，用于和单 Agent、固定组织比较。

## 4. 四个核心闭环

```text
目标解释闭环：
Goal → Hypothesis → Exploration → Deliverable

能力闭环：
Requirement → Gap → Acquire → Validate → Certify

组织闭环：
Form → Assign → Execute → Measure → Adjust/Dissolve

进化闭环：
History → Improvement → Candidate → Experiment → Promote/Rollback
```

运行速率：

- 执行循环：分钟至小时；
- 组织调整：小时至天；
- 能力进化：天至周。

单次任务失败不得直接触发组织重建。

## 5. 技术模块

V1 采用模块化单体加隔离 Worker：

```text
agentos/
├─ challenges/
├─ goals/
├─ deliberation/
├─ capabilities/
├─ organizations/
├─ work/
├─ actors/
├─ tools/
├─ runtime/
├─ artifacts/
├─ evaluation/
├─ governance/
├─ observations/
├─ evolution/
├─ events/
└─ observability/
```

### 5.1 Challenge Gateway

- 加载、校验和版本冻结 Challenge；
- 权限和评价绑定检查；
- 创建 Goal。

### 5.2 Goal & Deliberation Kernel

- 多版本 Goal Interpretation；
- 假设、未知项、证据和置信度；
- Exploration Work 与 Delivery Work；
- 候选计划和重规划；
- 阶段目标、转向与终止。

LLM 只能输出 Proposal/Hypothesis，不能直接更新权威状态。

### 5.3 Capability Kernel

- Capability Requirement；
- Provider Claim；
- Match 与 Gap Diagnosis；
- Acquisition；
- Validation 与 Certification；
- Assignment 与 Performance Review；
- Retain、Restrict、Revoke。

Gap 至少区分：Knowledge、Tool、Authority、Resource、Coordination、Quality、Goal Ambiguity、Ordinary Failure。

### 5.4 Organization Kernel

- Candidate Organization；
- Assignment、责任和权限；
- Artifact Ownership 与 Handoff；
- Organization Review；
- Keep、Reassign、Add、Replace、Merge、Remove、Dissolve；
- 回退单 Agent。

创建新 Agent 必须说明独立交付物、预期收益和协调成本。

### 5.5 Actor Runtime

```text
AIActor
HumanActor
ToolActor
ServiceActor
```

统一控制协议：

```text
describe → accept → start → heartbeat → checkpoint
→ complete/fail/cancel
```

执行适配器必须分离。

### 5.6 Work Runtime

- Work Graph 与依赖；
- Run、Attempt、Lease、Timer；
- 重试、暂停、恢复；
- HumanTask；
- UNKNOWN_OUTCOME；
- Artifact-first 协作。

### 5.7 Agent Factory

P0 中的新 Agent 是：

```text
Versioned AgentSpec
+ Model Profile
+ Instructions
+ Capability Bindings
+ Tool Allowlist
+ Context Policy
+ Authority Scope
+ Evaluation Contract
```

不是动态创建任意常驻服务。只改 Prompt 记为 `CONFIGURE`，不记为新增能力。

### 5.8 Skill/Tool Factory

P1 支持低风险候选能力：产品代码、内容处理 Skill、本地分析验证工具、API/CLI 包装器和测试工具。

```text
Specification
→ Generate
→ Static Check
→ Generated Tests
→ Independent Tests
→ Sandbox
→ Security Check
→ Sign
→ Limited Use
→ Promote/Rollback
```

### 5.9 Evaluation Kernel

- Builder、Runner、Evaluator、Releaser 逻辑和权限分离；
- Task、Capability、Organization、Goal 四级评价；
- 确定性测试、独立模型、外部 KPI 和人工评价；
- 评价规则和 KPI 口径不可由执行组织修改。

### 5.10 Governance Kernel

- Policy、Authority、Risk、Quota、Approval；
- Autonomy Budget 与 Progress Gate；
- 短期 ExecutionPermit；
- Audit 与 Kill Switch；
- 公开发布、支付、营销、个人数据和不可逆变更审批。

## 6. 最小数据模型

```text
Challenge
Goal
GoalInterpretation
Hypothesis
CapabilityRequirement
CapabilityProvider
CapabilityCertification
OrganizationInstance
Assignment
CoordinationContract
Work
Run
Artifact
Evidence
Evaluation
HumanTask
PolicyDecision
ExecutionPermit
ToolInvocation
Observation
DomainEvent
AuditRecord
```

所有对象必须带 Goal/Tenant Scope、版本、创建者、时间和关联链路。

## 7. 状态机

### 7.1 Goal

```text
DRAFT → QUALIFYING → EXPLORING → EXECUTING → OPERATING
→ PAUSED → ACHIEVED | PARTIAL | FAILED | CANCELLED
```

### 7.2 Work

```text
BLOCKED → READY → ASSIGNED → RUNNING → EVALUATING → COMPLETED
```

异常分支：

```text
WAITING_EXTERNAL
WAITING_HUMAN
RETRY_WAIT
UNKNOWN_OUTCOME
REPLAN_REQUIRED
FAILED
CANCELLED
```

### 7.3 Capability Acquisition

```text
IDENTIFIED → MATCHING
→ CONFIGURING | COMPOSING | BUILDING | REQUESTING_HUMAN
→ VALIDATING → PROVISIONAL
→ CERTIFIED | REJECTED | REVOKED
```

### 7.4 Organization

```text
PROPOSED → CHECKING → ACTIVE → REVIEWING → ADJUSTING
→ ACTIVE → DISSOLVED | FAILED
```

状态转换只能由确定性应用服务执行。

## 8. 持久化与部署

### 8.1 P0 基础设施

```text
PostgreSQL
+ Transactional Outbox
+ Database-backed Queue
+ Durable Timer
+ Worker Lease/Heartbeat
+ Object Storage
```

同一事务完成状态、AuditRecord 和 OutboxEvent 写入。

第一版不需要 Kafka、微服务或 Kubernetes。

### 8.2 部署拓扑

```text
Control API / Console
        │
PostgreSQL ─ Object Storage
        │
Outbox / Timer Dispatcher
        │
Workflow Workers
├─ Deliberation Worker
├─ AI Actor Worker
├─ Evaluation Worker
└─ Tool Sandbox Worker
```

每个 Goal 使用独立 Workspace、权限、密钥、KPI Adapter 和资源配额。

### 8.3 可靠性

- eventId 去重；
- aggregateVersion 乐观锁；
- Inbox 消费记录；
- Lease 过期重取；
- 指数退避；
- Dead-letter/Manual Review；
- 外部副作用幂等键；
- UNKNOWN_OUTCOME 对账后再决定重试。

## 9. LLM 与确定性边界

### LLM 适合

- 目标解释；
- 市场和产品假设；
- 探索和计划候选；
- 能力需求候选；
- Candidate Organization；
- AgentSpec、Skill 和代码候选；
- 内容理解与生成；
- 失败诊断和改进建议。

### 必须确定性

- Goal、KPI、预算、权限和版本；
- 状态机和依赖推进；
- Schema 校验；
- Tool Gateway 和 ExecutionPermit；
- 预算核销；
- 并发、Lease 和 Timer；
- 外部副作用幂等；
- Artifact 哈希和来源；
- 支付、DAU、留存等 KPI 计算；
- 发布门槛、Kill Switch 和回滚；
- Capability 晋级状态。

## 10. 记忆与归因

### 10.1 分层记忆

- Operational State：Goal、Work、Run、Organization、Budget；
- Artifact Memory：研究、设计、代码、内容、实验、决策；
- Episodic Memory：情境—行动—预测—结果—评价；
- Capability Memory：已验证 AgentSpec、Skill、Tool；
- Strategic Memory：被证伪假设、有效用户、渠道和组织调整。

聊天记录不是权威来源。未经验证的 LLM 总结只能进入候选记忆。

### 10.2 变更归因

```text
ChangeProposal
├─ trigger
├─ hypothesis
├─ changedVariable
├─ expectedEffect
├─ baselineWindow
├─ observationWindow
├─ result
└─ confidence
```

优先使用 A/B、Champion/Challenger、时间窗对比和 Holdout。没有归因证据，不能证明组织调整有效。

## 11. 安全与供应链

### 11.1 ExecutionPermit

```text
ExecutionPermit
├─ actorId
├─ action
├─ targetEnvironment
├─ parameterHash
├─ resourceLimit
├─ dataScope
├─ networkScope
├─ validUntil
├─ usageCount
└─ approvalRef
```

默认拒绝、最小范围、短期有效、绑定参数、使用后失效。

### 11.2 沙箱

- 非特权用户；
- 只读基础镜像；
- 临时文件系统；
- CPU、内存、磁盘、进程和时间限制；
- 默认无网络；
- 无宿主目录和生产凭证；
- 输出仅写 Artifact 目录；
- 完整文件、进程和网络审计。

### 11.3 制品供应链

每个部署制品必须包含：

```text
Source Commit
Build Recipe
Dependency Lock
SBOM
Test Report
Security Scan
Artifact Hash
Signer
Policy Decision
Approval Record
Deployment Manifest
```

生产只接受签名的不可变制品，不能从 Agent 工作区直接部署。

### 11.4 发布

```text
开发 → 预览 → 预发布 → Canary → 放量 → 稳定生产
```

首次公开上线、域名、支付、隐私条款、营销消息、破坏性迁移和权限扩大默认人工审批。

## 12. 外部 KPI 与反作弊

业务 KPI 必须由 AgentOS 无权修改的独立系统产生。

### 12.1 有效付费用户

权威来源：支付流水、退款、账号和有效使用事件。Agent 无权修改原始数据和口径。

### 12.2 有效 DAU

```text
去重真实用户
+ 有效阅读行为
+ 最低停留/滚动/阅读进度
- 爬虫、内部账号、异常设备/IP、自动流量和流量农场
```

需要服务端事件签名、幂等 ID、Bot 检测、内部流量标记和指标版本化。

## 13. 两个长期 Challenge

### 13.1 Challenge A：AI 从业者 App

```text
Goal：180天获得100名有效付费用户
约束：真实支付、事实可信、版权、隐私、无刷量
资源：服务器、AI算力、受控网络、代码执行、人类请求渠道
评价：独立支付、退款、活跃、留存和内容抽检
```

### 13.2 Challenge B：成年读者童话网站

```text
Goal：连续7天平均有效DAU达到10000
约束：真实流量、原创、版权、内容安全和隐私
资源：服务器、AI算力、受控网络、代码执行、人类请求渠道
评价：独立分析、反作弊、留存、内容安全与原创抽检
```

两个 Challenge 均不得预置 Agent、Tool、Workflow、产品方案和增长路径。

运行安排：两个 Challenge 同时可加载；A 先进入真实商业运营，B 先运行探索、原型和小规模真实用户验证，满足100、1000、3000有效DAU等 Gate 后再扩大到10000。

## 14. P0、P1 与后置范围

### P0：验证有界自组织

- Challenge 加载与版本冻结；
- Goal/Work/Run 持久化和重启恢复；
- Goal Interpretation 与假设台账；
- Capability Requirement、Gap、Certification；
- AgentSpec 动态配置；
- Candidate Organization、Assignment、Review；
- AI/Human/Tool/Service Actor Runtime；
- Artifact/Evidence；
- 外部 KPI Observation；
- 独立 Evaluation；
- Policy、Quota、Approval、Kill Switch；
- 单 Agent、固定组织、动态组织实验模式；
- 两个 Challenge 可同时加载并运行早期阶段。

P0 不建设通用 Tool Builder。

### P1：验证能力增长

- 低风险 Skill/Tool Builder；
- 沙箱构建和供应链安全；
- Handoff Contract；
- 长期记忆；
- Experiment/Attribution；
- Champion/Challenger；
- 组织成本和自动收缩；
- Capability 晋级、撤销和跨阶段复用。

### 后置

- 通用 World Model；
- 自动能力本体；
- 高风险 Tool 自动发布；
- 根 Goal 自主修改；
- 治理规则自修改；
- 无监督递归自改；
- Agent 市场；
- 微服务、Kafka 和多区域架构。

## 15. SLO 与上线门槛

### 15.1 平台 SLO

| 指标 | 目标 |
|---|---:|
| 已提交状态恢复率 | 100% |
| 审计覆盖率 | 100% |
| 未授权 Tool 拦截率 | 100% |
| Challenge/Asset 版本可追溯率 | 100% |
| 人工等待 Worker 占用 | 0 |
| 重启后 Work 恢复 | ≤60秒 |
| 幂等副作用重复率 | 0 |
| 配额越界执行 | 0 |
| 关键 Artifact 哈希覆盖率 | 100% |

### 15.2 公共测试上线

- 无高危漏洞和明文凭证；
- 生产无 Agent 常驻 Shell 或管理员权限；
- 预览、Canary、回滚和 Kill Switch 有效；
- 隐私、条款、投诉、删除和内容撤回入口可用；
- 分析指标口径经过验证；
- 完成一次备份恢复演练。

### 15.3 支付上线

- 使用第三方托管支付；
- AgentOS 不处理银行卡明文；
- 价格、退款和订阅规则人工批准；
- Webhook 验签、幂等和对账通过；
- 测试与生产支付隔离。

## 16. 测试需求

- 单元：状态、权限、能力匹配、Spawn、幂等、Schema；
- 集成：Goal→Work→Run→Evaluation、HumanTask、Outbox、Challenge 冻结；
- 故障注入：Worker 崩溃、Tool 超时、Actor 失联、KPI 冲突、配额耗尽；
- 安全：Prompt Injection、越权、Secret 泄露、Artifact 污染、Evaluator 篡改；
- 长稳：72小时、7天影子、30天受控运营；
- 对照：相同子目标运行单 Agent、固定组织和动态组织。

## 17. 技术成立条件

AgentOS 的核心命题只有在以下条件成立时才被验证：

1. 两个 Challenge 不携带解法即可运行；
2. Core 不包含 App、新闻、童话、订阅、DAU 等领域对象；
3. 系统能形成不同的组织、产品和执行路径；
4. 系统能识别和获取真实能力缺口；
5. 所有演化可恢复、审计、限额和回退；
6. 外部 KPI 能驱动计划和组织调整；
7. 至少一个场景中动态组织相对同资源基线产生可测净收益。

若能力构建有效但动态组织无增益，转向“强 Agent＋受治理能力工厂”；若固定组织持续更优，转向固定组织模板平台。
