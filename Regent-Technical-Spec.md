# Regent 技术架构与实施规范

> 状态：CURRENT  
> 日期：2026-07-30（合并自 Technical-Spec-v2 + Architecture-v3）  
> 性质：权威执行基线（Owner 批准）  
> 配套需求：[`Regent-PRD.md`](./Regent-PRD.md)  
> 附录：  
> - [`docs/appendices/State-Machines-and-Invariants.md`](docs/appendices/State-Machines-and-Invariants.md)  
> - [`docs/appendices/Durable-Execution-and-External-Effects.md`](docs/appendices/Durable-Execution-and-External-Effects.md)  
> - [`docs/appendices/Security-Tenancy-and-Recovery.md`](docs/appendices/Security-Tenancy-and-Recovery.md)

---

## 1. 架构总览

Regent 技术架构的核心使命是：**在目标 `G`、约束 `C`、治理 `V`、资源 `R_t`、状态 `S_t` 下，持续寻找并执行最优组织 `O_t^*`，使业务效用 `U` 最大化，并保证状态按 `S_{t+1} = Transition(S_t, O_t)` 演进。**

```text
                    用户 / 企业目标 (G)
                           │
                           │ 自然语言 + 可选约束/资源/偏好
                           ▼
              ┌────────────────────────────┐
              │  Constraint Engine  (C)     │  资源/预算/时效/安全/合规
              │  Governance Engine  (V)     │  身份、权限、审计、Permit
              └────────────┬───────────────┘
                           │ 准入后进入候选集合 𝒪_Regent
                           ▼
              ┌────────────────────────────┐
              │  Goal Engine    (G)         │  目标解释、分解、GoalSpec
              │  Organization Engine (O)    │  组织寻优、动态重构
              │  Resource Engine  (R_t)     │  能力发现、资源预算
              │  Memory/State Engine (S_t)  │  状态持久化、Evidence
              │  Event Engine               │  驱动持续迭代循环
              └────────────┬───────────────┘
                           │ 执行最优组织 O_t^*
                           ▼
              ┌────────────────────────────┐
              │  Agent Mesh / Runtime         │  单 Agent / 固定 / 动态
              │  - A2A (Agent ↔ Agent)      │  Agent 间协作
              │  - MCP (Agent ↔ Tool)       │  工具/外部服务调用
              └────────────┬───────────────┘
                           │ 输出 Evidence / Observation
                           ▼
              ┌────────────────────────────┐
              │  Infrastructure Layer       │  PostgreSQL / Redis / Docker
              └────────────────────────────┘
```

**关键原则**：
- Kernel 不是 Agent；Kernel 管理 Agent 的生命周期、权限、通信、状态与资源。
- 组织 `O_t` 是决策变量，不是固定架构。
- 所有副作用必须满足 `C(O_t) ≤ 0`、`V(O_t) = True`、`R_t(O_t) ≥ R_min`。
- 状态转移必须可持久化、可恢复、可审计。

**优先级**：
```text
事实完整性 → 安全与治理 → 可恢复性 → 产品证据可信度
→ 结果质量 → 延迟与成本 → 扩展便利性
```

---

## 2. 系统边界

```text
┌──────────────── Regent Core ────────────────┐
│ API / Conversation / Projection             │
│ Goal & Product Control Plane                  │
│ Scheduler & Execution Orchestrator           │
│ Governance / Permit / HumanTask / Secret     │
│ Evidence / Artifact / Observation / Audit     │
│ Capability / Runtime / Organization Registry  │
│ ExternalOperation / Eval Harness             │
└────────────── ports + signed contracts ─────┘
        │          │          │          │
 Evidence      Build      Deployment   Model
 Provider      Provider   Provider     Provider
```

禁止：Generated App 与 Core 相互 import；Agent 直接写状态；Provider 绕过 Permit；Conversation metadata 成为事实源；内部 smoke 满足产品 Gate；生成或部署阶段补造缺失业务功能。

---

## 3. 技术基线

