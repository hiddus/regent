# Regent Vibe Coding 项目计划书

> 状态：唯一有效编码基线  
> 配套需求：[Regent-PRD.md](./Regent-PRD.md)  
> 测量框架：[Regent-Measurement-Decision-Framework.md](./Regent-Measurement-Decision-Framework.md)

## 1. 实现方式

采用模块化单体、PostgreSQL 事实源、持久化状态机、Outbox、数据库任务队列、Timer 和 Worker Lease。P0 不引入微服务、外部事件总线、复杂工作流引擎、图数据库或通用 Agent DSL。LLM 只能提出结构化 Command，状态转换由确定性 Application Service 执行。

P0 是一个整体交付。S0—S8 是降低实现风险的纵向切片，不是独立产品版本；所有 P0 完成条件通过后才能宣告交付。

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

- `DRAFT`：仅保存原始输入；
- `READY`：GoalSpec 有效，具备最低资源与授权；
- `ACTIVE`：允许创建和调度 Work；
- `PAUSED`：用户主动暂停；
- `WAITING_HUMAN`：阻塞性 HumanTask 尚未决议；
- `BLOCKED`：当前无可执行 Work，但仍存在恢复可能；
- `ACHIEVED / EXHAUSTED / FAILED / CANCELLED`：终态。

状态恢复规则：

| 当前状态 | 触发方式 | 下一状态 |
|---|---|---|
| `ACTIVE` | `POST /goals/{id}/pause` | `PAUSED` |
| `PAUSED` | `POST /goals/{id}/resume` | `ACTIVE` |
| `WAITING_HUMAN` | HumanTask 完成 | `ACTIVE` 或 `BLOCKED` |
| `WAITING_HUMAN` | HumanTask 超时 | 按冻结策略进入 `ACTIVE`、`BLOCKED` 或 `EXHAUSTED` |
| `BLOCKED` | 资源/环境/授权事件或 Replan Command | `ACTIVE` |

`resume` 只接受 `PAUSED`。只有 `ACTIVE` 可以创建新 Run。终态 Goal 不重新打开；资源或目标改变后创建新 Goal，并引用原 Goal。

`EXHAUSTED` 只能由 Exhaustion Evaluator 提议，并由确定性规则确认：不存在可执行或可解锁 Work、候选路径已评估、硬约束与资源上限已耗尽、证据完整。证据不足时进入 `BLOCKED`，不能进入 `EXHAUSTED`。

## 3. GoalSpec 版本规则

原始 Goal 永不覆盖。每次解释产生不可变 GoalSpec 版本，并分别保存显式约束、系统推断、未知项和来源。

以下变化创建新 GoalSpec 版本，并使尚未开始且依赖旧版本的 Work 进入 `BLOCKED` 等待重规划：显式约束、成功标准、授权范围、预算上限或关键输入版本变化。所有未领取 Permit 立即撤销；已领取 Permit 按第 7 节处理。Work 的目的或验收标准变化必须创建新 Work，不能原位改写历史 Work。

## 4. Work 状态机

```text
PLANNED → READY → RUNNING → EVALUATING → ACCEPTED
             ↘ WAITING_HUMAN          ↘ REJECTED → READY
             ↘ BLOCKED
RUNNING → UNKNOWN
任意非终态 → CANCELLED
```

- `ACCEPTED`：终态，成果通过独立验收；
- `CANCELLED`：终态，工作被取消；
- `REJECTED`：本次成果未通过，可在修订计划后创建新 Run；
- `UNKNOWN`：外部副作用无法确认，必须先对账；
- `WAITING_HUMAN` 与 `BLOCKED` 的恢复机制与 Goal 同义，但作用域仅限当前 Work。

后续 Observation 推翻已接受结果时，不重开 `ACCEPTED` Work；创建纠正 Work，并由 Replan Command 更新剩余计划。

## 5. Run 状态机

```text
CREATED → PERMIT_PENDING → QUEUED → RUNNING
RUNNING → EXECUTED | FAILED | UNKNOWN | CANCELLED
PERMIT_PENDING → DENIED | EXPIRED | CANCELLED
```

Run 所有终态均不可修改：

