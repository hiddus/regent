# Regent 技术架构与实施规范

> 状态：CURRENT  
> 日期：2026-07-31（吸收对话式交付架构评审 + 交付可靠性差距审查 + 文档—实现对齐审计修复）
> 性质：权威执行基线（Owner 批准）  
> 配套需求：[`Regent-PRD.md`](./Regent-PRD.md)  
> 永久定义（唯一规范源，仅引用不复述正文）：[`docs/definitions/REGENT-DEFINITION-1.0.txt`](docs/definitions/REGENT-DEFINITION-1.0.txt)（`REGENT-DEFINITION-1.0`）  
> 编码执行清单：[`docs/conversational-delivery-plan-2026-07-31.md`](docs/conversational-delivery-plan-2026-07-31.md)（与 `Regent-Plan.md` §14 互指）  
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

### 13.4 生成策略选择与一致性

`generation_strategy`（`artifact-backed` / `agentic`）是运行时契约，不是装饰字段。Worker 构造生成器时必须按该值分派：`artifact-backed` → `ArtifactBackedCodeGenerator`，`agentic` → `AgenticCodeGenerator`（含多轮工具循环与真实验证）。`worker/main.py` 不得无条件固定某一生成器。

GQ-0 必须先扩展 `FileChangeSetGenerator` 协议，冻结只读 `generator_type`、`generator_ref` 与 `prompt_version` 元数据；协议与实现均已具备上述只读字段（见 `p1_ports.FileChangeSetGenerator` 与两类生成器）。编排器写入运行元数据前，须校验标签、实际对象类型与 `generation_strategy` 三者一致。不一致时唯一语义为拒绝启动该 Run，并写入包含期望值、实际值、策略、Run/Plan 标识和时间戳的 Evidence；不得静默回退或换用另一生成器。

### 13.5 会话内验证反馈闭环

artifact-backed 路径虽保留下游依赖构建（`execution_orchestrator` 依赖解析/构建）与部署后 smoke test，但反馈发生在较晚阶段、纠错成本高。为缩短反馈回路：

- 生成循环须能消费真实的构建失败、测试失败与端点 smoke 失败，作为下一轮（或同一会话的修正轮）的结构化输入，而非仅依赖文字化问题摘要或纯 LLM 裁判；
- `ArtifactBackedCodeGenerator` 的再生成应优先携带真实报错（traceback / 非 2xx 响应 / pytest 失败摘要），而非仅 gap reasons；
- agentic 路径的 AgentRunner 具备执行工具，但当前最终 VerificationAgent 失败不会自动返回工具循环，不得宣称强制闭环已经完成。GQ-2 必须至少触发一次受控修正。

跨阶段回灌统一使用持久化 FailureEnvelope 与 RepairAttempt：前者至少关联 goal/run/plan/workspace snapshot、失败阶段、裁剪后的错误摘要与证据 Artifact；后者记录幂等键、策略、尝试序号、输入/输出 snapshot、状态和终止原因。每类失败须冻结最大修正次数、超时、不可重试错误和人工接管条件，Worker 重启后仍可恢复且不得重复执行副作用。

### 13.6 VerificationAgent 测试能力扩展

`VerificationAgent` 现有 `_smoke_http` 仅执行 `compileall`、启动应用并探测最多四个 HTTP 路由，不运行 pytest。为满足「真实测试反馈」，须补充：

- 依 Runtime Profile / 项目约定解析并执行 pytest 或等价测试命令；
- 测试结果与构建、smoke 失败一并纳入 §13.5 的回灌闭环；
- 测试命令缺失或不可用时，明确降级路径（不静默跳过验证）。

### 13.7 影子 / Canary 对照

在将 `agentic` 设为默认前，必须运行对照实验：以影子流量或小比例 canary，在真实代表性任务样本上比较 `artifact-backed` 与 `agentic` 的：

