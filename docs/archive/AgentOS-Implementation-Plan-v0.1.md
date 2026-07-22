# AgentOS V0.1 可落地技术方案

> **已被 `AgentOS-Implementation-Plan-v0.2.md` 取代。** 本版本错误地将 Challenge 同时建模为用户输入和 Core 对象，并将场景目录放入 Core 工程。保留本文仅用于历史追踪。

> 状态：编码基线  
> 日期：2026-07-16  
> 目标：用最小工程复杂度跑通“薄 Challenge → 自主解释 → 能力与组织 → 持久执行 → 独立评价”的纵向闭环。

## 1. V0.1 交付范围

### 必须交付

1. Challenge 目录加载、校验和版本冻结。
2. Goal、Work、Run、HumanTask 的持久化状态机。
3. Worker 崩溃和进程重启后的恢复。
4. LLM 驱动的 Goal Interpretation、假设和候选计划。
5. Capability Requirement、Provider、Gap 和限定认证。
6. 动态生成版本化 AgentSpec。
7. Candidate Organization、Assignment 和组织评审。
8. AI、Human、Tool 三类 Actor 适配器。
9. Artifact/Evidence 工作空间。
10. 独立 Evaluation。
11. PolicyDecision、资源配额、审计和 Kill Switch。
12. External KPI Observation 接口。
13. 两个 Challenge 均可加载，且不修改 Core 代码。

### V0.1 不交付

- 通用 Tool Builder；
- 自动生产发布；
- 通用 World Model、Simulator、能力本体；
- 微服务、Kafka、Kubernetes；
- 自动修改根 Goal、KPI 或治理规则；
- 复杂前端控制台；
- 完整商业产品功能。

## 2. 技术栈

### 后端

- Python 3.12
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x async
- Alembic
- PostgreSQL 16+
- pytest、pytest-asyncio
- structlog
- OpenTelemetry

### Worker 与调度

V0.1 使用 PostgreSQL：

- Transactional Outbox
- `FOR UPDATE SKIP LOCKED` 数据库任务队列
- Durable Timer 表
- Worker Lease/Heartbeat
- Inbox 幂等消费记录

不引入 Celery、Redis 或 Kafka。吞吐出现真实瓶颈后再评估消息中间件。

### Artifact

- 开发环境：本地文件系统
- 生产环境：S3 兼容对象存储
- 数据库只保存 URI、内容哈希和元数据

### AI Runtime

定义内部 `ModelProvider` 协议，第一版只实现一个模型供应商适配器。模型输出必须通过 Pydantic Schema 验证。

### 管理界面

第一阶段 API-first，使用 OpenAPI 和最小管理页面。核心闭环稳定后再增加独立 Web Console。

## 3. 部署拓扑

```text
┌────────────────────┐
│ Control API        │
└─────────┬──────────┘
          │
┌─────────▼──────────┐       ┌──────────────────┐
│ PostgreSQL         │       │ Object Storage   │
│ State/Queue/Outbox │       │ Artifact/Evidence│
└─────────┬──────────┘       └──────────────────┘
          │
┌─────────▼─────────────────────────────────────┐
│ Workers                                        │
│ Deliberation | Workflow | AI Actor | Evaluation│
└────────────────────────────────────────────────┘
```

开发环境通过 Docker Compose 启动 PostgreSQL、API 和 Worker。

## 4. 工程目录

```text
agentos/
├─ pyproject.toml
├─ alembic.ini
├─ docker-compose.yml
├─ src/agentos/
│  ├─ api/
│  ├─ application/
│  ├─ domain/
│  │  ├─ challenges/
│  │  ├─ goals/
│  │  ├─ capabilities/
│  │  ├─ organizations/
│  │  ├─ work/
│  │  ├─ actors/
│  │  ├─ artifacts/
│  │  ├─ evaluation/
│  │  └─ governance/
│  ├─ infrastructure/
│  │  ├─ db/
│  │  ├─ model_providers/
│  │  ├─ object_store/
│  │  └─ sandbox/
│  ├─ runtime/
│  │  ├─ dispatcher/
│  │  ├─ workers/
│  │  ├─ timers/
│  │  └─ leases/
│  └─ observability/
├─ challenges/
│  ├─ ai-professional-app/
│  └─ adult-fairy-tales/
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  ├─ recovery/
│  ├─ security/
│  └─ conformance/
└─ docs/adr/
```