- `EXECUTED`：执行正常返回，不表示 Work 已验收；
- `FAILED`：本次尝试明确失败；
- `UNKNOWN`：外部结果无法确定；
- `DENIED / EXPIRED / CANCELLED`：未执行或被终止。

同一 Work 同时最多一个活动 Run。重试、换 Agent、换 Tool 或输入版本变化均创建新 Run。`UNKNOWN` 不得自动重试；Reconciler 必须使用外部回执、幂等键或权威查询产生对账 Evidence，随后创建纠正 Work 或新 Run。

## 6. 状态转换契约

每个状态转换必须在实现前登记以下字段：

```text
command
aggregate_type / aggregate_id / expected_version
allowed_from / target_state
preconditions
transaction_writes
audit_type
outbox_event
error_code
idempotency_key
recovery_behavior
```

同一事务必须完成：校验版本与状态、更新聚合、追加 Audit、写 Outbox。非法转换不得产生部分写入。并发冲突返回稳定错误码，调用方重新读取后决定，不在服务端静默覆盖。

最低错误码集合：`INVALID_STATE`、`VERSION_CONFLICT`、`ACTIVE_RUN_EXISTS`、`PERMIT_REQUIRED`、`PERMIT_INVALID`、`RECONCILIATION_REQUIRED`、`GOAL_TERMINAL`、`POLICY_DENIED`。

## 7. ExecutionPermit 生命周期与不变量

```text
REQUESTED → GRANTED → CLAIMED → CONSUMED
REQUESTED → DENIED
GRANTED → EXPIRED | REVOKED
CLAIMED → CONSUMED | REVOKED
```

1. 确定 Run 后由 Policy Engine 创建 `REQUESTED`；
2. 策略或人工批准后进入 `GRANTED`；
3. Worker 执行前以原子操作领取为 `CLAIMED`；
4. 动作成功、失败或未知均进入 `CONSUMED`，不得重用；
5. 只有尚未领取的 Permit 可因超过 `validUntil` 进入 `EXPIRED`；
6. 策略、Goal 或人工可以撤销 Permit；已领取 Permit 的撤销表达“禁止开始或要求尽力取消”，不能伪造外部动作已停止；
7. 已开始动作即使跨过 `validUntil` 也必须记录实际结果并 `CONSUMED`，不能改写为 `EXPIRED`；
8. Worker 在领取后崩溃，Run 进入 `UNKNOWN`；新 Worker 先对账，不得凭 Lease 到期直接重复副作用。

Permit 绑定 `goalId/workId/runId/actorId/action/target/parameterHash/dataScope/networkScope/resourceLimit/validUntil/nonce/idempotencyKey`。任何绑定内容变化均申请新 Permit。Permit 不保存明文凭证。

幂等键由 Application Service 在创建 Run 时生成，在同一外部副作用目标和业务操作作用域内唯一；Actor 不得自行替换。Secret Broker 仅向已验证的 Permit 代执行或下发短期能力，Agent 不读取明文长期凭证。

必须覆盖原子领取、重复领取、领取后崩溃、撤销竞态、跨过有效期、未知结果对账和重复外部请求测试。

## 8. API 与状态一致性

```text
POST /goals
GET  /goals/{id}
POST /goals/{id}/pause       # ACTIVE → PAUSED
POST /goals/{id}/resume      # PAUSED → ACTIVE
POST /goals/{id}/cancel      # 非终态 → CANCELLED
GET  /goals/{id}/timeline
GET  /human-tasks
POST /human-tasks/{id}/complete
POST /observations
```

HumanTask 完成和超时由内部 Command 触发，不调用 `/resume`。Blocked 恢复由资源事件或 Replan Command 触发。每类 HumanTask 必须在创建时冻结批准角色、期限、升级路径和超时默认策略；高风险副作用超时默认拒绝。

## 9. 固定能力缺口验收

```text
名称：EVT_PARSER_GAP
输入：timestamp|category|value|crc32，共 6 行，1 行 CRC32 错误
预置能力：无 EVT Parser
输出：valid_count=5, invalid_count=1
约束：断网；fixtures/ 只读；只写 output/；Builder 不可读隐藏测试
```

系统必须登记能力缺口，生成 `evt-summary` 候选 Tool，通过公开与隐藏样例后获得仅限当前 Goal 的认证，并由实际 Run 使用。构建或隐藏测试失败不得注册。