- 端到端任务成功率（机器验证优先于 LLM-as-a-Judge）；
- 单任务 mean cost 与 cost per verified success；
- 墙钟延迟与完成时间分布；

生成策略实验不得直接占用 P2-4 的 A_single_agent/B_certified_hive/C_control 组织维度。GQ-0 应建立独立的 generation-strategy experiment contract，仅复用 P2-4 的冻结任务集、统计与 DecisionRecord 基础设施；结果作为 §13.4 默认切换依据，之后再由 P2-4 评估组织效应。

任务集必须预注册用户场景、难度与框架分层，隔离调参与最终测试样本，并指定独立盲评 owner。实验运行前须冻结最小成功率提升或非劣界、最大成本和 P95 延迟退化、最低样本量、停止规则、失败/超时计分及严重质量与安全护栏。

影子任务必须运行在独立 sandbox 与 Artifact namespace，禁止发布和外部副作用；canary 使用稳定分桶。配置须提供 kill switch，回滚时新 Run 使用旧策略，在途 Run 按已冻结 GenerationPlan 完成或显式取消，禁止中途无证据换生成器。

#### 13.7.1 GQ-3 Canary 控制流

- 启动期构造 `GeneratorSelector`（`generator_factory.build_generator_selector`）：**轻量** `ArtifactBackedCodeGenerator` 可立即持有；`AgenticCodeGenerator` **首次命中 agentic 策略时再懒构造**（避免启动期双实例死重）。编排器与 API 注入选择器而非单例。
- 生成时按 `goal_id` 调用 `GeneratorSelector.select(goal_id)`，由 `resolve_effective_generation_strategy` 解析有效策略后返回对应生成器，再对该具体生成器做 `assert_generator_consistency`。由此 canary 选中的 `agentic` goal 真正使用 agentic 生成器，而非因单例不符而 fail-closed（历史 bug）。
- canary 排序强制：解析中先查 kill switch，再经 `canary_rollout_allowed(kill_switch, gq2_closed)` 校验 `generation_strategy_canary_gate`（GQ-2 验证后由运维置 True）。`canary_gate=False`（默认）或 `canary_percent=0` 时，任何 goal 都回落默认策略。
- **已剪死重（2026-07-31）**：artifact-backed 写文件后默认**不再**额外调用 `validate_goal_alignment_semantic`（该 LLM「语义对齐」**不是**质量验证 / **非** fail-closed 真实验证；真实验证仍为 build/deploy/smoke/pytest）。仅当显式 `REGENT_GOAL_SEMANTIC_ALIGNMENT_ENABLED=true` 时才启用。

#### 13.7.2 GQ-4 默认切换控制流

- 实验驱动 `drive_generation_strategy_experiment(config, runner)` 注入 `runner(variant, task) -> StrategyRunResult`，跑通双臂并聚合 `UserQualityMetrics`（O9/O10 producer 即 runner）。
- 晋级须经强制门：`apply_gq4_promotion(experiment_report, kill_switch=, decision_record_ref=)` 调用 `gq4_default_switch_gate`；仅当 `PROMOTE_AGENTIC_CANDIDATE` 且无 kill switch 才返回允许，否则 `DomainError(POLICY_DENIED)` 阻止晋级。`evaluate_gq4_promotion` 为非抛出版本供巡检。
- 运行时默认仍由 `generation_strategy` 驱动（Settings 代码默认 `artifact-backed`）；晋级步骤为「实验报告 → `apply_gq4_promotion` 通过 → 记录 DecisionRecord → 运维翻转 `REGENT_GENERATION_STRATEGY=agentic`」。kill switch 在运行时始终覆盖默认。
- **运维覆盖**：生产 `.env` 可设置 `REGENT_GENERATION_STRATEGY=agentic` 作为运维侧运行时覆盖，**不等于** GQ-4 已正式晋级；部署流程不得擅自改写生产策略，除非 DecisionRecord 明确要求。

### 13.8 Agent 工具执行环境与两级 Effect（对话式交付前置）

