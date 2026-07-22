# Regent Vibe Coding 项目计划书 V1

> 状态：可开工  
> 日期：2026-07-16  
> 配套需求：[Regent-PRD-v1.md](./Regent-PRD-v1.md)

## 1. 开发原则

1. 每个阶段必须形成可运行纵向切片，不按“先写完所有领域模型”推进；
2. 先确定性运行内核，后接入概率性 Agent；
3. 先单 Agent 跑通闭环，再让组织机制按收益增减成员；
4. Core 只认识通用对象，任何 App 业务对象不得进入 Core；
5. AI 生成的是候选代码和候选决策，必须经过独立门禁；
6. 不为未来规模提前引入微服务、Kafka、Kubernetes、通用本体或复杂工作流引擎。

## 2. 项目结构

```text
regent/
├─ core/
│  ├─ pyproject.toml
│  ├─ src/regent/
│  │  ├─ api/
│  │  ├─ domain/
│  │  ├─ application/
│  │  ├─ runtime/
│  │  ├─ infrastructure/
│  │  └─ observability/
│  ├─ migrations/
│  ├─ tests/
│  └─ docs/adr/
├─ apps/
│  └─ .gitkeep
├─ tests/
│  └─ e2e/
├─ compose.yaml
├─ Makefile
└─ README.md
```

`apps/<app-id>` 由 Regent 创建，每个 App 是独立项目边界；允许将来拆成独立仓库。根目录只用于共同开发和本地集成，不形成源码依赖。

## 3. 初期技术选型

- Python 3.12；
- FastAPI + Pydantic v2；
- SQLAlchemy 2 + Alembic；
- PostgreSQL；
- PostgreSQL Outbox、Queue、Timer 和 Lease；
- 本地文件制品库，接口兼容后续 S3；
- Docker/容器作为执行沙箱起点；
- pytest、Ruff、mypy；
- OpenTelemetry 结构化 Trace；
- 模型访问统一走 `ModelProvider`，不绑定单一厂商。

P0 不使用 Kafka、Temporal、Kubernetes、向量数据库和独立图数据库。需要时再通过接口替换。

## 4. 最小领域模型

```text
Goal / GoalSpec
Work / Run
Artifact / Evidence / Observation / Evaluation
CapabilityRequirement / CapabilityProvider / CapabilityAsset
Organization / Assignment / CoordinationContract
HumanTask
PolicyDecision / ExecutionPermit
ResourceEnvelope / ResourceUsage
ExperienceRecord
AuditRecord
```

不要创建 `Challenge`、`Scenario`、`News`、`Story`、`Subscription` 或 `DAU` Core 对象。

## 5. 状态与事件

状态机是事实源，事件用于可靠触发后续工作：

```text
事务：更新状态 + 写入 Outbox
提交后：Dispatcher 投递事件
Worker：领取 Work Lease → 执行 → 幂等提交结果
```

P0 不需要外部事件总线。PostgreSQL Outbox 就能跑通事件驱动和故障恢复，未来再替换传输层。

Goal 状态：

```text
DRAFT → ACTIVE ↔ WAITING → SUCCEEDED
                 ↘ BLOCKED / FAILED / CANCELLED
```

Work 状态：

```text
PLANNED → READY → RUNNING → SUCCEEDED
                    ↘ WAITING / FAILED / UNKNOWN
```

所有转移由确定性 Application Service 执行，LLM 只能提出 Command，不能直接写状态。

## 6. 核心接口

```text
POST   /goals
GET    /goals/{id}
POST   /goals/{id}/pause
POST   /goals/{id}/resume
POST   /goals/{id}/cancel
GET    /goals/{id}/timeline
GET    /goals/{id}/organization
GET    /human-tasks
POST   /human-tasks/{id}/complete
POST   /observations
```

内部端口：

```text
ModelProvider
WorkspaceService
SandboxService
BuildService
TestService
ArtifactStore
DeploymentService
ObservationProvider
SecretBroker
PolicyEngine
```

## 7. 决策责任边界

- Goal Interpreter：把自然语言转为候选 GoalSpec；
- Planner：产生候选 Work Graph；
- Organization Designer：选择能力主体、职责和授权；
- Decision Service：比较方案、冲突和证据，选定候选；
- Policy Engine：判断是否允许；
- Scheduler：只调度已批准 Work 的优先级、并发、资源和租约；
- Evaluator：独立判断 Artifact 是否满足标准；
- Experience Store：提供历史先验，不直接做决定。

Organization 不排在 Planning 的固定前后。流程为：能力盘点约束初始计划，计划暴露缺口，组织调整后重新规划，直到形成可执行组合。

## 8. 冲突裁决

子目标或 Agent 方案冲突按以下顺序处理：

1. 根 Goal 与用户明确成功条件；
2. 硬约束、权限和治理策略；
3. 是否存在逻辑或资源不可共存；
4. 外部证据质量和置信度；
5. 全局预期收益、成本和风险；
6. 可逆方案优先；
7. 仍无法裁决则创建 HumanTask。

裁决生成版本化 DecisionRecord，包含候选、证据、被拒原因和影响范围。

## 9. 安全最小集