认证默认仅限当前 Goal。候选能力在两个相互独立的后续 Goal 中通过独立验收且没有安全违规后，才可晋级为跨 Goal `VERIFIED`；任何供应链、权限或结果完整性失败均可撤销认证。

## 10. 开发切片

1. `S0` 工程骨架：Core、空 Apps、数据库、迁移、CI 和一键启动；
2. `S1` 可靠内核：三套状态机、Outbox、Lease、Timer、Artifact、Evidence、Audit，通过 `CSV_SUMMARY_BASELINE`；
3. `S2` 单 Agent 闭环：Goal Interpreter、Planner、Executor、Evaluator 和预算停止；
4. `S3` 治理与人工流程：Permit、Policy、Secret Broker、HumanTask 及各自恢复规则；
5. `S4` 能力与组织：Requirement、Provider、Certification、Organization、Assignment、DecisionRecord；
6. `S5` 能力构建：沙箱、ToolSpec、构建、扫描、独立测试和认证，通过 `EVT_PARSER_GAP`；
7. `S6` 独立 App：Workspace、Build、Test 和 Artifact 通用端口；
8. `S7` 反馈重规划：预览部署、Observation、ExperienceRecord 和重规划；
9. `S8` 产品验证与长期目标：完成 A/B/C 首轮冻结实验并形成产品 DecisionRecord；随后通过相同 Goal API 启动两个独立产品。

S4 的默认路径必须是单 Agent。创建额外 Agent 前，Organization Designer 必须记录预期收益、协调成本、风险、可逆性和停止条件。没有正向预期净收益证据时不得增员。

## 11. 编码与阶段门禁

每个任务必须写明允许目录、输入输出契约、状态转换、自动验收、权限与副作用风险。先写失败测试，再做最小实现。每次合并必须通过格式、类型、单元、集成、状态转换、幂等、迁移、权限拒绝和至少一个恢复测试。

阶段门禁：

- S1 结束：冻结状态转换表、错误码和崩溃恢复证据；
- S3 结束：Permit 并发、撤销、过期、未知结果与 Secret 隔离测试全部通过；
- S4 结束：冻结实验任务集、真值标签、模型版本、预算和净收益公式；
- S5 结束：能力认证与撤销链路通过公开及隐藏测试；
- S7 结束：外部指标数据源、口径版本、防作弊和重规划归因可审计；
- S8 结束：发布签名的实验报告和唯一产品 DecisionRecord。

禁止一次生成整个 Core、无测试的大规模重构，以及向 Core 加入具体 App 业务概念。

## P1 编码基线

P1 整体交付，按依赖关系分批编码但不拆分验收：

1. 批次一：领域状态机、发现/假设/需求/生成协议、数据库迁移、外部端口和长任务契约。
2. 批次二：发现编排、证据连接器、假设决策和需求修订服务。
3. 批次三：能力解析、WorkspaceWriter、依赖解析和离线可复现构建。
4. 批次四：预览发布、观测回流、CONTINUE/REVISE/STOP 决策及端到端验收。

每批必须通过格式、静态类型、单元测试、迁移检查和协议兼容性测试。任何真实网络访问、构建或发布只允许通过端口适配器进入，并受 Permit、幂等键和审计约束。
### 当前实现进度（2026-07-18）

- 批次一已完成：基础状态机、0011、通用 Schema、外部端口和长任务契约骨架。
- 批次二已启动：Goal 资格判断、证据源编排、结构化产品假设、冻结决策策略校验、需求修订提案和证据继承校验已落地。
- 下一门禁：接入持久化事务与 202 接口，然后进入 0012 能力解析。
### 批次三进度（2026-07-18）

- DiscoveryRound 请求与查询已持久化，支持 Goal 资格校验、输入快照哈希、轮次递增和幂等键作用域校验。
- Discovery API 已注册：创建轮次返回 202，并提供轮次、候选和决策查询。
- `0012` 已落地需求修订、能力解析计划和解析条目表，迁移链保持单一 Head。
- 能力解析固定采用 REUSE、CONFIGURE、COMPOSE、BUILD、REQUEST_HUMAN、BLOCK 顺序，并复用 P0 Capability 与 ToolSpec。
- 后续继续实现 Discovery Worker 写入事务、需求修订持久化命令和 WorkspaceWriter。
### 批次四进度（2026-07-18）