> 依据：架构评审 C2 / R0-1；统一计划 CD-0；PRD §4.4 / §10.5。

#### 13.8.1 沙箱强制

- `AgentRunner` / `agent/tools.py` 中的命令与文件效应**必须**经 `infrastructure/sandbox.py` 的 `DockerSandboxDriver`（或等价隔离驱动）执行；**禁止**在持有数据库凭据与 Provider API key 的 worker 宿主进程内直接 `create_subprocess_shell`。
- 生产配置：`sandbox_mode` 必须为 `docker`；`local` 仅允许显式测试/开发环境，且不得接入生产 canary。
- 白名单含 `pip` / `python` / `curl` 时，隔离与出网策略必须满足 §19「网络默认拒绝」与 §13「断网非 root 构建」的等价约束（影子任务禁止发布与外部副作用）。
- **本条未满足前，禁止**将 `generation_strategy_canary_gate` 置 True 或提高 `canary_percent` 于生产（即使 §13.7.1 控制流代码已就绪）。

#### 13.8.2 两级 Effect 模型

正式承认 Agent 循环内已存在的分层，并补齐审计：

| 效应类型 | 治理 | 要求 |
|---|---|---|
| 沙箱内可逆效应（写工作区文件、跑测试、读代码） | **事后** Effect / Transcript 日志 | append-only；与 Outbox **同事务或同幂等键可对账**；不得 `except: pass` 静默丢弃 |
| 不可逆 / 外部效应（部署、付费 API、对外发消息、生产发布） | **前置** ExecutionPermit | 复用 `REQUESTED→GRANTED→CLAIMED→CONSUMED` |

LLM 仍只提出结构化 Command / Tool 调用；聚合状态转换由确定性 Application Service 执行。不得为顺滑删除 Permit / Outbox / Evidence / Audit / Reconciler。

#### 13.8.3 交付状态机接线

- `application/delivery_state.py` 的 `decide_delivery_verdict` / `DeliveryState` **必须**被 `execution_orchestrator` 生产路径消费，并写入 `goal.metadata_json["delivery_state"]`。
- 交付拒绝须使用类型化错误（建议 `DeliveryRejection`），携带 `gap_kind`、`reasons`、`draft_uri`；禁止仅依赖魔法字符串 `delivery-review-v1 rejected...` 作为唯一契约。
- `goal_intent` / 需主观判断的 gap：在能力阶梯耗尽**之前**转入 `DELIVERED_FOR_REVIEW`（或等价 WAITING_HUMAN），并保留当前最优产出。
- AC1 门禁（`ops/delivery_dead_end_gate.py`）须覆盖嵌套函数所在方法体，TARGETS 含真实终态文件，并进入 CI。

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
### 17.1 A2A 兼容投影（非内核协议替换）

内部 Agent 继续使用全轨迹、可审计的 `AgentEnvelope`；A2A 只作为未来跨组织互操作的边界投影。映射合同：

| Regent | A2A 投影 |
|---|---|
| `goal_id` / `correlation_id` | `contextId` |
| Run `QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED` | `submitted/working/completed/failed/canceled` |
| `WAITING_HUMAN` | `input_required` |
| Permit 待授权 | `auth_required` |
| Capability/AgentSpec 声明 | 签名 Agent Card 的受限视图 |

Agent Card 只能声明身份和能力，不能授予当前 Goal 权限。进入内核前仍需身份 allowlist、签名验证、Permit/fencing、租户隔离与 `UNTRUSTED_DATA` 标记。不得采用 A2A 的不透明语义弱化内部证据链。

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
### 18.1 组织路由特征与裁剪

`TopologyPlanner` 的输入必须包含：

```text
TaskFeatures
- tool_call_density
- decomposability_score
- sequential_dependency_score
- single_agent_baseline_success_rate
- independent_verification_required
- estimated_parallelism_ceiling
```