- Python 3.12+、FastAPI、SQLAlchemy async；
- PostgreSQL 唯一事实源，Alembic 单一迁移链；
- Outbox、Worker Lease、Durable Timer；
- Pydantic 结构化协议；
- 不可变 Artifact 与内容哈希；
- Docker 或独立 Build Provider 隔离构建；
- Ruff、mypy strict、Pytest 和迁移检查作为合并门禁。

P2 前期继续采用模块化单体。除非测量证明 Outbox 已成为瓶颈，否则不引入外部事件总线或微服务拆分。

---

## 4. 六要素引擎设计

### 4.1 Goal Engine（目标引擎）

| 模块 | 职责 | 输出 |
|---|---|---|
| `GoalInterpreter` | 自然语言 → 显式目标、约束、推断、未知项 | GoalSpec DRAFT |
| `GoalDecomposer` | 目标分解为子目标/Work | 子目标图 + 依赖 |
| `GoalVersioning` | 冻结/修订/替代版本管理 | GoalSpec 版本链 |
| `GoalStateMachine` | DRAFT → FROZEN → ACTIVE → 终态 | 状态事件 |

### 4.2 Constraint Engine（约束引擎）

| 模块 | 职责 |
|---|---|
| `PolicyRegistry` | 业务规则、合规策略 |
| `BudgetMonitor` | 预算、Token、算力、时间跟踪 |
| `ConstraintChecker` | 组织方案约束评估 |
| `ViolationHandler` | 违反约束时的失败关闭 |

预算超限不是异常，而是合法终态路径之一（`BLOCKED` 或 `EXHAUSTED`）。

### 4.3 Governance Engine（治理引擎）

| 模块 | 职责 |
|---|---|
| Identity & Access | Agent/用户/工具身份与最小权限 |
| Permit Manager | 一次性 ExecutionPermit：REQUESTED→GRANTED→CLAIMED→CONSUMED |
| HumanTask Manager | 人工审批节点，等待期间不占 Worker |
| Audit Logger | 不可变审计日志 |
| Compliance Checker | 凭据扫描、PII 检查 |
| Risk Engine | 高风险行动识别与升级 |

### 4.4 Resource Engine（资源引擎）

| 模块 | 职责 |
|---|---|
| `CapabilityRegistry` | Agent、技能、工具、Runtime Profile 注册 |
| `CapabilityGapResolver` | 缺口发现与补齐（复用→配置→组合→构建→人类） |
| `ModelRouter` | 模型/算力/端点调度 |
| `BudgetLedger` | 实时成本账簿 |

### 4.5 Memory / State Engine（状态引擎）

| 模块 | 存储 |
|---|---|
| `StateStore` | PostgreSQL（Goal/Work/Run/Permit 状态机） |
| `Outbox` | PostgreSQL（状态变更事件投递） |
| `ArtifactStore` | 对象存储 + 哈希（不可变 Artifact） |
| `EvidenceStore` | 审计链（外部证据、Observation） |
| `WorkingMemory` | Redis / 内存（当前任务上下文） |

### 4.6 Organization Engine（组织引擎）

| 模块 | 职责 |
|---|---|
| `OrganizationSpace` | 所有候选组织方案 |
| `UtilityFunction` | 评估 U(O_t) |
| `TopologyPlanner` | Agent 角色分工与拓扑 |
| `ReorganizationTrigger` | 触发重构的事件 |

**单 Agent 是默认组织**；动态组织必须通过 P2-4 Eval Harness 证明正净收益才能晋级默认策略。

---

## 5. Agent Mesh / Runtime

### 5.1 Agent 通信体系

```text
Agent A  ──A2A──>  Agent B      (Agent 间协作与任务委托)
Agent    ──MCP──>  Tool/API/Data  (工具与外部服务调用)
```

所有通信必须通过治理引擎授权；跨 Agent 调用需要 Permit 或预授权策略。

### 5.2 Agent 生命周期

```text
Create → Register → Discover → Deploy → Run → Communicate → Evaluate → Upgrade → Retire
```