- Discovery Worker 已实现两阶段事务：原子进入 RESEARCHING，事务外执行证据与模型调用，随后原子写入候选、证据引用和唯一决策；异常收敛到 FAILED。
- RequirementRevision 持久化服务只接受自动决策选中的 Hypothesis，并生成不可变 revision、predecessor 和规范内容哈希。
- WorkspaceWriter 已成为生成文件唯一落盘原语，支持基础快照、CREATE/REPLACE/DELETE、previous hash、防逃逸、防链接、配额、确定性 manifest/source archive、fsync、原子提交和幂等重放。
- 下一步进入 `0013` GenerationPlan、GenerationRun、FileChangeSet、WorkspaceSnapshot 的持久化与生成编排。
### 批次五进度（2026-07-18）

- `0013` 已增加 GenerationPlan、GenerationRun、FileChangeSet 和 WorkspaceSnapshot，所有生成输入与输出均以不可变哈希绑定。
- GenerationService 支持冻结计划幂等创建、运行请求幂等、固定路径校验、WorkspaceWriter 提交、模型用量记录、完成与失败收敛。
- 生成服务不直接依赖通用大模型，而依赖 FileChangeSetGenerator 能力端口；适配器必须先物化完整文件内容，再返回可信 Artifact URI 和哈希。
- 下一步实现代码生成适配器、Generation API/Worker，并进入 `0014` 依赖解析和隔离构建。
### 批次六进度（2026-07-18）

- ArtifactBackedCodeGenerator 已把模型完整源码输出物化为不可变 Artifact，再生成可信 FileChangeSet；ArtifactUriResolver 强制 URI 位于 Artifact Root。
- Generation API 已提供计划创建、运行请求（202）和运行查询；同步 HTTP 不执行模型或构建任务。
- `0014` 已增加 DependencyResolution、AppBuild 和 VerificationReport。
- BuildService 严格拆分受 Permit 的依赖物化与断网 Sandbox VerifyBuild；外部异常进入 UNKNOWN 并要求对账，不自动重复副作用。
- 下一步实现真实依赖物化与 Sandbox Adapter、Build API/Worker，然后进入 `0015` Preview Release。
### 批次七进度（2026-07-18）

- DockerSandboxDriver 已落实断网、非 root、只读根文件系统、cap-drop、no-new-privileges、进程/内存/CPU 配额和只读输入挂载。
- DockerDependencyMaterializer 仅在受控 Egress Proxy 与有效 Permit 同时存在时联网；缺少代理时 fail closed。
- Provider result 中所有输出路径均被约束在独立 output root，阻止路径逃逸。
- Build API 已提供 DependencyResolution 请求、AppBuild 请求（均返回 202）和 Build 查询。
- 下一步补齐构建对账 Worker 和可信 resolver/sandbox 镜像内容，然后进入 `0015` ReleaseCandidate 与 Preview Deployment。
### 批次八进度（2026-07-18）

- 已提供 python-web-v1 resolver 与 sandbox 可信镜像入口：resolver 仅下载冻结 wheel 并验证 hash，生成 lockfile、Bundle 与 CycloneDX SBOM；sandbox 断网安装本地 wheel 并执行编译和测试。
- Build UNKNOWN 已支持 query 对账并收敛为 PASSED/FAILED，保留 VerificationReport。
- 输出目录权限只授予每次任务独立目录，所有容器输出继续执行 root-bound path 校验。
- 服务器可先验证 0014 迁移、API 与 Artifact 生成；当前容器化 Worker 无权启动 Docker Sandbox，禁止通过挂载宿主 Docker Socket 绕过，需独立 Build Provider。
### 批次九进度（2026-07-18）

- `0016` 已增加不可变指标定义绑定、闸门评估和迭代决策记录，迁移链保持单一 Head。
- 反馈闭环根据冻结指标口径聚合 Observation，排除机器人和内部流量，并输出 `PASSED`、`FAILED` 或 `INSUFFICIENT_EVIDENCE`。
- 决策服务确定性输出 `CONTINUE`、`REVISE` 或 `STOP`；`REVISE` 必须绑定主要假设与同一 Goal 的新 Work。
- 已开放指标绑定、闸门评估/查询、迭代决策/查询 API，并通过本地全量测试及生产服务器迁移、健康和路由验证。
- P1 整体验收尚需补齐真实 App 的端到端生成、构建、预览发布、真实观测回流与唯一产品决策记录，不拆分 P1 验收。
### 对话工作区与受监管自我改进（2026-07-18）