`OrganizationSpace` 先按 PRD §10.1 的冻结规则裁剪，再由 `UtilityFunction` 计算效用。裁剪结果、命中规则、输入特征版本和被排除拓扑必须写入 `SchedulingDecision`，不得只保存最终模板。

### 18.2 过程级调度记录

每次派工新增不可变 `DispatchDecision`（可先作为 `SchedulingDecision` 的版本化子记录实现）：

```text
DispatchDecision
- goal_id / run_id / step_id / organization_version_id
- source_agent_id / selected_agent_id / candidate_agent_ids
- evidence_refs / reason_code / policy_version
- capability_scope / permit_refs
- input_digest / output_digest
- created_at
```

重放必须能解释“派给谁、为什么、依据什么”，并能按时间序列计算 `dispatch_entropy`。调度器不得从 Agent 自述推断权限或认证状态。

### 18.3 指标计算合同

- `coordination_token_share = coordination_message_tokens / total_tokens`；分母含全部 Agent、编排器和评价调用，缓存 Token 单列；
- `error_amplification_factor` 只在版本化故障注入任务上计算，保存注入点、预期影响边界、实际受影响节点和独立评价证据；
- `dispatch_entropy` 对每个可派工步骤保存候选概率/权重分布及熵，报告均值、斜率、峰值和终止前窗口；
- 缺字段时结果为 `INSUFFICIENT_EVIDENCE`，不得用 0 填充；
- 指标定义、阈值和计算器版本进入任务集哈希与 DecisionRecord。

### 18.4 MAST failure_code 命名空间

新增稳定前缀 `MAST_`，首批至少包含：

```text
MAST_STEP_REPETITION
MAST_PREMATURE_TERMINATION
MAST_ROLE_BOUNDARY_VIOLATION
MAST_REASONING_ACTION_MISMATCH
MAST_CLARIFICATION_NOT_REQUESTED
MAST_IGNORED_PEER_OUTPUT
MAST_IMPLICIT_DECISION_CONFLICT
MAST_VERIFICATION_MISSING
MAST_VERIFIER_FAILURE
```

分类器输出必须带轨迹引用和置信度；低置信度保留原始错误码并进入人工/离线复核，不得覆盖事实错误。

### 18.5 模板整体认证

`OrganizationTemplate` 的认证摘要必须覆盖 `member_manifest_hash`、`topology_hash`、`model_endpoint_hash`、`prompt_skill_tool_hash` 和 `verification_contract_hash`。任一摘要改变即创建新版本，旧认证不得继承。认证测试以整套模板运行，至少包含正常、澄清、同伴输出冲突、验证者拒绝和错误注入场景。

### 18.6 长任务上下文与持久计划

当前 `todo_write`、`micro_compact`、`autoCompact` 为实现基础，但需要以下耐久化补强：

1. `ExecutionPlanItem` 持久化 `status/owner/dependencies/evidence_refs/next_action/version`，通过 Goal/Run checkpoint 恢复；
2. 工具结果超过配置阈值（初始建议 20k Token）时写入不可变 Artifact，消息保存 URI、SHA-256、MIME、长度和截断预览；
3. 每次 autoCompact 前保存完整 Transcript Artifact；压缩摘要使用 `goal_intent/produced_artifacts/open_risks/next_actions` 结构；
4. rehydration 必须校验 Artifact 哈希，并重新注入硬约束、Permit 状态和未决 HumanTask；
5. 相关指标记录压缩次数、压缩前后 Token、Artifact 读取命中和恢复失败，不把压缩本身视为成功。

### 18.7 MCP 工具面边界

能力池的工具接入优先提供 MCP 兼容适配器，但 MCP 只负责工具发现与调用语义。`CapabilityRegistry`、认证、细粒度授权、Permit、fencing、审计和撤销仍由 Regent 管理。第三方 Agent 框架只能作为能力池内单个 Agent 的封装，不得进入 Kernel 替换 Outbox、Lease、状态机或证据链。

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

### 21.1 规范意图 ↔ 实际路由（双列对照）