### 5.3 组织运行形态

| 形态 | 阶段 |
|---|---|
| 单 Agent | P0 / P1（默认） |
| 固定模板 | P1 候选 |
| 动态组织 | P2-5（需实验验证） |
| 人类参与 | 全阶段 |

---

## 6. 事实源与 Artifact 边界

| 层 | 权威性 | 示例 |
|---|---|---|
| 事实源 | 唯一可写权威 | PostgreSQL 中 Goal、Work、Run、Permit、ExternalOperation、Outbox |
| Artifact | 不可变内容寻址 | Evidence Snapshot、FileChangeSet、SBOM、VerificationReport |
| 投影 | 可重建 | Conversation Timeline、状态页 |
| 模型输出 | 候选，须校验 | Hypothesis、FileChangeSet 草案 |

删除投影后必须能从事实源 + Artifact 重建。Artifact 不能单独授权副作用。

**权威对象树**：

```text
AppProject
└─ Goal
   ├─ GoalSpec[]
   ├─ DiscoveryRound[] ─ EvidenceRef[] / ProductHypothesis[] / HypothesisDecision
   ├─ RequirementRevision[] ─ CapabilityResolutionPlan
   ├─ GenerationPlan ─ GenerationRun ─ FileChangeSet / WorkspaceSnapshot
   ├─ DependencyResolution / AppBuild / ReleaseCandidate / Deployment[]
   ├─ MetricBinding[] / Observation[] / GateEvaluation[] / IterationDecision[]
   ├─ ExternalOperation[] / MemoryRecord[]
   └─ EvalRun[] / ExperimentAssignment[]（P2-4+）
```

---

## 7. 完整状态机索引

每个控制对象必须在附录 1 冻结：`state → command → guard → resulting state → emitted event → terminal? / retry rule / timeout / cancel / unknown rule`。

数据库要求：`version` 列、Check Constraint、条件更新（`WHERE version = expected`）。

### 核心状态机

```text
Goal:  DRAFT → FROZEN → ACTIVE → ACHIEVED/EXHAUSTED/FAILED/CANCELLED
            ↓          ↑
         SUPERSEDED   PAUSED / WAITING_HUMAN / BLOCKED

Work:  PLANNED → READY → RUNNING → EVALUATING → ACCEPTED
             ↘ WAITING_HUMAN          ↘ REJECTED → READY
             ↘ BLOCKED
RUNNING → UNKNOWN
任意非终态 → CANCELLED

Run:   CREATED → PERMIT_PENDING → QUEUED → RUNNING
RUNNING → EXECUTED | FAILED | UNKNOWN | CANCELLED
```

对象清单（细节见附录）：Goal、Work、Run、DiscoveryRound、AppBuild、Deployment、ResourceReservation、GateEvaluation、Permit、ExternalOperation、MemoryRecord、EvalRun。

---

## 8. Outbox / Inbox 与事件兼容

主链事件：

```text
GoalExecutionRequested
→ DiscoveryRoundRequested → DiscoveryCompleted
→ RequirementRequested → RequirementValidated
→ CapabilityResolutionRequested → CapabilityResolutionSatisfied
→ GenerationRunRequested → WorkspaceSnapshotReady
→ DependencyResolutionRequested
→ AppBuildRequested → AppBuildPassed
→ PreviewDeploymentRequested → PreviewDeploymentSucceeded
→ ExternalObservationReceived
→ GateEvaluationRequested → IterationDecisionRecorded
```

事件必须包含 `event_id`、`event_type`、`schema_version`、聚合 ID/版本、`idempotency_key`、`correlation_id`、`causation_id`、时间与 payload。

Handler 必须：
1. 校验 Schema 与聚合状态；
2. 用控制对象唯一键实现幂等；
3. 同事务提交状态、Audit 和下一 Outbox；
4. 外部调用不持有数据库事务；
5. 可重试失败抛出并由 Dispatcher 退避；
6. 达到上限进入 Dead Letter；
7. UNKNOWN 不重复副作用，只查询和对账；
8. 终态失败写稳定 `failure_code`。

