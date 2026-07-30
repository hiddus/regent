# Regent v3 技术架构（以 AgentOS 六要素映射）

> 状态：CURRENT（2026-07-25 冻结） / 配套 `Regent-Definition-v3.md`  
> 日期：2026-07-24（冻结：2026-07-25）  
> 性质：权威执行基线（Owner 批准升 CURRENT）  
> 将图片定义中的 G/C/V/R/S/O 公式映射为 Regent 可执行的技术模块、接口与数据模型

---

## 0. 架构总览

Regent 技术架构的核心使命是：**在目标 `G`、约束 `C`、治理 `V`、资源 `R_t`、状态 `S_t` 下，持续寻找并执行最优组织 `O_t^*`，使业务效用 `U` 最大化，并保证状态按 `S_{t+1} = Transition(S_t, O_t)` 演进。**

```text
                    用户 / 企业目标 (G)
                           │
                           │ 自然语言 + 可选约束/资源/偏好
                           ▼
              ┌────────────────────────────┐
              │  Constraint Engine  (C)     │  资源/预算/时效/安全/合规检查
              │  Governance Engine  (V)     │  身份、权限、审计、Permit、合规
              └────────────┬───────────────┘
                           │ 准入后进入候选集合 𝒪_Regent
                           ▼
              ┌────────────────────────────┐
              │  Goal Engine    (G)         │  目标解释、分解、GoalSpec 版本化
              │  Organization Engine (O)    │  组织寻优、动态重构、扩缩容
              │  Resource Engine  (R_t)     │  能力发现、资源预算、实时可用性
              │  Memory/State Engine (S_t)  │  状态持久化、记忆、Evidence、审计
              │  Event Engine               │  驱动持续迭代循环
              └────────────┬───────────────┘
                           │ 执行最优组织 O_t^*
                           ▼
              ┌────────────────────────────┐
              │  Agent Mesh / Runtime         │  单 Agent / 固定组织 / 动态组织
              │  - A2A (Agent ↔ Agent)      │  Agent 间协作与任务委托
              │  - MCP (Agent ↔ Tool)       │  工具/数据/外部服务调用
              └────────────┬───────────────┘
                           │ 输出 Evidence / Observation
                           ▼
              ┌────────────────────────────┐
              │  Infrastructure Layer       │  K8s / Ray / Dapr / PostgreSQL / Redis
              └────────────────────────────┘
```

**关键原则**：

- Kernel 不是 Agent；Kernel 负责管理 Agent 的生命周期、权限、通信、状态与资源。
- 组织 `O_t` 是决策变量，不是固定架构；单 Agent、固定模板、多 Agent 动态组织都是候选实现。
- 所有副作用必须满足 `C(O_t) ≤ 0`、`V(O_t) = True`、`R_t(O_t) ≥ R_min`。
- 状态转移 `S_{t+1} = Transition(S_t, O_t)` 必须可持久化、可恢复、可审计。

---

## 1. 核心引擎设计（六要素映射）

### 1.1 Goal Engine（目标引擎）

**职责**：把 `G` 解释为可执行、可验证、可版本化的 `GoalSpec`。

| 模块 | 职责 | 输出 |
|---|---|---|
| `GoalInterpreter` | 自然语言 → 显式目标、显式约束、推断项、未知项、非目标项 | `GoalSpec` DRAFT |
| `GoalDecomposer` | 目标分解为子目标/Work 单元 | 子目标图 + 依赖 |
| `KPIExtractor` | 提取可验证成功指标与验收标准 | `AcceptanceCriteria` |
| `GoalVersioning` | 冻结/修订/替代版本管理 | `GoalSpec` 版本链 |
| `GoalStateMachine` | 驱动 `DRAFT → FROZEN → ACTIVE → ACHIEVED/EXHAUSTED/FAILED/CANCELLED` | 状态事件 |

**约束**：

- `GoalSpec` 不是用户预先写的产品需求，而是系统解释产物。
- 用户唯一必填输入是自然语言 `G`；附件、期限、资源、约束和偏好均为可选。
- 任何版本修改必须通过 `SUPERSEDED` 旧版 + 新版 `DRAFT` 的链式记录。

### 1.2 Constraint Engine（约束引擎）

**职责**：保证任意候选组织 `O_t` 满足 `C(O_t) ≤ 0`。