- 默认无权限，Tool 调用需要短期 ExecutionPermit；
- Builder、Runner、Evaluator、Releaser 逻辑分离；
- 沙箱默认无生产凭证、限制网络、时间、CPU、内存和目录；
- Secret 不进入 Prompt、日志和制品；
- 执行 Agent 不可修改策略、验收标准和受保护测试；
- 对外发布、支付、群发、不可逆迁移和权限提升进入 HumanTask；
- 全局 Kill Switch 和 Goal 级暂停；
- 外部副作用使用幂等键，未知结果进入 UNKNOWN 状态而非盲重试。

## 10. 开发阶段

### S0：骨架与契约（1—2 天）

交付：目录、依赖、配置、数据库迁移、CI、ADR 模板、Health API。

验收：本地一条命令启动 API、Worker 和 PostgreSQL；空 `apps` 不影响 Core。

### S1：可靠运行内核（3—5 天）

实现 Goal、GoalSpec、Work、Run、Outbox、Queue、Lease、Timer、Artifact 和 Audit。

验收：提交固定 Goal 后完成三步 Work；强制杀死 Worker 后恢复；同一副作用不重复。

### S2：自然语言 Goal 闭环（3—5 天）

实现 ModelProvider、Goal Interpreter、单 Agent Planner/Executor、独立 Evaluator、预算与无进展停止。

验收：只输入自然语言即可产生 GoalSpec、计划、制品和评价；所有推断与用户约束分开保存。

### S3：人工异步流程与治理（2—4 天）

实现 HumanTask、Policy Engine、ExecutionPermit、风险等级、暂停与恢复。

验收：流程等待人工 24 小时也不占 Worker；完成或过期后按策略恢复；越权工具调用被拒并审计。

### S4：能力与最小组织（4—7 天）

实现 Capability Requirement、Inventory、Gap 分类、Candidate Agent、Organization、Assignment、Coordination Contract 和组织 Review。

验收：系统默认单 Agent；仅在并行、隔离或专业化预期有净收益时增员；失败后能替换或收缩；冲突生成 DecisionRecord。

### S5：能力构建（4—7 天）

实现 ToolSpec、候选代码生成、沙箱构建、静态检查、测试、独立评价、签名 Artifact 和限定作用域注册。

验收：在无预置 Tool 的虚拟任务中发现真实 Gap，生成一个小型 Tool，通过独立测试后被当前 Goal 使用；失败 Tool 不进入能力池。

### S6：独立 App 生成（4—7 天）

实现 Workspace、Build、Test、Artifact 通用适配器，以及 `apps/<id>` 项目创建。

验收：从普通 Goal 创建一个独立可启动 App；删除 App 不影响 Core；App 不导入 Core 内部模块；切换第二类 Goal 不修改 Core 领域代码。

### S7：预览部署与观测（4—7 天）

实现预览环境部署、Observation 接入、发布审批、回滚和外部 KPI 证据引用。

验收：候选 App 可部署到预览环境，产生观测，触发一次有证据的重规划；生产发布仍需人工许可。

### S8：两个长期 Goal 启动（持续）

分别提交 AI 从业者产品和成人童话网站 Goal。Regent 自主提出阶段目标、产品候选、组织和能力需求。

验收：两个 App 独立构建和部署；Core 无场景业务表或条件分支；运营指标由外部来源进入 Observation。

## 11. 每个编码任务的 Vibe Coding 模板

每次只给编码 Agent 一个可验收小任务：

```text
目标：要形成的用户或系统行为
边界：允许修改的目录和禁止触碰的对象
已有契约：输入、输出、状态和错误语义
验收：可自动执行的测试
风险：权限、副作用、迁移和兼容性
完成：代码 + 测试 + 迁移/文档 + 运行证据
```

工作循环：

```text
读取 PRD/ADR
→ 写失败的验收测试
→ 最小实现
→ 格式/类型/单元/集成测试
→ 独立审查
→ 小步提交
→ 更新决策和下一任务
```

禁止一次提示生成整个 Core；禁止在无测试时让 Agent 大规模重构；一个提交只解决一个可解释问题。

## 12. 首批 Backlog

1. 初始化 `core` 与空 `apps`；
2. 配置、日志、数据库与迁移；
3. Goal/GoalSpec 表和 API；
4. Work/Run 状态机；
5. Outbox Dispatcher；
6. Worker Lease 与崩溃恢复；
7. Artifact/Evidence/Audit；
8. ModelProvider fake 与真实适配器；
9. Goal Interpreter；
10. 单 Agent Planner/Executor；
11. Evaluator 与预算停止；
12. HumanTask；
13. Policy/Permit；
14. Capability Inventory/Gap；
15. Organization/Assignment/Conflict Decision；
16. Sandbox 与 Tool Builder；
17. Workspace/Build/Test；
18. 独立 App 创建；
19. Preview Deploy；
20. Observation 与重规划。

## 13. 质量门禁

每次合并必须通过：

- Ruff、mypy、单元测试；
- 状态转移和幂等测试；
- 数据库迁移前后测试；
- 权限拒绝测试；
- 至少一个崩溃恢复或异常路径测试；
- Core 不得出现 App 业务术语的架构检查。

S1、S4、S6、S7 完成后分别进行一次端到端演示并冻结基线。

## 14. 开工判定

以下事项确定后即可进入 S0：

- Python 3.12 与 PostgreSQL 技术栈；
- 首个模型供应商和测试额度；
- 本地容器执行可用；
- 代码许可协议；
- 人工审批联系人；
- 可写工作区与禁止访问路径；
- P0 不开放生产发布权限。

其他产品细节由 Regent 在目标推进中探索，不作为 Core 开工阻塞项。