---

## 9. ExternalOperation（G0 实现，先于 Scheduler）

统一外部副作用控制对象。**最小闭环是 P1 Graduation G8 的前置依赖，禁止推迟到 P2-1。**

```text
PREPARED → DISPATCHING → SUCCEEDED | FAILED_TERMINAL | UNKNOWN
→ RECONCILING → SUCCEEDED | FAILED_TERMINAL | MANUAL_REVIEW
```

原子派发权（同 DB 事务，禁止网络 I/O）：
1. `PREPARED → DISPATCHING`
2. `Permit CLAIMED → CONSUMED`
3. 固化 `dispatch_generation` 与 `operation_key`

`CONSUMED` = 唯一派发权已持久化，**不是**供应商已收到请求。提交事务后才允许外部 I/O；重试复用同一 `operation_key`。

必填：operation_key、request_digest、permit_id、local_fencing_token、dispatch_generation、external_id（可空）、对账字段。

---

## 10. Permit 与 Fencing

```text
REQUESTED → GRANTED → CLAIMED → CONSUMED
REQUESTED → DENIED
GRANTED → EXPIRED | REVOKED
CLAIMED → CONSUMED | REVOKED
```

不变量：
1. Permit 1:1 ExternalOperation；claim 产生 local_fencing_token。
2. CONSUMED 与 DISPATCHING 同事务固化。
3. 本地 fencing 防止旧 Worker 继续控制；不假设第三方 API 识别 token。
4. 重复外部效果靠 Provider idempotency / query。
5. Lease 过期旧 Worker 不得提交；新 Worker 对账或新授权。

Permit 绑定 `goalId/workId/runId/actorId/action/target/parameterHash/dataScope/networkScope/resourceLimit/validUntil/nonce/idempotencyKey`。不保存明文凭证。

---

## 11. 数据与持久化模型

### 核心实体关系

```text
Goal 1 ──* Work 1 ──* Run
Work 1 ──* Evidence
Run 1 ──* Permit
Run 1 ──* Artifact
Run 1 ──* Audit
Goal 1 ──* OrganizationDecision
```

### 不可变 Artifact 类型

| Artifact | 用途 |
|---|---|
| `GoalSpec` | 目标版本化 |
| `Evidence` | 决策依据 |
| `HypothesisDecision` | 产品方向 |
| `RequirementRevision` | 生成约束 |
| `GenerationPlan` | 可复现构建 |
| `BuildReport` | 可验证 |
| `Observation` | 迭代依据 |
| `GateEvaluation` | 决策 |
| `DecisionRecord` | 阶段门 |
| `Permit` | 副作用授权 |
| `AuditLog` | 合规 |

### 数据隔离

四级隔离：`tenant / org / project / goal`（首发可简化为 `org/project/goal`，tenant 单例）。

---

## 12. Evidence、Observation 与归因

### Evidence 分类

- `declared-intent`：用户声明；
- `sourced-observation`：受控外部来源快照；
- `build-verification`：构建与测试；
- `product-observation`：真实用户行为或反馈；
- `operational-observation`：内部 smoke 与监控。

规则：declared-intent 不能单独证明市场事实；operational-observation 不能满足产品价值 Gate；OBSERVED claim 必须引用 Evidence。

**UNTRUSTED_DATA**：必须携带 `trust_label`、`source_*`、`content_hash`、`parser_version`、`injection_site`、`retrieved_at`。只能作为数据，不得成为指令、授权或策略来源。

### Prompt Injection 威胁模型

按阶段强制执行（附录 3）：G0 含间接/工具输出/外泄/篡改评价器；P2-3 强制 Memory-delayed；P2-5 强制 Agent-to-Agent。

读取优先级：硬约束 > 当前冻结事实 > 当前外部证据 > VERIFIED 记忆 > CANDIDATE 记忆。

---

## 13. 生成、构建与浏览器验证

### Generation