| 模块 | 职责 | 示例 |
|---|---|---|
| `PolicyRegistry` | 注册业务规则、合规策略、安全策略 | 禁止联网、不得修改输入 |
| `BudgetMonitor` | 实时预算、Token、算力、时间跟踪 | 预算超限时 BLOCKED |
| `ResourceQuota` | 资源上限与配额 | 并发 Goal 数、Worker 数 |
| `ConstraintChecker` | 对候选 `O_t` 进行约束评估 | 运行前/运行中检查 |
| `ViolationHandler` | 违反约束时的失败关闭 | 记录 failure_code、审计、通知 |

**关键设计**：

- 约束在组织寻优前作为过滤条件，在组织执行中作为持续护栏。
- 预算 ledger 与模型/工具账单对齐 `price_book_version`。
- 预算超限不是异常，而是合法终态路径之一，必须形成可解释的 `BLOCKED` 或 `EXHAUSTED`。

### 1.3 Governance Engine（治理引擎）

**职责**：保证 `V(O_t) = True`，即任何组织执行都经过身份、权限、审计、Permit 与合规检查。

| 模块 | 职责 | 输出 |
|---|---|---|
| `Identity & Access (I&A)` | Agent/用户/工具/外部服务的身份与最小权限 | 权限矩阵 |
| `Permit Manager` | 一次性 `ExecutionPermit`：REQUESTED → GRANTED → CLAIMED → CONSUMED | Permit 链 |
| `HumanTask Manager` | 人工审批节点，等待期间不占 Worker | WAITING_HUMAN 事件 |
| `Audit Logger` | 不可变审计日志：谁、何时、为何、调用什么、产生什么 | 审计链 |
| `Compliance Checker` | 凭据扫描、PII 检查、安全策略 | 合规报告 |
| `Risk Engine` | 高风险行动识别与升级 | 风险标记 + 强制审批 |

**关键设计**：

- `ExecutionPermit` 是一次性行动级授权；`CLAIMED` 后必须最终进入 `CONSUMED` 或 `REVOKED`。
- `HumanTask` 是独立等待状态，不能阻塞 Worker 池。
- 所有外部副作用必须绑定 `operation_key` 以实现幂等与对账。
- 凭据泄露、安全违规、越权访问 > 0 立即触发系统停止投资评审。

### 1.4 Resource Engine（资源引擎）

**职责**：管理 `R_t`，保证 `R_t(O_t) ≥ R_min`，并在资源变化时驱动重规划。

| 模块 | 职责 | 内容 |
|---|---|---|
| `CapabilityRegistry` | 注册 Agent、技能、工具、Runtime Profile | 能力清单 + 版本 |
| `SkillComposer` | 复用、配置、组合、构建能力 | 组合能力包 |
| `CapabilityGapResolver` | 发现缺口并按优先级补齐 | 缺口 → 复用/配置/组合/构建/人类 |
| `ModelRouter` | 模型/算力/端点调度 | 模型版本、Token、缓存 |
| `ExternalServiceConnector` | 外部服务、渠道、API 接入 | 服务凭证、健康状态 |
| `BudgetLedger` | 实时成本账簿 | 分项成本与归属 |

**关键设计**：

- 资源是时间的函数 `R_t`；资源变化时触发 `BLOCKED` 或重新寻优。
- 能力补齐顺序：复用 → 配置 → 组合 → 构建 → 请求人类。
- Capability Provider 不得注入缺失的业务功能；业务功能必须由 Goal 驱动在 `apps/` 中实现。

### 1.5 Memory / State Engine（状态引擎）

**职责**：实现 `S_{t+1} = Transition(S_t, O_t)`，保证状态持久、可恢复、可审计。

| 模块 | 职责 | 存储 |
|---|---|---|
| `StateStore` | Goal/Work/Run/Permit/HumanTask 状态机 | PostgreSQL |
| `Outbox` | 状态变更事件可靠投递 | PostgreSQL + 事件总线 |
| `ArtifactStore` | 不可变 Artifact（输出、Evidence、报告、SBOM） | 对象存储 + 哈希 |
| `EvidenceStore` | 外部证据、Observation、签名、哈希 | 审计链 |
| `WorkingMemory` | 当前任务上下文 | Redis / 内存 |
| `EpisodicMemory` | 经历、历史 Run、失败/成功模式 | Vector DB |
| `SemanticMemory` | 行业知识、规则、经验、能力模板 | Knowledge Graph / 向量索引 |

**状态机核心**：

