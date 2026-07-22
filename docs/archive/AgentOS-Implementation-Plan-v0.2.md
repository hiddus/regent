# AgentOS V0.2 可落地技术方案

> 状态：编码基线  
> 日期：2026-07-16  
> 核心修正：Core、场景应用、验证工程完全分离；用户继续以自然语言目标交互，不引入 Challenge 用户标准。

## 1. 三个独立工程

```text
agentos-workspace/
├─ agentos-core/             # 通用自治组织底座
├─ scenario-apps/            # AgentOS 创建和运营的真实产品
│  ├─ ai-professional-app/
│  └─ adult-fairy-tales/
└─ agentos-validation/       # 对照实验、指标和验证数据
```

三者可以位于同一开发工作区，但必须是独立包、独立依赖和独立数据边界；生产中可以独立部署。

依赖方向：

```text
scenario-apps ──HTTP/MCP/A2A/Artifact──▶ agentos-core
agentos-validation ──Public API──▶ agentos-core

agentos-core ─X─▶ scenario-apps 源码包
agentos-core ─X─▶ agentos-validation 内部实现
```

Core 不导入场景代码，不持有场景业务表，不认识 App、新闻、童话、订阅或 DAU。

## 2. 用户输入保持自然

用户入口仍是：

```text
自然语言 Goal
+ 可选附件
+ 用户主动声明的约束、资源或偏好
```

不要求用户学习或填写 `ChallengeContract`。

示例：

```text
做一个面向 AI 从业者的 App，长期提供有价值的信息，
第一阶段达到 100 个有效付费用户。
```

Core 内部由 `Goal Intake` 生成可版本化的内部对象：

```text
GoalSpec
├─ originalInput
├─ interpretedOutcome
├─ candidateSuccessMeasures[]
├─ explicitConstraints[]
├─ inferredAssumptions[]
├─ unknowns[]
├─ availableResources[]
├─ authorityBoundary
└─ version
```

`GoalSpec` 是内部运行快照，不是新的用户输入标准。系统可以在低风险范围内带假设探索；只有涉及根目标、硬约束、权限和高影响行动时才请求用户确认。

## 3. Challenge 的正确位置

`Challenge` 只属于技术验证工程：

```text
agentos-validation/
└─ experiments/
   ├─ ai-product-experiment.yaml
   └─ fairy-tales-experiment.yaml
```

它用于：

- 固定实验输入；
- 控制单 Agent、固定组织和动态组织三组变量；
- 定义隐藏评价；
- 注入故障和资源变化；
- 判断 AgentOS 技术命题是否成立。

它不是 AgentOS 的产品 API，也不是普通用户需要接触的概念。

## 4. AgentOS Core 工程

```text
agentos-core/
├─ pyproject.toml
├─ alembic.ini
├─ docker-compose.yml
├─ src/agentos/
│  ├─ api/
│  ├─ domain/
│  │  ├─ goals/
│  │  ├─ capabilities/
│  │  ├─ organizations/
│  │  ├─ work/
│  │  ├─ actors/
│  │  ├─ artifacts/
│  │  ├─ evaluation/
│  │  └─ governance/
│  ├─ application/
│  ├─ infrastructure/
│  │  ├─ db/
│  │  ├─ model_providers/
│  │  ├─ object_store/
│  │  └─ sandbox/
│  ├─ runtime/
│  │  ├─ workers/
│  │  ├─ timers/
│  │  └─ leases/
│  └─ observability/
├─ tests/
└─ docs/adr/
```

Core 技术栈：Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、PostgreSQL、pytest、OpenTelemetry。

## 5. 场景应用工程

场景应用是 AgentOS 要创建和运营的真实产品，不是插件配置。

```text
scenario-apps/ai-professional-app/
├─ product source code
├─ product database migrations
├─ product tests
├─ deployment manifests
└─ product documentation

scenario-apps/adult-fairy-tales/
├─ product source code
├─ product database migrations
├─ product tests
├─ deployment manifests
└─ product documentation
```

每个应用：

- 有自己的技术栈和依赖；
- 有自己的业务数据库；
- 有自己的支付、分析和内容模型；
- 有自己的部署与发布生命周期；
- 通过受限 Workspace/Tool/Service 接口被 AgentOS 操作；
- 通过外部 Observation Adapter 向 AgentOS 返回结果。

应用是否使用 Core SDK 由 AgentOS 自主选择，Core 不要求应用继承某种领域框架。

## 6. 验证工程

```text
agentos-validation/
├─ pyproject.toml
├─ src/validation/
│  ├─ experiment_runner/
│  ├─ baseline_agents/
│  ├─ fixed_organizations/
│  ├─ metric_collectors/
│  ├─ fault_injection/
│  └─ report_generator/
├─ experiments/
├─ hidden_evaluators/
├─ fixtures/
└─ reports/
```

验证工程可以认识两个场景，并负责：

```text
A：强单 Agent
B：人工固定组织
C：AgentOS 自组织
```

Core 只看到普通 Goal、Actor、Artifact、Observation 和 Evaluation。

## 7. Core 的公共输入输出

### 用户 Goal API

```text
POST /goals
Content-Type: multipart/form-data | application/json

text: 自然语言目标
attachments: 可选附件
constraints: 可选自由结构
resources: 可选资源声明
```