GenerationPlan 冻结 GoalSpec、Decision、Requirement、Resolution、Runtime Profile 与 Evidence Bundle 摘要；模型只返回 FileChangeSet；WorkspaceWriter 拒绝路径逃逸；同一 GenerationRun 一个 WorkspaceSnapshot。

### Dependency 与 Build

受控 Egress + Permit；锁文件与 SBOM；断网非 root 构建；PASSED 须完整 VerificationReport。

### Journey

RequirementRevision 提供机器可执行 Journey；存在按钮或事件属性不构成成功。

---

## 14. Runtime Profile 认证（P2-2）

```text
RuntimeProfile
- name / version / manifest_hash
- resolver_image_digest / sandbox_image_digest
- supported_artifact_types / dependency_policy
- build_commands / verification_contract
- resource_defaults / lifecycle_status
```

状态：DRAFT、CERTIFIED、DEPRECATED、REVOKED。只有 CERTIFIED 可用于新计划。

---

## 15. Scheduler Reservation / Ledger（P2-1）

ExecutionQueue、多资源原子预留、BudgetLedger+price_book_version、Aging/公平性、checkpoint/resume、可重放 SchedulingDecision。  
前置：G0 ExternalOperation 与 G8 已签署。

---

## 16. Memory Admission / Revocation（P2-3）

```text
Admission → Retrieval → Usage Trace → Impact Graph → Revocation → Revalidation
```

强制：准入 Guard、冲突处理、衰减、隔离、批量撤销、循环证据检测；重验证期间下游标 `REVALIDATION_REQUIRED`，不得支撑新的 PASSED Gate。

---

## 17. AgentEnvelope 与权限传播（P2-5）

Agent 间消息必须封装为 AgentEnvelope：
- `source_agent_id`、`dest_agent_id`、`capability_scope`；
- `permit_refs` / fencing 引用（不得隐式扩大）；
- `content_trust = UNTRUSTED_DATA`；
- `correlation_id` / 签名或 HMAC。

权限只减不增：子 Agent 权限 ⊆ 父授权 ∩ GoalSpec 硬约束。

---

## 18. 独立评价器与 Eval Harness（P2-4）

- 冻结版本与不可变 Rubric；
- 默认看不到 Agent 身份与组织形式（盲评）；
- 不能修改测试、指标或排除规则；
- 机器验证优先于 LLM-as-a-Judge；
- 保存 evaluator model、prompt hash、工具版本、校准版本；
- 低置信度或冲突升级人工。

最小 Eval Harness 必须记录：任务集哈希、基线配置、预算账本、种子、重复次数、墙钟与计算预算、pass@k、安全违规、DecisionRecord。  
**自适应组织（P2-5）不得在 Harness 统计 Gate 之前默认启用。**

---

## 19. 安全与可靠性

### 凭据与密钥

- 凭据只来自环境 Secret、Secret Manager 或 Secret Broker；
- 禁止源码/脚本/fixture/日志/Artifact 明文；
- 镜像 digest；依赖 hash；网络默认拒绝；
- Provider 调用绑定 Permit 与 ExternalOperation。

### 故障注入（G8）

| 故障 | 要求 |
|---|---|
| Worker 杀进程 | 状态恢复后无重复副作用 |
| 重复投递 | 同 operation_key 只产生一次有意义外部效果 |
| 响应丢失 | UNKNOWN 在 15 min 内进入 RECONCILING 或 MANUAL_REVIEW |
| 外部服务失败 | 幂等重试，超次 BLOCKED |

### 灾备

PostgreSQL **RPO ≤ 15 分钟**；控制面 API **RTO ≤ 2 小时**；Artifact 多副本，丢失恢复 ≤ 24 小时。

---

## 20. 发布、迁移、回滚

```text
ReleaseCandidate → Preview → Staging → Production
```

Production：独立批准、Secret Broker、迁移与回滚计划、Canary/蓝绿、SLO、错误预算、观测窗口、事故接管。  
生成 Agent 不得拥有生产批准权或长期凭据。