```text
Goal:  DRAFT → FROZEN → ACTIVE → ACHIEVED/EXHAUSTED/FAILED/CANCELLED
            ↓          ↑
         SUPERSEDED   PAUSED / WAITING_HUMAN / BLOCKED

Work:  DRAFT → ACCEPTED → (Run loop) → ACCEPTED / REJECTED

Run:   PENDING → DISPATCHED → EXECUTED → EVALUATED
              ↓            ↓
         BLOCKED/UNKNOWN → RECONCILING/MANUAL_REVIEW
```

**关键设计**：

- 状态转移必须幂等；Run 是不可变尝试，历史 Run 不覆盖。
- UNKNOWN 状态必须在 15 min 内进入 `RECONCILING` 或 `MANUAL_REVIEW`；否则触发安全停。
- 外部数据默认 `UNTRUSTED_DATA`，不得成为指令或授权来源。

### 1.6 Organization Engine（组织引擎）

**职责**：在 `𝒪_Regent` 中寻优，输出 `O_t^* = arg max U(...)`。

| 模块 | 职责 | 说明 |
|---|---|---|
| `OrganizationSpace` | 定义所有候选组织方案 | 单 Agent、固定模板、动态多 Agent、人类参与 |
| `UtilityFunction` | 评估 `U(O_t | G, C, V, R_t, S_t)` | 成功概率、成本、延迟、人工、风险 |
| `TopologyPlanner` | Agent 角色分工与拓扑设计 | 角色、依赖、通信路径 |
| `Orchestrator` | 协作流程编排与执行 | 工作流、任务委托、同步/异步 |
| `ReorganizationTrigger` | 触发重构的事件：证据变化、资源变化、失败、人工反馈 | 重规划信号 |
| `OrganizationEvaluator` | 运行后评估组织效用，反馈优化 | 闭环 |

**关键设计**：

- **单 Agent 是默认组织**；动态组织必须通过 P2-4 Eval Harness 证明正净收益才能晋级默认策略。
- 组织寻优是持续过程：`O_t` 可以在每个状态转移后被重新评估。
- 组织必须可解释：每次选择 `O_t^*` 必须能说明为什么优于其他候选。

---

## 2. Agent Mesh / Runtime

### 2.1 Agent 标准模型（Agent Manifest）

每个 Agent 必须具备类似 Android APK 的 Manifest：

```yaml
agent:
  id: <uuid>
  name: <string>
  capability:
    - <capability_id>
  skills:
    - <skill_id>
  resource:
    - <resource_id>
  permission:
    - <permission_id>
  memory:
    - <memory_scope>
  state:
    - <state_schema>
  communication:
    - a2a
    - mcp
  runtime_profile:
    - <profile_id>
```

### 2.2 Agent 通信体系

```text
Agent A  ──A2A──>  Agent B      (Agent 间协作与任务委托)
Agent    ──MCP──>  Tool/API/Data  (工具与外部服务调用)
```

- **A2A**：Agent 间发现、委托、结果交换。
- **MCP**：工具、数据、外部服务调用。
- 所有通信必须通过治理引擎授权；跨 Agent 调用需要 Permit 或预授权策略。

### 2.3 Agent 生命周期

```text
Create → Register → Discover → Deploy → Run → Communicate → Evaluate → Upgrade → Retire
```

### 2.4 运行时推荐（与 PRD v2 工程约束一致）

| 层 | 推荐技术 | 职责 |
|---|---|---|
| 编排/状态 | LangGraph | Agent 流程、状态保存、Human-in-loop、长流程执行 |
| 计算调度 | Ray | 大规模并发、GPU 任务、Actor 模型 |
| 事件/通信 | Dapr | Pub/Sub、服务发现、状态管理、事件驱动 |
| 基础设施 | Kubernetes | 容器编排、资源隔离、伸缩 |
| 数据库 | PostgreSQL | 主状态、Outbox、审计、关系模型 |
| 缓存/短时记忆 | Redis | Working Memory、会话、锁 |
| 向量/长时记忆 | Milvus / PGVector | Episodic Memory、Semantic Memory |
| 对象存储 | MinIO / S3 | Artifact、Evidence、报告 |

### 2.5 组织运行形态

| 形态 | 说明 | 阶段 |
|---|---|---|
| 单 Agent | 默认，强基线 | P0 / P1 |
| 固定模板 | 预定义角色与流程 | P1 候选 |
| 动态组织 | 根据目标/状态实时重构 | P2-5（需实验验证） |
| 人类参与 | HumanTask、Operator 接管 | 全阶段 |

