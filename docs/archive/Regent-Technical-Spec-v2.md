# Regent 技术架构与实施规范 v2

> 状态：CURRENT  
> 性质：权威执行基线（Owner 批准升 CURRENT）  
> 日期：2026-07-22  
> 配套需求：`Regent-PRD-v2.md`  
> 适用范围：P1 Graduation 与 P2（文档复审通过前不得据此开工 P2 编码）  
> 附录：  
> - [`docs/appendices/State-Machines-and-Invariants.md`](docs/appendices/State-Machines-and-Invariants.md)  
> - [`docs/appendices/Durable-Execution-and-External-Effects.md`](docs/appendices/Durable-Execution-and-External-Effects.md)  
> - [`docs/appendices/Security-Tenancy-and-Recovery.md`](docs/appendices/Security-Tenancy-and-Recovery.md)

## 复审意见摘要

首轮：产品/架构批准；P2 编码不批准。  
Owner 批准（2026-07-23）：升为 CURRENT；P2Start 已签署；`p2-scheduler-01+` 允许开工。P2-4 仍先于自适应组织。

准入顺序：A 定义保护 → B 文档收口 → C G0 ExternalOperation → D 故障注入 → E–G Graduation → H 文档 CURRENT → I P2Start → J Scheduler。

实现现状：Permit claim/consume 与 UNKNOWN 叙述已有；完整 ExternalOperation / operation_key / dispatch_generation **须在 G0 实现**，不得推迟到 P2-1。

产品身份唯一引用 `docs/definitions/REGENT-DEFINITION-1.0.txt`（见 PRD §1.1）；本规范不得复制 DEFINITION_TEXT。

---

## 1. 架构目标与优先级

本规范不重新定义 Regent。产品身份唯一引用 `Regent-PRD-v2.md` §1.1 的 `REGENT-DEFINITION-1.0`；技术架构、对象模型、Agent 拓扑和阶段范围均不得反向改写该定义。若实现与定义冲突，应修改实现或明确记录尚未实现的能力。

Regent Core 必须在进程崩溃、重复投递、Provider UNKNOWN、模型输出错误和人工等待条件下保持事实一致、可恢复和可审计。LLM 提出结构化候选；确定性服务负责校验、状态转换、权限和提交。

```text
事实完整性 → 安全与治理 → 可恢复性 → 产品证据可信度
→ 结果质量 → 延迟与成本 → 扩展便利性
```

## 2. 系统边界

```text
┌──────────────── Regent Core ────────────────┐
│ API / Conversation / Projection             │
│ Goal & Product Control Plane                │
│ Scheduler & Execution Orchestrator          │
│ Governance / Permit / HumanTask / Secret    │
│ Evidence / Artifact / Observation / Audit   │
│ Capability / Runtime / Organization Registry│
│ ExternalOperation / Eval Harness            │
└────────────── ports + signed contracts ─────┘
        │          │          │          │
 Evidence      Build      Deployment   Model
 Provider      Provider   Provider     Provider
        │
┌────────────── Generated App ────────────────┐
│ independent source, deps, data, tests       │
│ independent release and product telemetry   │
└──────────────────────────────────────────────┘
```

禁止：Generated App 与 Core 相互 import；Agent 直接写状态；Provider 绕过 Permit；Conversation metadata 成为事实源；内部 smoke 满足产品 Gate；生成或部署阶段补造缺失业务功能。

## 3. 技术基线

- Python 3.12+、FastAPI、SQLAlchemy async；
- PostgreSQL 唯一事实源，Alembic 单一迁移链；
- Outbox、Worker Lease、Durable Timer；
- Pydantic 结构化协议；
- 不可变 Artifact 与内容哈希；
- Docker 或独立 Build Provider 隔离构建；
- Ruff、mypy strict、Pytest 和迁移检查作为合并门禁。

P2 前期继续采用模块化单体。除非测量证明数据库 Outbox 已成为瓶颈，否则不引入外部事件总线或微服务拆分。

