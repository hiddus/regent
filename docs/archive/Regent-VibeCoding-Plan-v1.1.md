# Regent Vibe Coding 项目计划书 V1.1

> 状态：可开工；替代 V1  
> 配套需求：[Regent-PRD-v1.1.md](./Regent-PRD-v1.1.md)

## 1. 实现方式

采用模块化单体、PostgreSQL 事实源、持久化状态机、Outbox、数据库任务队列、Timer 和 Worker Lease。P0 不引入微服务、Kafka、Temporal、Kubernetes、图数据库或通用 Agent DSL。模型只能提出结构化 Command，所有状态转换由确定性 Application Service 执行。

## 2. Goal 状态机

```text
DRAFT → READY → ACTIVE ↔ PAUSED
                   ↕
             WAITING_HUMAN
                   ↕
                BLOCKED

ACTIVE / WAITING_HUMAN / BLOCKED
→ ACHIEVED | EXHAUSTED | FAILED | CANCELLED
```

- `DRAFT`：保存原始输入，尚无有效 GoalSpec；
- `READY`：GoalSpec 有效，具备最低资源和授权；
- `ACTIVE`：允许创建与调度 Work；
- `PAUSED`：人为暂停，不创建新 Run；
- `WAITING_HUMAN`：存在阻塞性 HumanTask；
- `BLOCKED`：当前无可执行 Work，但仍可能恢复；
- `ACHIEVED`、`EXHAUSTED`、`FAILED`、`CANCELLED`：终态，语义以 PRD 为准。

只有 `ACTIVE` 可创建新 Run。任意非终态可被取消。每条转换必须定义 Command、前置条件、事务写入、Outbox Event 和非法转换错误码。

## 3. Work/Run 聚合与状态机

Work 是逻辑工作，Run 是一次执行尝试：

```text
Goal 1 ──* Work 1 ──* Run
```

同一 Work 最多一个活动 Run；重试必须新建 Run。Run 记录实际 Actor、AgentSpec、模型、Tool、输入版本、Permit、资源用量和结果。Run 历史不可修改。

Work：

```text
PLANNED → READY → RUNNING → EVALUATING → SUCCEEDED
             ↘ WAITING_HUMAN          ↘ REJECTED → READY
             ↘ BLOCKED
RUNNING → UNKNOWN
任意非终态 → CANCELLED
```

Work 终态为 `SUCCEEDED`、`CANCELLED`。`REJECTED` 可重试。`UNKNOWN` 必须先对账。

Run：

```text
CREATED → PERMIT_PENDING → QUEUED → RUNNING
RUNNING → SUCCEEDED | FAILED | UNKNOWN | CANCELLED
PERMIT_PENDING → DENIED | EXPIRED | CANCELLED
```

Run 终态为 `SUCCEEDED`、`FAILED`、`UNKNOWN`、`DENIED`、`EXPIRED`、`CANCELLED`。Run 的 `SUCCEEDED` 仅表示执行完成；Evaluator 接受其 Evidence 后 Work 才进入 `SUCCEEDED`。

## 4. ExecutionPermit 生命周期

```text
REQUESTED → GRANTED → CLAIMED → CONSUMED
REQUESTED → DENIED
GRANTED → EXPIRED | REVOKED
CLAIMED → CONSUMED | EXPIRED | REVOKED
```

1. 确定 Run 后由 Policy Engine 创建 `REQUESTED`；
2. 策略或人工批准后进入 `GRANTED`；
3. Worker 执行前原子领取为 `CLAIMED`；
4. 动作成功、失败或未知均进入 `CONSUMED`；
5. 超过 `validUntil` 进入 `EXPIRED`；
6. 策略、Goal 或人工撤销进入 `REVOKED`。

`DENIED`、`CONSUMED`、`EXPIRED`、`REVOKED` 为终态。Permit 绑定 `goalId/workId/runId/actorId/action/target/parameterHash/dataScope/networkScope/resourceLimit/validUntil/nonce/idempotencyKey`。绑定内容变化必须申请新 Permit。Secret Broker 代执行有凭证动作，Agent 不读取明文凭证。