---

## 3. 数据与持久化模型

### 3.1 核心实体关系

```text
Goal 1 ──* Work 1 ──* Run
Work 1 ──* Evidence
Run 1 ──* Permit
Run 1 ──* Artifact
Run 1 ──* Audit
Goal 1 ──* OrganizationDecision
OrganizationDecision ──* O_t
```

### 3.2 不可变 Artifact 类型

| Artifact | 内容 | 用途 |
|---|---|---|
| `GoalSpec` | 目标解释、约束、未知项 | 目标版本化 |
| `Evidence` | 外部证据、Observation、哈希 | 决策依据 |
| `HypothesisDecision` | 假设比较与选择 | 产品方向 |
| `RequirementRevision` | 需求与生成计划 | 生成约束 |
| `GenerationPlan` | 构建计划与文件变更集 | 可复现构建 |
| `BuildReport` | 构建结果、依赖哈希、SBOM | 可验证 |
| `Observation` | 签名、幂等、Bot 检测后的真实使用数据 | 迭代依据 |
| `GateEvaluation` | CONTINUE / REVISE / STOP / INSUFFICIENT | 决策 |
| `DecisionRecord` | 继续/转向/停止的正式记录 | 阶段门 |
| `Permit` | REQUESTED→GRANTED→CLAIMED→CONSUMED | 副作用授权 |
| `AuditLog` | 不可变审计链 | 合规 |

### 3.3 数据隔离

- 四级隔离：`tenant / org / project / goal`（首发可简化为 `org/project/goal`，tenant 单例）。
- 外部数据 `UNTRUSTED_DATA` 标记，不得进入指令或授权路径。
- 数据驻留：默认单区域；多区域为候选路线图。

---

## 4. 事件驱动与迭代循环

Event Engine 驱动整个 `arg max` 循环：

```text
1. 事件触发：Goal 创建、Evidence 到达、资源变化、Permit 完成、HumanTask 完成、Run 失败、超时
        │
        ▼
2. Constraint Engine 评估 C(O_t) ≤ 0
        │
        ▼
3. Resource Engine 评估 R_t(O_t) ≥ R_min
        │
        ▼
4. Governance Engine 评估 V(O_t) = True
        │
        ▼
5. Organization Engine 寻优 O_t^* = arg max U(...)
        │
        ▼
6. 执行 O_t^*，产生 S_{t+1} = Transition(S_t, O_t)
        │
        ▼
7. 输出 Evidence/Observation，触发下一轮事件
```

**关键事件**：

- `GOAL_DRAFTED` / `GOAL_FROZEN` / `GOAL_STARTED`
- `WORK_CREATED` / `WORK_ACCEPTED` / `WORK_REJECTED`
- `RUN_DISPATCHED` / `RUN_EXECUTED` / `RUN_EVALUATED`
- `PERMIT_REQUESTED` / `PERMIT_GRANTED` / `PERMIT_CLAIMED` / `PERMIT_CONSUMED`
- `HUMANTASK_CREATED` / `HUMANTASK_COMPLETED` / `HUMANTASK_ESCALATED`
- `EVIDENCE_ARRIVED` / `OBSERVATION_SIGNED` / `GATE_EVALUATED`
- `REORGANIZATION_TRIGGERED` / `ORGANIZATION_SELECTED`
- `BLOCKED` / `UNKNOWN` / `RECONCILING_REQUIRED`

---

## 5. 接口与 API 设计（概要）

### 5.1 面向用户/Operator

```text
POST /goals                         # 提交自然语言目标
GET  /goals/{id}                    # 查询目标状态与证据
POST /goals/{id}/freeze             # 冻结 GoalSpec
POST /goals/{id}/resume             # 从 PAUSED 恢复
POST /goals/{id}/cancel             # 取消目标
POST /humantasks/{id}/approve       # 批准人工任务
POST /humantasks/{id}/reject        # 拒绝人工任务
GET  /decisions/{id}                # 查询决策与证据链
GET  /audit/{goal_id}               # 导出审计链
```

### 5.2 面向内部引擎