## 4. 事实源与 Artifact 边界

| 层 | 权威性 | 示例 |
|---|---|---|
| 事实源 | 唯一可写权威 | PostgreSQL 中 Goal、Work、Run、Permit、ExternalOperation、Outbox |
| Artifact | 不可变内容寻址 | Evidence Snapshot、FileChangeSet、SBOM、VerificationReport |
| 投影 | 可重建 | Conversation Timeline、状态页 |
| 模型输出 | 候选，须校验 | Hypothesis、FileChangeSet 草案 |

删除投影后必须能从事实源 + Artifact 重建。Artifact 不能单独授权副作用。

权威对象树：

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

## 5. 完整状态机索引

事件链**不是**状态机。每个控制对象必须在附录 1 冻结：

```text
state → command → guard → resulting state → emitted event
terminal? / retry rule / timeout / cancel / unknown rule
```

数据库要求：`version` 列、Check Constraint、条件更新（`WHERE version = expected`）；不得只靠 handler 约定。

对象清单（细节见附录 1）：Goal、Work、Run、DiscoveryRound、AppBuild、Deployment、ResourceReservation、GateEvaluation、Permit、ExternalOperation、MemoryRecord、EvalRun。

## 6. Outbox / Inbox 与事件兼容

主链事件（兼容保留）：

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

事件必须包含 `event_id`、`event_type`、`schema_version`、聚合 ID/版本、`idempotency_key`、`correlation_id`、`causation_id`、时间与 payload。破坏性 schema 变更递增 `schema_version`；旧消费者 fail-closed 或显式兼容。

Handler 必须：

1. 校验 Schema 与聚合状态；
2. 用控制对象唯一键实现幂等；
3. 同事务提交状态、Audit 和下一 Outbox；
4. **外部调用不持有数据库事务**；
5. 可重试失败抛出并由 Dispatcher 退避；
6. 达到上限进入 Dead Letter；
7. UNKNOWN 不重复副作用，只查询和对账；
8. 终态失败写稳定 `failure_code`。

Inbox（若引入外部回调）同样幂等去重，并关联 `ExternalOperation`。

## 7. ExternalOperation（G0 实现，先于 Scheduler）

统一外部副作用控制对象（详见附录 2）。**最小闭环是 P1 Graduation G8 的前置依赖，禁止推迟到 P2-1。**

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

无 `IDEMPOTENT_REPLAY` 且无 query 能力的 Provider，不得自动不可逆副作用（见附录 2 能力矩阵）。

## 8. Permit 与 fencing

生命周期：REQUESTED→GRANTED→CLAIMED→CONSUMED；以及 DENIED/EXPIRED/REVOKED。

不变量：

1. Permit 1:1 ExternalOperation；claim 产生 **local_fencing_token**。
2. CONSUMED 与 DISPATCHING 同事务固化（见 §7）。
3. **本地 fencing** 防止旧 Worker 继续控制；**不假设**第三方 API 识别 token。
4. 重复外部效果靠 Provider idempotency / query；强撤权靠可选 native fencing 或 Regent egress gateway。
5. Lease 过期旧 Worker 不得提交；新 Worker 对账或新授权。

## 9. Scheduler Reservation / Ledger（P2-1）

见附录 1 §11–§13：ExecutionQueue、多资源原子预留、BudgetLedger+price_book_version、Aging/公平性、checkpoint/resume、可重放 SchedulingDecision。  
前置：G0 ExternalOperation 与 G8 已签署。

## 10. Runtime Profile 认证（P2-2）

```text
RuntimeProfile
- name / version / manifest_hash
- resolver_image_digest / sandbox_image_digest
- supported_artifact_types / dependency_policy
- build_commands / verification_contract
- resource_defaults / lifecycle_status
```

状态：DRAFT、CERTIFIED、DEPRECATED、REVOKED。只有 CERTIFIED 可用于新计划；模型只能在确定性兼容过滤后的集合中选择。