> 以 `core/src/regent/api/main.py` 的 `include_router` 为**唯一上线真相**。勿为对齐文档新增废弃形态端点。

| 规范意图 | 实际实现 | 状态 |
|---|---|---|
| `POST /goals` | `POST /v1/goals`；Console 主链路为 `POST /v1/app-projects/drafts`（含 auto-start） | ✅ |
| `GET /goals/{id}` | `GET /v1/goals/{id}` | ✅ |
| `POST /goals/{id}/freeze` | 无独立端点；显式确认路径为 `POST /v1/app-projects/{id}/confirm`；主链路快照见 DecisionNote auto-start | ✅ 产品包装 |
| `POST /goals/{id}/resume\|cancel` | `POST /v1/goals/{id}/transitions`（Command：`RESUME` / `CANCEL` 等） | ✅ |
| `POST /humantasks/{id}/approve\|reject` | `POST /v1/human-tasks/{id}/complete`（`decision` 字段分流 allow/deny） | ✅ 已挂载 |
| `GET /decisions/{id}` | `GET /v1/scheduler/decisions/{id}`；另有 `GET /v2/organizations/{goal_id}/decisions` | ✅ 命名空间不同 |
| `GET /audit/{goal_id}` | 经 governance / goal 投影与审计导出能力提供；无顶层 `/audit` 别名 | 🟡 能力存在、路径不同 |

**Console / 对话主链路（已挂载）：**

```text
POST /v1/app-projects/drafts
POST /v1/app-projects/{id}/guidance
POST /v1/app-projects/{id}/confirm
GET  /v1/app-projects/{id}/delivery-review
POST /v1/conversations/{id}/messages          # CD-4：绑定 AppProject 时触发 guidance
POST /v1/human-tasks/{id}/complete
POST /v1/uploads
```

**其它已挂载族（规范未逐一枚举，以实现为准）：**  
`/v1/works`、`/v1/observations`、`/v1/baselines`、`/v1/governance`、`/v1/side-effects`、`/v1/experiments`、`/v1/self-improvement-runs`、`/v1/tools`、`/v1/memories`、`/v1/eval-runs`、`/v1/scheduler`、`/v1/runtime-profiles`、`/v1/webhooks`、`/v1/reports`、`/v1/public-deploy`、`/v1/deployments/*`、`/v2/*`（aar1）。

App-projects 入口族是对 Goals 清单的**产品包装**，不是静默废弃 Goal 状态机。破坏性路径变更进入 `/v2`。
---

## 22. 可观测性

统一记录 correlation、causation、Goal、Work、Run、Event、ExternalOperation、Actor。  
必须观测：Outbox/Dead Letter、Lease、阶段耗时、UNKNOWN、对账、Permit/fencing、预算、Agent 协调 Token、`coordination_token_share`、`error_amplification_factor`、`dispatch_entropy`、MAST failure_code、压缩与恢复、Observation 排除原因、Decision 证据链。Agent 步骤优先对齐 OpenTelemetry GenAI 语义约定；语义版本必须记录，未稳定字段不得成为唯一事实源。

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

> 截至 2026-07-31，代码统计：core 含多 Agent 补足模块（metrics/MAST/member_contract/TaskFeatures/DispatchDecision/ExecutionPlanItem 等）与 GQ 生成质量控制流，迁移 head `20260731_0041`。

### 已完成

> 下列「已完成」指**结构/链路已落地**；可验证交付以治理管道行为 pytest + 非桩 Eval 为准，不以模块存在或结构级断言为准（见 DecisionNote `docs/decision-note-verifiable-delivery-2026-07-30.md`）。