```text
# Goal Engine
GoalSpec interpret_goal(G, C_opt, R_opt)

# Constraint Engine
ConstraintReport check_constraints(O_t, C, R_t)

# Governance Engine
Permit request_permit(action, risk_level, actor)
bool check_governance(O_t, V)

# Resource Engine
ResourceReport assess_resources(O_t)
Capability resolve_capability_gap(capability_demand)

# Organization Engine
Organization select_organization(G, C, V, R_t, S_t)
Utility evaluate_utility(O_t)

# State Engine
State transition(State S_t, Organization O_t)
void persist_event(Event e)
```

---

## 6. 安全与可靠性设计

### 6.1 故障注入与对账（G8 / Durable External Effects）

| 故障 | 要求 |
|---|---|
| Worker 杀进程 | 状态恢复后无重复副作用 |
| 重复投递 | 同 `operation_key` 只产生一次有意义外部效果 |
| 响应丢失 | UNKNOWN 在 15 min 内进入 RECONCILING 或 MANUAL_REVIEW |
| 外部服务失败 | 幂等重试，超次 BLOCKED |

### 6.2 凭据与密钥

- 仓库+镜像扫描：0 明文密钥。
- 密钥轮换回执入审计。
- 运行时密钥通过安全注入，不进入 Artifact 或日志。

### 6.3 可观测性

- 状态机全链路追踪（Goal → Work → Run → Permit → Evidence）。
- 效用函数 `U` 的每次评估必须可记录、可解释。
- 所有组织方案候选及其评分必须入审计。

---

## 7. 阶段落地方案

### 7.1 P0 MVP（3 个月）

基础设施：

- Kubernetes + Docker + PostgreSQL + Redis + 对象存储
- 可选：Ray / Dapr（如 P0 需要并发/事件能力）

Core 模块：

1. `Goal Engine`：自然语言 → GoalSpec（显式/推断/未知）。
2. `Constraint Engine`：预算、资源、简单业务规则。
3. `Governance Engine`：Permit 基础流程 + Audit Log。
4. `Resource Engine`：Capability Registry + 本地工具 + 简单能力补齐。
5. `State Engine`：Goal/Work/Run 状态机 + PostgreSQL + Outbox。
6. `Organization Engine`：单 Agent 默认；固定模板为候选。
7. `Runtime`：LangGraph 执行循环。

验收：

- `CSV_SUMMARY_BASELINE` 通过。
- `EVT_PARSER_GAP` 通过。
- 独立 `apps/` 创建可运行 App。
- A/B/C 对照实验完成并形成 DecisionRecord。

### 7.2 P1 产品创建与运营

扩展：

- Evidence 发现、Hypothesis 比较、Requirement Revision。
- 隔离 Build、Preview、Observation、Gate、Decision。
- HumanTask 产品化、Durable External Effects、G8 故障注入。
- Graduation（SYSTEM + PRODUCT_EVIDENCE）。

### 7.3 P2 规模化与自适应组织

扩展：

- 多 Goal 调度与资源治理（P2-1）。
- 多 Runtime Profile（P2-2）。
- Eval Harness（P2-4，先于自适应组织）。
- 条件承诺：长期记忆（P2-3）、自适应组织（P2-5）、Champion/Challenger（P2-6）。
- 候选：受控生产发布（P2-7）、受监管自我改进（P2-8）、能力生态（P2-9）。

---

## 8. 与 AgentOS 白皮书的差异说明

| AgentOS 白皮书 | Regent v3 适配 | 原因 |
|---|---|---|
| 面向文旅/行业的设备 Agent 网络 | 首发聚焦低风险内部工具 / Web MVP | 产品验证路径更可控 |
| Agent Marketplace 商业化 | 候选路线图（P2-9），非 P0/P1 核心 | 先证明目标执行内核与产品创建闭环 |
| 万物 Agent 网络 | 候选远期 | 先做软件 Agent 组织，再扩展到设备 Agent |
| Kernel 管理 Agent | 一致 | Kernel 不是 Agent |
| 组织寻优公式 | 完全一致 | 作为架构锚点 |

---

## 9. 待冻结事项

1. 本架构需与 `Regent-Definition-v3.md` 同步冻结。
2. 明确 `U` 的默认函数形式与可替换接口。
3. 明确 `𝒪_Regent` 候选集合的编码方式（单 Agent / 固定模板 / 动态组织）。
4. 确定 P0 是否引入 Ray / Dapr，或先以 PostgreSQL + Redis + LangGraph 为主。
5. 定义 Capability Registry 与 MCP/A2A 的接口规范。
6. 更新 CI 检查：架构文档与定义文档的哈希绑定。