字段保持可扩展，除 `text` 外不强制要求用户填写完整结构。

### Core 输出

```text
Goal Interpretation
Question / Human Capability Request
Plan and Organization Proposal
Work / Run Status
Artifact References
Decision / Evaluation
Resource Usage
```

### 外部结果输入

```text
Observation
├─ source
├─ metricOrFact
├─ value
├─ observedAt
├─ evidenceRef
├─ confidence
└─ scope
```

支付、DAU 等业务语义由应用或验证工程解释，Core 将其视为带证据的 Observation。

## 8. Core 最小领域对象

```text
Goal
GoalSpec
Hypothesis
CapabilityRequirement
CapabilityProvider
CapabilityCertification
Organization
Assignment
CoordinationContract
Work
Run
Artifact
Evidence
Observation
Evaluation
HumanTask
PolicyDecision
ExecutionPermit
ResourceUsage
AuditRecord
```

删除 Core 中的：

```text
Challenge
Scenario
App
News
Story
Subscription
DAU
```

`Challenge` 若需要存储，只存在于验证工程中。

## 9. 应用生成与运行边界

AgentOS 通过通用工具操作独立应用目录：

```text
WorkspaceService
VersionControlService
BuildService
TestService
ArtifactRegistry
DeploymentService
ObservationService
```

典型流程：

```text
用户 Goal
→ GoalSpec
→ 探索与能力分析
→ 生成产品候选方案
→ 在 scenario-apps/<generated-app-id> 创建独立工程
→ 构建和测试
→ 生成签名制品
→ 经审批发布
→ 独立应用持续产生业务 Observation
→ AgentOS 调整计划、能力和组织
```

应用源代码和运行数据不写入 Core 数据库，只以 Artifact、Evidence 和 Observation 引用关联。

## 10. 持久化与部署

Core P0：

```text
PostgreSQL
+ Transactional Outbox
+ Database-backed Queue
+ Durable Timer
+ Worker Lease/Heartbeat
+ S3-compatible Artifact Store
```

部署：

```text
agentos-core-api
agentos-core-worker
agentos-postgres
agentos-object-store

ai-professional-app-*        # 独立部署
adult-fairy-tales-*          # 独立部署

agentos-validation-runner    # 独立运行
```

每个应用拥有独立域名、数据库、密钥和资源配额。

## 11. 第一纵向切片

第一条编码链路完全不依赖场景：

```text
自然语言 Goal
→ 创建 Goal
→ Goal Intake 生成 GoalSpec
→ Goal Interpreter 生成假设与候选交付物
→ 创建 Work
→ General AIActor 执行
→ 产生 Artifact
→ Independent Evaluator 验收
→ 更新 Goal 阶段
→ 杀死 Worker并恢复
```

用一个纯虚拟 Goal 进行测试，不使用 AI App 或童话网站。

## 12. 第二纵向切片

```text
GoalSpec
→ Capability Requirement
→ Inventory Match
→ Candidate Gap
→ CandidateAgentSpec
→ Capability Trial
→ Organization
→ Assignment
→ Work
→ Organization Review
```

同样使用虚拟测试环境，验证能力和组织机制本身。

## 13. 第三纵向切片

Core 稳定后，分别把两个自然语言 Goal 提交给相同 API：

```text
Goal A → 生成/操作 ai-professional-app 独立工程
Goal B → 生成/操作 adult-fairy-tales 独立工程
```

如果接入任一应用必须修改 Core 领域模型或状态机，视为架构失败。

## 14. 编码顺序

### M0：拆分工程

- 创建 `agentos-core`；
- 创建 `scenario-apps`；
- 创建 `agentos-validation`；
- 设置独立依赖、配置和 CI。

### M1：Core 持久化内核

- Goal、GoalSpec、Work、Run；
- Outbox、Worker、Lease、Timer；
- Artifact、Audit、恢复测试。

### M2：Goal Intake 与 AI 闭环

- 自然语言 Goal API；
- ModelProvider、AIActor；
- Goal Interpreter；
- Evaluation；
- 第一纵向切片。

### M3：能力与组织

- Capability、AgentSpec、Organization、Assignment；
- 第二纵向切片。

### M4：应用操作能力

- Workspace、Git、Build、Test、Deploy、Observation 通用服务；
- 权限、ExecutionPermit、HumanTask。

### M5：双场景运行

- 将两条自然语言 Goal 分别提交给 Core；
- AgentOS 自行创建两个独立应用工程；
- Validation Harness 运行对照和归因。

## 15. 架构验收

必须同时满足：

1. `agentos-core` 可在没有 `scenario-apps` 时独立启动和通过测试；
2. 删除任一场景应用不影响 Core 编译和启动；
3. 两个应用不导入 Core 内部模块，只能使用公开 API/SDK；
4. Core 数据库不存在场景业务表；
5. 用户无需了解 ChallengeContract；
6. 两个 Goal 通过相同自然语言 Goal API 提交；
7. 场景应用由 AgentOS 创建和演进，而不是作为预定义插件加载；
8. 验证工程可以替换场景而不修改 Core；
9. 应用 KPI 通过 Observation/Evidence 进入 Core；
10. Challenge 仅作为验证实验配置存在。