## 11. Evidence、Observation 与归因

Evidence 分类：

- `declared-intent`：用户声明；
- `sourced-observation`：受控外部来源快照；
- `build-verification`：构建与测试；
- `product-observation`：真实用户行为或反馈；
- `operational-observation`：内部 smoke 与监控。

规则：declared-intent 不能单独证明市场事实；operational-observation 不能满足产品价值 Gate；OBSERVED claim 必须引用 Evidence。

**UNTRUSTED_DATA**：必须携带 `trust_label`、`source_*`、`content_hash`、`parser_version`、`injection_site`、`retrieved_at`（附录 3）。它们只能作为数据，不得成为指令、授权或策略来源。

产品 Observation 至少包含：事件 ID、签名、Goal/Requirement/Release/Deployment 归因、指标及版本、来源、internal/bot/test 标记、观测时间。

## 12. Prompt Injection 威胁模型

按阶段**强制**执行（附录 3）：G0 含间接/工具输出/外泄/篡改评价器；P2-3 强制 Memory-delayed；P2-5 强制 Agent-to-Agent。不得用抽样跳过阶段强制项。

读取优先级：硬约束 > 当前冻结事实 > 当前外部证据 > VERIFIED 记忆 > CANDIDATE 记忆。

## 13. Memory Admission / Revocation（P2-3）

```text
Admission → Retrieval → Usage Trace → Impact Graph → Revocation → Revalidation
```

强制：准入 Guard、冲突处理、衰减、隔离、批量撤销、循环证据检测、图一致性恢复；重验证期间下游标 `REVALIDATION_REQUIRED`，不得支撑新的 PASSED Gate。

## 14. AgentEnvelope 与权限传播（P2-5）

Agent 间消息必须封装为 AgentEnvelope：

- `source_agent_id`、`dest_agent_id`、`capability_scope`；
- `permit_refs` / fencing 引用（不得隐式扩大）；
- `content_trust = UNTRUSTED_DATA`；
- `correlation_id` / 签名或 HMAC（按部署级别）。

权限只减不增：子 Agent 权限 ⊆ 父授权 ∩ GoalSpec 硬约束。Team Lead 不能扩大 GoalSpec 或 Permit。

## 15. 独立评价器与 Eval Harness（P2-4）

- 冻结版本与不可变 Rubric；
- 默认看不到 Agent 身份与组织形式（盲评）；
- 不能修改测试、指标或排除规则；
- 机器验证优先于 LLM-as-a-Judge；
- 保存 evaluator model、prompt hash、工具版本、校准版本；
- 低置信度或冲突升级人工。

最小 Eval Harness 必须记录：任务集哈希、基线配置、预算账本、种子、重复次数、墙钟与计算预算、pass@k、安全违规、DecisionRecord。  
**自适应组织（P2-5）不得在 Harness 统计 Gate 之前默认启用。**

## 16. 身份、租户、隐私与安全

- 凭据只来自环境 Secret、Secret Manager 或 Secret Broker；
- 禁止源码/脚本/fixture/日志/Artifact 明文；
- Artifact 路径与大小检查；镜像 digest；依赖 hash；
- 网络默认拒绝；Provider 调用绑定 Permit 与 ExternalOperation；
- 生产职责分离；安全拒绝路径有自动测试；
- tenant/org/project/goal 隔离；PII 最小化；导出/删除审计（见附录 3）。

## 17. 发布、迁移、回滚与灾备

```text
ReleaseCandidate → Preview → Staging → Production
```

Production：独立批准、Secret Broker、迁移与回滚计划、Canary/蓝绿、SLO、错误预算、观测窗口、事故接管。  
生成 Agent 不得拥有生产批准权或长期凭据。  
灾备（冻结，见附录 3 §6）：PostgreSQL **RPO ≤ 15 分钟**；控制面 API **RTO ≤ 2 小时**；Artifact 多副本，丢失恢复 ≤ 24 小时；每季/每年演练按附录执行。