- **P0 全链路**：Goal/Work/Run 三套状态机、Outbox、Lease、Timer、Artifact、Evidence、Audit，通过 CSV_SUMMARY_BASELINE。
- **P1 R1–R6**：Goal 解释与分解、Discovery 编排、证据连接器、假设决策、需求修订、能力解析、WorkspaceWriter、Generation Service、Docker 沙箱构建、Preview 发布、观测回流、Gate 评估与 Iteration Decision。
- **P1 R7**：DeploymentSmokeTestService 实现简化可体验性 Gate。
- **P1 R8**：IterationLoopService 实现 Observation→Decision→REVISE 端到端闭环。
- **对话与 App 身份**：Conversation 持久化、AppProject、GoalSpec DRAFT/FROZEN/SUPERSEDED、对话驱动修订（0019）。主链路 GoalSpec 冻结语义见 DecisionNote `docs/decision-note-auto-start-journey-2026-07-31.md`（快照启动 + 事后纠偏）；`/confirm` 保留为纠偏路径。
- **真实 App 预览**：StaticAppPublisher、CSP 预览、观测钩子（0020）。
- **受监管自我改进（候选，未产品门禁）**：SelfImprovementRun 隔离副本、AST 验证、独立审查（0021）已落地代码；按 PRD §9.3 属 P2-8 候选，需单独产品 DecisionRecord 后方可宣称验收完成。
- **确认后自主执行闭环**：Outbox 指数退避与死信（0022）。产品语义已从「Confirm/Start 硬分离」更新为「快照启动 + 纠偏」（同上 DecisionNote）；发布审批仍独立且默认需要人类批准。
- **Durable Hive（opt-in 固定模板）**：认证模板 `pm-dev-independent-qa-v1` 经 `REGENT_AAR1_CERTIFIED_HIVE=true` 启用（生产服务器已开；本地/测试默认仍关以保 P0 单 Agent 基线）。该 flag 现受 §18.5 / MA-2 整体认证摘要约束（成员契约 + 五类 hash + 回归；摘要变更即旧认证失效）。能力 C/V/R 满足时优先该固定模板；**产品默认语义仍是强单 Agent champion**；自适应自由拓扑 `ROLLOUT_NOT_ALLOWED`，不得表述为已验证的默认并行执行能力。
- **多 Agent 补足（MA-0～MA-6，2026-07-31）**：已落地指标合同、MAST 词表、成员契约、`ExecutionPlanItem` / `DispatchDecision`（迁移 `0039`）与模板认证回填（迁移 `0040`）。固定 Hive 候选必须通过五类摘要复算，opt-in 不得绕过 TaskFeatures 裁剪；Agent 生成主链已接入 todo 持久化、大结果卸载与压缩前 Transcript Artifact，固定 Hive 派工已接入过程审计。P2-4 仍是实验骨架，**P2-5 自适应拓扑仍禁止启用**。见 Plan §12。
  > **更正（2026-08-01 代码核查）**：MAST 失败码（`application/mast_failure.py`）已定义 9 码与 `classify_mast_failure`，但截至核查**尚未接入生产分类路径**（全库零生产引用，仅测试引用）；§18.4 要求的"轨迹引用 + 置信度 + 人工/离线复核"接入逻辑缺失，当前应视为"定义就绪、集成待 P2-4"，不得宣称已部署生效。另：PRD §12 原列 P2-3 Impact Graph / P2-5 AgentEnvelope HMAC / G0 ExternalOperation 核心闭环**均已实现**（见 `docs/registered-unimplemented-2026-07-30.md`），已从"未实现"清单移除，本 Spec 不再与 PRD 冲突。