---

## 21. API 原则

- `/v1` 保持兼容，破坏性变化进入 `/v2`；
- 202 返回可查询控制对象 ID；
- 写 API 接受幂等键；
- 状态 API 返回事实投影与对象引用；
- 稳定游标分页；错误含 `error_code`、`message`、`correlation_id`。

用户入口：

```text
POST /goals                     # 提交自然语言目标
GET  /goals/{id}                # 查询目标状态与证据
POST /goals/{id}/freeze         # 冻结 GoalSpec
POST /goals/{id}/resume         # 从 PAUSED 恢复
POST /goals/{id}/cancel         # 取消目标
POST /humantasks/{id}/approve   # 批准人工任务
POST /humantasks/{id}/reject    # 拒绝人工任务
GET  /decisions/{id}            # 查询决策与证据链
GET  /audit/{goal_id}           # 导出审计链
```

---

## 22. 可观测性

统一记录 correlation、causation、Goal、Work、Run、Event、ExternalOperation、Actor。  
必须观测：Outbox/Dead Letter、Lease、阶段耗时、UNKNOWN、对账、Permit/fencing、预算、Agent 协调 Token、Observation 排除原因、Decision 证据链。

Dead Letter 重放需授权、操作者与原因，并继续使用原业务幂等键。

---

## 23. 故障注入、并发、性能与恢复门禁

合并与阶段门禁必须包含：

1. Ruff、mypy strict、Pytest；Alembic upgrade + check；
2. 状态转换及非法转换；
3. 幂等重复投递与 Worker 中断恢复；
4. Provider UNKNOWN 对账与「响应丢失」剧本；
5. Permit 拒绝、过期、撤销、重复领取、fencing 拒绝；
6. 路径逃逸、symlink、压缩炸弹、供应链拒绝；
7. 浏览器核心任务；
8. internal/bot/test Observation 排除；
9. Prompt Injection 套件抽样；
10. 凭据扫描；
11. 端到端证据链查询；
12. 并发抢占与预算耗尽（P2-1+）。

禁止用源码字符串检查、类名存在或伪 Observation 代替行为验证。

---

## 24. 实施阶段

### G0：P1 Graduation（含 Durable External Effects）

按 PRD §6：`SYSTEM_GRADUATED` + `PRODUCT_EVIDENCE_GRADUATED`；先实现最小 ExternalOperation + Permit + operation_key + 故障注入（G8）。

| 阶段 | 批次依赖 | 核心交付 |
|---|---|---|
| G0 ExternalOperation | 依赖当前 Alembic head 之上的下一 revision | EO 表、operation_key、dispatch_generation、原子 CONSUMED、对账 |
| P2-1 Scheduler | 依赖 G0 revision 已应用 + Graduation/P2Start | Queue、Reservation、BudgetLedger、抢占 |
| P2-2 Runtime Registry | 依赖 P2-1 DecisionRecord | Profile、Certification、构建矩阵 |
| P2-3 Memory | **条件承诺** | MemoryRecord、Impact Graph、Revocation |
| P2-4 Minimal Eval Harness | **承诺**；先于自适应组织 | 任务集、基线、预算账本、盲评、统计 Gate |
| P2-5 Adaptive Organization | **条件**：P2-4 正净收益 | 组织提案、路由 |
| P2-6 Experiment Platform | **条件** | 完整 Champion/Challenger |
| P2-7…P2-9 | **候选** | 生产发布 / 自我改进 / 能力生态 |

迁移文件命名使用日期+描述；revision id 在实现时由 Alembic 生成。

---

## 25. 当前实现状态

> 截至 2026-07-30，代码统计：core 166 个 Python 模块，81 个测试文件，35 个迁移版本。

### 已完成

> 下列「已完成」指**结构/链路已落地**；可验证交付以治理管道行为 pytest + 非桩 Eval 为准，不以模块存在或结构级断言为准（见 DecisionNote `docs/decision-note-verifiable-delivery-2026-07-30.md`）。