## 18. 生成、构建与浏览器验证

### Generation

GenerationPlan 冻结 GoalSpec、Decision、Requirement、Resolution、Runtime Profile 与 Evidence Bundle 摘要；模型只返回 FileChangeSet；WorkspaceWriter 拒绝路径逃逸；同一 GenerationRun 一个 WorkspaceSnapshot。

### Dependency 与 Build

受控 Egress + Permit；锁文件与 SBOM；断网非 root 构建；PASSED 须完整 VerificationReport。

### Journey

RequirementRevision 提供机器可执行 Journey；存在按钮或事件属性不构成成功。

## 19. API 原则

- `/v1` 保持兼容，破坏性变化进入 `/v2`；
- 202 返回可查询控制对象 ID；
- 写 API 接受幂等键；
- 状态 API 返回事实投影与对象引用；
- 稳定游标分页；错误含 `error_code`、`message`、`correlation_id`。

建议入口：`/v2/scheduler/queue`、`/v2/runtime-profiles`、`/v2/organizations/proposals`、`/v2/memories`、`/v2/eval-runs`、`/v2/experiments`、promote、rollback。

## 20. 可观测性与恢复

统一记录 correlation、causation、Goal、Work、Run、Event、ExternalOperation、Actor。  
必须观测：Outbox/Dead Letter、Lease、阶段耗时、UNKNOWN、对账、Permit/fencing、预算、Agent 协调 Token、Observation 排除原因、Decision 证据链。  
Dead Letter 重放需授权、操作者与原因，并继续使用原业务幂等键。

## 21. 故障注入、并发、性能与恢复门禁

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

## 22. 实施阶段

### G0：P1 Graduation（含 Durable External Effects）

按 PRD §5：`SYSTEM_GRADUATED` + `PRODUCT_EVIDENCE_GRADUATED`；**先实现**最小 ExternalOperation + Permit 原子消费 + operation_key + 故障注入（G8）；Browser Journey；真实 Evidence；质量门禁；Git/CI/Release。

| 阶段 | 批次依赖 | 核心交付 |
|---|---|---|
| G0 ExternalOperation | 依赖当前 Alembic head 之上的**下一 revision**（勿预占固定编号） | EO 表、operation_key、dispatch_generation、原子 CONSUMED、对账 |
| P2-1 Scheduler | 依赖 G0 revision 已应用 + Graduation/P2Start | Queue、Reservation、BudgetLedger、抢占 |
| P2-2 Runtime Registry | 依赖 P2-1 DecisionRecord（若拆分发布）或同里程碑合同 | Profile、Certification、构建矩阵 |
| P2-3 Memory | **条件承诺**：前序 DecisionRecord | MemoryRecord、Impact Graph、Revocation |
| P2-4 Minimal Eval Harness | **承诺**；可与 Scheduler/Runtime 规划并行，组织不得早于其 Gate | 任务集、基线、预算账本、盲评、统计 Gate |
| P2-5 Adaptive Organization | **条件**：P2-4 正净收益 DecisionRecord | 组织提案、路由 |
| P2-6 Experiment Platform | **条件**：Harness 可用 DecisionRecord | 完整 Champion/Challenger |
| P2-7…P2-9 | **候选**：单独产品 DecisionRecord | 生产发布 / 自我改进 / 能力生态 |

迁移文件命名使用日期+描述；**revision id 在实现时由 Alembic 生成**，技术路线不预占 `0023–0031` 等号码。

## 23. 技术完成定义

阶段完成要求：对象与状态有数据库约束；API、Worker、Projection 和运维入口完整；正常、失败、UNKNOWN、重试、Dead Letter 与恢复有测试；副作用受 Permit、fencing、幂等和审计约束；真实结果满足产品合同；不把 mock、内部流量或结构检查当作成功；文档、代码、迁移和部署一致；形成唯一 DecisionRecord；且所依据的 PRD/本规范状态为 `CURRENT`。