- **单 Agent 生成质量基线（GQ-0～GQ-4，2026-07-31）**：生成器元数据协议与 fail-closed 一致性；Worker 按 generation_strategy 分派；FailureEnvelope/RepairAttempt；独立生成策略实验合同；VerificationAgent pytest/项目测试与预算化修正；GQ-3/GQ-4 **控制流已实现**（标签：**已实现但默认不可启用** — canary_gate=False、canary_percent=0、代码默认 artifact-backed）。生产 .env 覆盖 ≠ GQ-4 晋级。GQ-3 真实流量窗与 GQ-4 DecisionRecord 仍待运维实验（见 docs/decision-note-gq4-pending-2026-07-31.md）。生产 CERTIFIED_HIVE opt-in **不扩容**。
- **控制台前端**：React 19 + Vite + TS；SSE 自适应轮询；三栏布局；status.agents + live_action；GET /v1/app-projects/{id}/delivery-review 审阅面（plan/transcript/verification/budget）。
- **桌面端（探索性）**：Tauri 骨架；未纳入 P0/P1 验收。
- **交付状态机（CD-1，2026-07-31）**：decide_delivery_verdict **已接入** _apply_delivery_verdict；DeliveryRejection 类型化；goal_intent 早交人；AC1 门禁进 CI。
- **下一步（CD-6…CD-12）**：**CD-6 代码侧已落地**（agent-exec 镜像、`--entrypoint sh`、`host_path_map` fail-closed、uid 对齐、T1–T6；见 `docs/cd6-execution-plan-2026-07-31.md`）。CD-7.1/7.4 已落地；7.2/7.3/7.5 与 GQ-3 窗仍待。权威：`docs/conversational-delivery-next-plan-2026-07-31.md`。生产 docker 三联未在目标主机验收前禁止开 canary。
- **API 挂载（F-1 修复）**：human_tasks / uploads / webhooks / reports / public_deploy 已在 `api/main.py` `include_router`。

### 已知非阻塞限制

1. ~~ReleaseCandidate 在 P1 执行链上自动批准~~ **已修复（2026-07-30）**。
2. 完整浏览器级 R7 gate：无 Playwright 时 browser_journey 为 dry-run。
3. ExternalOperation 跨 provider 真实网络 query→resolve 生产对账仍为后续切片。
4. Eval Harness 已改为交付信号/Goal 证据评分。
5. ~~PRD §7.1–7.3 隐私缺口~~ **已修复（2026-07-30）**。
6. SSE LISTEN/NOTIFY、token 流式、DeliveryRecoveryCoordinator 抽离仍为体验/结构持续项。

> 更正（2026-07-30）：Evidence Connector / StaticPreviewDeploymentProvider 已接线。

### 状态标签约定（F-9）

| 标签 | 含义 |
|---|---|
| **已完成** | 生产路径可用且默认启用 |
| **已实现但默认不可启用** | 控制流/代码就绪，须门禁或 DecisionRecord 后方可开流量（如 GQ-3/GQ-4） |
| **门禁就绪，实验窗待运维** | 前置合规已满足代码侧；缺真实流量实验 |
| **PENDING** | DecisionNote 未 ACCEPTED（如 GQ-4 晋级） |

### 已关闭的历史阻塞（对齐审计 F-1…F-3 / CD）

1. ~~Agent 工具宿主 create_subprocess_shell~~ → WorkspaceToolkit + build_agent_sandbox；smoke 改沙箱探针脚本。
2. ~~Transcript except: pass~~ → 失败抛 DeliveryRejection。
3. ~~对话层纯分类 / conversations 死路~~ → CD-4 guidance 链式 + append 触发。
4. ~~decide_delivery_verdict 未接线~~ → 已接入 orchestrator。
5. ~~5 个 API router 未挂载~~ → 已挂载。
6. ~~定义冻结测试指向 -v2.md~~ → 指向 CURRENT Regent-PRD.md / Regent-Technical-Spec.md。

> 更正（2026-07-31）：「agentic 默认不可达是缺陷」已撤销；status.agents 未填充已证伪。

---

## 26. 技术完成定义

阶段完成要求：对象与状态有数据库约束；API、Worker、Projection 和运维入口完整；正常、失败、UNKNOWN、重试、Dead Letter 与恢复有测试；副作用受 Permit、fencing、幂等和审计约束；真实结果满足产品合同；不把 mock、内部流量或结构检查当作成功；文档、代码、迁移和部署一致；形成唯一 DecisionRecord；且所依据的 PRD 状态为 `CURRENT`。