- **P0 全链路**：Goal/Work/Run 三套状态机、Outbox、Lease、Timer、Artifact、Evidence、Audit，通过 CSV_SUMMARY_BASELINE。
- **P1 R1–R6**：Goal 解释与分解、Discovery 编排、证据连接器、假设决策、需求修订、能力解析、WorkspaceWriter、Generation Service、Docker 沙箱构建、Preview 发布、观测回流、Gate 评估与 Iteration Decision。
- **P1 R7**：DeploymentSmokeTestService 实现简化可体验性 Gate。
- **P1 R8**：IterationLoopService 实现 Observation→Decision→REVISE 端到端闭环。
- **对话与 App 身份**：Conversation 持久化、AppProject、GoalSpec DRAFT/FROZEN/SUPERSEDED、确认闸门、对话驱动修订（0019）。
- **真实 App 预览**：StaticAppPublisher、CSP 预览、观测钩子（0020）。
- **受监管自我改进（候选，未产品门禁）**：SelfImprovementRun 隔离副本、AST 验证、独立审查（0021）已落地代码；按 PRD §9.3 属 P2-8 候选，需单独产品 DecisionRecord 后方可宣称验收完成。
- **确认后自主执行闭环**：Confirm/Start 分离、Outbox 指数退避与死信（0022）。
- **Durable Hive（opt-in 固定模板）**：认证模板 `pm-dev-independent-qa-v1` 可经 `REGENT_AAR1_CERTIFIED_HIVE` 启用；**默认仍为强单 Agent**；自适应自由拓扑 `ROLLOUT_NOT_ALLOWED`，不得表述为已验证的默认并行执行能力。
- **控制台前端**：React 19 + Vite + TS，SSE 实时推送，三栏布局；右侧以 `status.agents` + SSE/`live_action` 驱动参与 Agent 名册与对话进度卡详略，产物与预览为可折叠次要区（见 `apps/regent-console/README.md`）。
- **桌面端（探索性）**：Tauri 桌面应用骨架存在于仓库；PRD 主交付范围为 Core + Web Console，桌面端未纳入 P0/P1 验收。

### 已知非阻塞限制

1. ReleaseCandidate 在 P1 执行链上自动批准（跳过人工任务）；人工批准 API 可用，默认不强制（见 `docs/registered-unimplemented-2026-07-30.md`）。
2. 完整浏览器级 R7 gate：无 Playwright 时 `browser_journey` 为 dry-run（步骤标记 passed，非真浏览器验收）；有 Playwright 时执行真实旅程。
3. ExternalOperation 完整闭环需在 G0 合入；调度路径 `dispatch_with_eo` 已创建真实 EO 行（`scheduler-dispatch-v1`），但不替代完整 provider 对账闭环。
4. Eval Harness 已改为交付信号/Goal 证据评分（无 `hash%2` 桩），`decide` 写入签名 `product_decision_record`；北极星/护栏提供只读报告 API（`/v1/governance/north-star`）。P0 完成定义第 5 条仍要求冻结任务集上的真实 A/B/C 对照与唯一产品 DecisionRecord，不得仅凭模块存在或夹具信号宣称已满足。

> 更正（2026-07-30）：此前误报「Evidence Connector 仍为空实现」「Deployment Provider 为内存实现」。现状为 `AllowlistedHttpEvidenceConnector` / `GoalIntentEvidenceConnector` 已接线；生产预览为 `StaticPreviewDeploymentProvider`（`InMemoryDeploymentProvider` 仅测试用）。

---

## 26. 技术完成定义

阶段完成要求：对象与状态有数据库约束；API、Worker、Projection 和运维入口完整；正常、失败、UNKNOWN、重试、Dead Letter 与恢复有测试；副作用受 Permit、fencing、幂等和审计约束；真实结果满足产品合同；不把 mock、内部流量或结构检查当作成功；文档、代码、迁移和部署一致；形成唯一 DecisionRecord；且所依据的 PRD 状态为 `CURRENT`。