领域层不得导入 FastAPI、SQLAlchemy 或模型供应商 SDK。

## 5. 模块边界

### Challenge Gateway

输入：Challenge 目录。  
输出：不可变 `ChallengeSnapshot`。

只允许：Goal、Success Criteria、Constraints、Resources、Authority、Evaluation、Termination。

### Goal Runtime

拥有 Goal 状态、GoalInterpretation 和阶段转换。其他模块不得直接修改 Goal。

### Deliberation

调用 LLM 生成 Proposal：目标解释、假设、探索计划、能力需求和组织候选。Proposal 经 Schema、Policy 和应用服务校验后才生效。

### Capability Runtime

拥有 Requirement、Provider、Gap、Acquisition 和 Certification。普通失败不得由 Actor 自行标记为 CapabilityGap。

### Organization Runtime

拥有 Organization、Assignment、CoordinationContract 和 Review。Scheduler 不能修改权限或认证。

### Work Runtime

拥有 Work Graph、Run、Timer、Lease、重试和恢复。

### Actor Runtime

通过适配器执行 AI/Human/Tool/Service，不能直接访问控制数据库。

### Evaluation

独立读取 Artifact/Evidence，输出 Evaluation。执行者自评只能作为低等级证据。

### Governance

所有副作用、权限扩大、资源消耗和高风险状态转换必须通过 PolicyDecision。

## 6. 首批数据库表

### 第一纵向切片必须实现

```text
challenge_snapshots
goals
goal_interpretations
works
runs
artifacts
evaluations
outbox_events
worker_leases
audit_records
```

### 第二纵向切片增加

```text
capability_requirements
capability_providers
capability_gaps
capability_certifications
agent_specs
organizations
assignments
coordination_contracts
human_tasks
policy_decisions
resource_usages
observations
inbox_receipts
durable_timers
```

### 通用字段

所有业务表至少包含：

```text
id UUID
goal_id UUID NULLABLE
version INTEGER
status VARCHAR
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
created_by VARCHAR
correlation_id UUID
metadata JSONB
```

状态更新使用：

```sql
UPDATE ...
SET status = :next_status, version = version + 1
WHERE id = :id AND version = :expected_version
```

更新行数为零表示并发冲突。

## 7. 核心状态机

### Goal

```text
DRAFT → QUALIFYING → EXPLORING → EXECUTING → OPERATING
→ PAUSED → ACHIEVED | PARTIAL | FAILED | CANCELLED
```

### Work

```text
BLOCKED → READY → ASSIGNED → RUNNING → EVALUATING → COMPLETED
```

异常分支：

```text
WAITING_EXTERNAL | WAITING_HUMAN | RETRY_WAIT
| UNKNOWN_OUTCOME | REPLAN_REQUIRED | FAILED | CANCELLED
```

### Run

```text
CREATED → CLAIMED → RUNNING
→ SUCCEEDED | FAILED | TIMED_OUT | UNKNOWN_OUTCOME | CANCELLED
```

### Organization

```text
PROPOSED → CHECKING → ACTIVE → REVIEWING → ADJUSTING
→ ACTIVE → DISSOLVED | FAILED
```

LLM 不得直接执行状态转换。

## 8. Domain Event

统一事件格式：

```text
DomainEvent
├─ event_id
├─ event_type
├─ aggregate_type
├─ aggregate_id
├─ aggregate_version
├─ occurred_at
├─ actor
├─ correlation_id
├─ causation_id
└─ payload
```

首批事件：

```text
ChallengeLoaded
GoalCreated
GoalQualificationRequested
GoalInterpretationProposed
GoalInterpretationAccepted
WorkCreated
WorkReady
RunRequested
RunStarted
RunCompleted
RunFailed
EvaluationRequested
EvaluationCompleted
GoalStageChanged
HumanTaskCreated
CapabilityGapProposed
OrganizationProposed
OrganizationActivated
OrganizationReviewRequested
```