- `0017` 增加持久化 Conversation 与 ConversationMessage，消息按会话内序号形成可查询时间线，并可绑定唯一 Goal。
- Regent Console 改为对话式主界面，侧栏保留长期任务历史；用户消息、Core 回复、执行进展与结构化引用统一显示。
- 新增受监管自我改进入口：Core 可检查自身缺口并提出、实现和验证改进，但禁止直接修改生产环境或降低治理要求。
- 对话层不替代 Goal、Work、Run、Evidence、Artifact 与 DecisionRecord，只作为它们的统一交互与历史视图。
### 0018 App 身份与确认闸门

- 增加极简 AppProject，作为长期 App 身份；Goal 可选归属于 AppProject，一个 App 可拥有多个执行周期。
- App 主对话直接绑定 AppProject；现有 Goal 和 Conversation 兼容保留，避免破坏历史。
- GoalSpec 增加 DRAFT/FROZEN/SUPERSEDED、内容哈希和确认记录；确认操作原子写入 Goal、AppProject、Audit 和 Outbox。
- 规划、组织和执行均拒绝未确认 Goal；界面用“确认并开始”表达治理闸门，不暴露内部状态术语。
- Regent Console 增加“新建 App”、App 列表和产品理解确认卡，首条消息不再直接执行。
### 0019 对话驱动修订

- 增加不可变 ConversationCommand，记录后续消息的 QUERY/MODIFY/CONTINUE 解释、模型、哈希、状态和产生的新 Goal。
- MODIFY 在同一 AppProject 下创建新的 DRAFT Goal 与 GoalSpec，不覆盖上一轮目标；用户重新“确认并开始”后才允许规划。
- QUERY 返回当前 Goal 和 Work 状态摘要；CONTINUE 在不可继续状态下明确拒绝静默重启。
- Console 后续消息改由 Core 解释，状态使用短轮询恢复，不提前引入 SSE。
### 0020 真实 App 预览闭环

- Core 根据已确认 Goal 生成完整静态 Web App，固定为 index.html/styles.css/app.js，不依赖外部资源。
- StaticAppPublisher 在隔离工作区执行路径、文件集、体积、语义主区、观测钩子和离线编译前验证；发布内容按哈希不可变。
- Preview 通过严格 CSP 从 Core 提供可访问地址；前端不能再伪造 ASSISTANT/EVENT 消息。
- Core 注入同源 activation 观测钩子，服务端签名写入 Observation；预览闸门复用 GateEvaluation 与 IterationDecision。

### 0021 受监管自我改进

- SelfImprovementRun 冻结主要问题、单一假设、目标文件、基线哈希、候选哈希、验证证据、风险和人工决定。
- 候选只在隔离副本中物化，禁止修改 Permit、Secret、状态机、数据模型、迁移和自我改进评价器。
- 候选执行 AST 与隔离 compileall，并由固定外部提示进行独立审查；候选代码不能修改裁判。
- 人工批准不自动应用或发布，只允许进入另行授权的实现步骤；生产保持不变。
### 0022 确认后自主执行闭环

- 初次 App 确认消息冻结 GoalSpec ID、版本、状态和内容哈希，页面刷新后仍可确认。
- Confirm 与 Start 语义分离；Console 的“确认并开始”依次提交两条可审计命令。
- Start 原子地将 Goal 置为 ACTIVE 并写入 GoalExecutionRequested；Worker 独立完成规划提示、生成、检查和 Preview 发布。
- Goal metadata 与 App 对话持久保存 QUEUED、PLANNING、GENERATING、PREVIEW_READY 或 FAILED 阶段。
- CONTINUE 在 READY 时启动、FAILED 时重试、运行中只返回真实状态，不再伪装执行。
- Outbox 增加指数退避、最大尝试和 DEAD_LETTER；健康检查公开失败与死信计数。