## 5. 事件与事务

```text
同一数据库事务：校验状态 → 更新聚合 → 追加 Audit → 写 Outbox
提交后：Dispatcher 投递 → Worker 领取 Lease → 幂等执行 → 提交结果
```

P0 的 PostgreSQL Outbox 即事件传输机制，不要求外部总线。事件不是事实源，状态表和追加审计才是事实源。

## 6. 固定验收夹具

### 6.1 CSV_SUMMARY_BASELINE

使用 PRD 定义的四行 CSV 和精确 JSON 输出。测试覆盖 GoalSpec、Work/Run 关系、哈希 Evidence、Worker 崩溃恢复、幂等重放、越界写入拒绝和 `ACHIEVED` 终态。

### 6.2 EVT_PARSER_GAP

```text
输入格式：timestamp|category|value|crc32
公开样例：6 行，其中 1 行 CRC32 错误
预置能力：明确不提供 EVT Parser
目标输出：valid_count=5, invalid_count=1
约束：断网；只读 fixtures/；只写 output/；隐藏测试不可被 Builder 读取
```

系统必须登记解析能力缺口，生成 `evt-summary` 候选 Tool，通过公开和隐藏样例后获得仅限当前 Goal 的认证，并由实际 Run 使用。构建或隐藏测试失败时不得注册。

## 7. 开发切片

### S0 工程骨架（1—2 天）

创建 `core/`、空 `apps/`、配置、迁移、CI、API/Worker/PostgreSQL 一键启动。验收：没有 App 时 Core 正常启动。

### S1 可靠内核（3—5 天）

实现 Goal、Work、Run 三套状态机，Outbox、Lease、Timer、Artifact、Evidence 和 Audit。验收：完整通过 `CSV_SUMMARY_BASELINE`。

### S2 自然语言单 Agent 闭环（3—5 天）

实现 ModelProvider、Goal Interpreter、Planner、Executor、Evaluator 和预算停止。验收：自然语言输入到证据化结果全链路运行，约束与推断分离。

### S3 治理与人工流程（2—4 天）

实现 Permit 生命周期、Policy Engine、Secret Broker 接口和 HumanTask。验收：领取互斥、单次消费、过期/撤销、高风险等待及恢复均有自动测试。

### S4 能力与最小组织（4—7 天）

实现 Capability Requirement、Provider、Certification、Organization、Assignment 和 DecisionRecord。验收：默认单 Agent；增员有净收益依据；能替换、收缩和裁决冲突。

### S5 能力构建（4—7 天）

实现 ToolSpec、断网沙箱、构建、扫描、独立测试、签名和限定认证。验收：完整通过 `EVT_PARSER_GAP`。

### S6 独立 App（4—7 天）

实现 Workspace、Build、Test 和 Artifact 通用端口，在 `apps/<id>` 创建独立项目。验收：App 可独立启动；删除 App 不影响 Core；第二类 Goal 不要求修改 Core。

### S7 预览部署与反馈（4—7 天）

实现预览部署、Observation、周期评价、ExperienceRecord 和重规划。验收：外部指标变化触发可追溯调整；生产发布仍需人工 Permit。

### S8 两个长期 Goal（持续）

通过相同 Goal API 分别启动两个产品。验收：独立构建部署，Core 无场景条件分支，指标由外部 Evidence 进入。

## 8. 编码任务模板与门禁

每个任务必须写明目标、允许目录、输入输出契约、状态转换、自动验收、权限/副作用风险和完成证据。先写失败测试，再做最小实现。每次合并必须通过格式、类型、单元、集成、状态转换、幂等、迁移、权限拒绝与至少一个恢复测试。

禁止一次生成整个 Core，禁止无测试的大规模重构，禁止将具体 App 业务术语加入 Core。