状态更新、AuditRecord 和 OutboxEvent 必须在同一事务提交。

## 9. Worker 协议

Worker 获取任务：

```sql
SELECT * FROM work_queue
WHERE available_at <= now()
  AND status = 'READY'
ORDER BY priority DESC, available_at
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

Claim 时写入：

```text
lease_owner
lease_expires_at
heartbeat_at
attempt
```

规则：

- Worker 定期 Heartbeat；
- Lease 过期后其他 Worker 可接管；
- Handler 必须幂等；
- eventId 与 handlerName 形成 Inbox 唯一键；
- 外部调用使用 idempotencyKey；
- 超时且结果不明进入 UNKNOWN_OUTCOME，不直接重试。

## 10. Actor 协议

```python
class ActorAdapter(Protocol):
    async def describe(self) -> ActorDescriptor: ...
    async def accept(self, work: WorkContract) -> Acceptance: ...
    async def start(self, run: RunContext) -> RunHandle: ...
    async def poll(self, handle: RunHandle) -> RunProgress: ...
    async def cancel(self, handle: RunHandle) -> None: ...
```

### AIActor

- 输入使用 Goal/Work/Artifact 引用装配；
- Prompt、模型和 Tool 版本写入 Run Snapshot；
- 输出必须符合指定 Schema；
- Schema 失败允许有限修复，不得无限重试。

### HumanActor

- 创建持久化 HumanTask；
- 支持截止、提醒、升级和无响应策略；
- 等待期间不占 Worker。

### ToolActor

- 输入输出结构化；
- Tool Gateway 校验权限、配额和参数；
- 保存调用回执和副作用证据。

## 11. Artifact 与工作空间

```text
Artifact
├─ id
├─ goal_id
├─ type
├─ schema_ref
├─ uri
├─ content_hash
├─ producer_ref
├─ provenance
├─ created_at
└─ version
```

要求：

- Agent 通过 Artifact 引用协作；
- 聊天记录不是权威状态；
- Artifact 默认不可变，修改产生新版本；
- 每个 Goal 独立 Workspace；
- 跨 Goal 读取需显式授权。

## 12. Challenge 文件

```text
challenges/<challenge-id>/
├─ challenge.yaml
├─ constraints.yaml
├─ resources.yaml
├─ success.schema.json
└─ evaluator-bindings.yaml
```

两个初始 Challenge：

```text
ai-professional-app
adult-fairy-tales
```

目录中禁止包含 Agent、Tool、Plan 和 Workflow。

## 13. API v0.1

### Challenge

```text
POST /challenges/validate
POST /challenges/install
GET  /challenges
GET  /challenges/{id}/versions
```

### Goal

```text
POST /goals
GET  /goals/{id}
POST /goals/{id}/activate
POST /goals/{id}/pause
POST /goals/{id}/resume
POST /goals/{id}/cancel
GET  /goals/{id}/timeline
```

### Work/Run

```text
GET  /goals/{id}/works
GET  /works/{id}
POST /works/{id}/retry
GET  /runs/{id}
```

### HumanTask

```text
GET  /human-tasks
POST /human-tasks/{id}/complete
POST /human-tasks/{id}/reject
```

### Artifact/Evaluation

```text
GET /goals/{id}/artifacts
GET /artifacts/{id}
GET /goals/{id}/evaluations
```

### Operations

```text
POST /operations/kill-switch
GET  /operations/health
GET  /operations/workers
```

## 14. ExecutionPermit 与风险

```text
ExecutionPermit
├─ actor_id
├─ action
├─ target_environment
├─ parameter_hash
├─ resource_limit
├─ data_scope
├─ network_scope
├─ valid_until
├─ usage_count
└─ approval_ref
```

V0.1 风险等级：

- R0：推理与草稿，自动；
- R1：公开信息读取、沙箱测试，自动并审计；
- R2：候选代码和预览环境，策略校验；
- R3：公开发布、生产配置和外部消息，人工审批；
- R4：支付、退款、密钥、隐私导出和不可逆迁移，强审批。

## 15. 第一纵向切片

第一阶段只实现一条完整链路：

```text
加载薄 Challenge
→ 创建 Goal
→ GoalInterpreter 生成结构化解释
→ 独立规则/人工接受解释
→ 创建一个 Work
→ 分配给 General AIActor
→ 产生 Artifact
→ Independent Evaluator 验收
→ 更新 Goal Stage
→ 中途杀死 Worker 并验证恢复
```

验收：

1. Challenge 不携带解法。
2. 全部状态持久化。
3. Worker 重启后 60 秒内恢复。
4. 重复事件不产生重复 Run。
5. Run、Artifact、Evaluation 和 Audit 可追溯。
6. 未授权 Tool 调用被拒绝。

## 16. 第二纵向切片

```text
Goal Interpretation
→ Capability Requirement
→ Inventory Match
→ Candidate Gap
→ CandidateAgentSpec
→ Capability Trial
→ PROVISIONAL Certification
→ Candidate Organization
→ Assignment
→ Work Execution
→ Organization Review
```

必须支持不创建新 Agent 和回退 General AIActor。

## 17. 测试策略

### 单元测试

- 状态转换；
- Schema；
- Capability Match；
- Spawn Policy；
- 权限和配额；
- 幂等和乐观锁。

### 集成测试

- Challenge→Goal→Work→Run→Evaluation；
- HumanTask 暂停恢复；
- Outbox 原子性；
- Lease 失效接管；
- Challenge 版本冻结；
- Goal Workspace 隔离。

### 故障注入

- 模型调用前后 Worker 崩溃；
- 数据库提交后通知失败；
- Tool 超时；
- Actor 失联；
- 人工无响应；
- 配额耗尽。

### 安全测试

- Prompt Injection；
- 跨 Goal 数据访问；
- Tool 越权；
- Secret 泄露；
- Evaluator 篡改；
- 预算升级尝试。

## 18. 编码顺序

### M0：工程骨架

- pyproject、配置、日志、数据库、迁移、CI；
- Docker Compose；
- ADR。

### M1：持久化内核

- ChallengeSnapshot、Goal、Work、Run；
- Outbox、Queue、Worker、Lease、Timer；
- 状态机和恢复测试。

### M2：AI 纵向闭环

- ModelProvider、AIActor；
- GoalInterpreter；
- Artifact；
- Evaluator；
- 第一纵向切片。

### M3：能力与组织

- Capability、AgentSpec、Organization、Assignment；
- Spawn Policy；
- 第二纵向切片。

### M4：治理与人类

- PolicyDecision、ExecutionPermit、Quota；
- HumanTask；
- Kill Switch、审计。

### M5：Challenge 验证

- 两个 Challenge 文件；
- KPI Adapter 接口；
- 单 Agent、固定组织、动态组织模式；
- 72小时长稳测试。

## 19. 开工门槛

以下项目完成即可开始 M0/M1 编码：

- Python/PostgreSQL 技术栈确认；
- ChallengeContract v0.1 确认；
- Goal/Work/Run 状态机确认；
- 第一纵向切片验收标准确认；
- 模型供应商和调用配额确认；
- 开发环境 Secret 管理方式确认。

以下不阻塞 M0/M1：支付账户、域名、最终 KPI 数值、完整 UI、Tool Builder 和生产发布。

## 20. Definition of Done

V0.1 技术完成意味着：

1. 两个薄 Challenge 均可加载且 Core 无场景业务对象；
2. Goal 可跨进程和 Worker 重启持续运行；
3. 系统能生成并验证 Goal Interpretation；
4. 系统能提出能力需求、识别候选缺口并配置候选 Agent；
5. 系统能形成、评审、调整和解散最小组织；
6. 所有工作以 Artifact 和 Evidence 交接；
7. 状态、权限、预算、评价和审计由确定性内核控制；
8. 系统可暂停、接管、回退并阻止无限自治；
9. 外部 KPI Adapter 能将真实结果写为 Observation；
10. 单 Agent、固定组织和动态组织可在同一接口下运行比较。
