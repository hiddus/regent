# Regent Vibe Coding 项目计划书

> 更新：2026-07-31（对话式完整交付统一计划 CD-0…CD-5）

> 状态：唯一有效编码基线  
> 配套需求：[Regent-PRD.md](./Regent-PRD.md)  
> 技术规范：[Regent-Technical-Spec.md](./Regent-Technical-Spec.md)  
> 测量框架：[Regent-Measurement-Decision-Framework.md](./Regent-Measurement-Decision-Framework.md)  
> 对话式交付计划：[docs/conversational-delivery-plan-2026-07-31.md](./docs/conversational-delivery-plan-2026-07-31.md)

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
## 12. Multi-Agent 能力补足计划（2026-07-31）

### 12.1 现状与原则

现有代码已具备固定 Hive、AgentTask、组织版本、协调 Token 计数、Eval 基础、MCP 注册、会话 todo 与自动压缩；本计划在其上补强，不重复引入新的编排内核。单 Agent champion 保持默认，自适应自由拓扑继续 `ROLLOUT_NOT_ALLOWED`，直到 P2-4 统计 Gate 证明正净收益。

| 能力 | 当前基础 | 主要缺口 | 目标落点 |
|---|---|---|---|
| 组织选择 | `OrganizationSpace` / `UtilityFunction` | 缺任务特征先验和可解释裁剪 | 路由特征、冻结规则、排除理由 |
| 协作评测 | 协调 Token、Eval/实验平台基础 | 缺份额、错误放大、调度熵 | P2-4 三项冻结指标 |
| 失败归因 | 通用 `failure_code` | 缺稳定协作失败词表 | MAST 命名空间与轨迹证据 |
| 固定 Hive | `pm-dev-independent-qa-v1`、durable AgentTask | ~~成员契约与整体再认证不足~~（MA-2 已闭合） | 三要素契约、模板整体回归 |
| 长任务 | todo、micro/auto compact | todo 不耐久；完整原文与大结果未统一 Artifact 化 | ExecutionPlanItem、Transcript/ToolResult Artifact |
| 调度审计 | `SchedulingDecision` | 组织内逐步派工理由不完整 | `DispatchDecision` 可回放记录 |
| 协议兼容 | MCP 注册；AgentEnvelope 设计 | 边界映射未冻结 | MCP 工具适配、A2A 只做外部投影 |

### 12.2 交付批次

| 批次 | 依赖 | 交付物 | 完成门禁 |
|---|---|---|---|
| MA-0 合同冻结 | 无 | 指标公式、MAST 词表、成员三要素 Schema、现状基线报告 | PRD/Tech/Plan 一致；合同测试先失败 |
| MA-1 可观测与归因 | MA-0 | 协调 Token 分类、三指标计算器、过程 span、MAST 归因 | 缺数据为 `INSUFFICIENT_EVIDENCE`；指标可由原始轨迹复算；过程 span 验收要求对齐 OTel GenAI semantic conventions（未接供应商栈前为后续对齐项，不得仅用内部私有 span 名义宣称完成） |
| MA-2 固定模板加固 | MA-0 | 成员契约、强制澄清、整体认证摘要与回归套件 | 改任一成员/模型/Prompt/工具后旧认证失效 |
| MA-3 长任务耐久化 | MA-0 | `ExecutionPlanItem`、大结果卸载、Transcript Artifact、结构化 rehydration | Worker 重启与两次压缩后计划/约束/证据无丢失 |
| MA-4 路由与过程评估 | MA-1、MA-2 | TaskFeatures、裁剪器、DispatchDecision、熵趋势告警 | 强顺序任务不扩编；每次派工可解释与重放 |
| MA-5 P2-4 冻结实验 | MA-1…MA-4 | 强单 Agent vs 固定 Hive A/B/C 报告与 DecisionRecord | 同预算、盲评、置信区间、护栏全量报告 |
| MA-6 条件激活 | MA-5 正净收益 | P2-5 自适应组织候选；A2A 边界适配探索 | 未获正净收益则不开发/不启用自适应拓扑 |

### 12.3 建议实施顺序与工作包

1. **近期（MA-0，1 个短迭代）**：冻结 Schema、公式和错误码；补齐 `pm-dev-independent-qa-v1` 每角色边界、allowlist、停止/澄清条件。
2. **近期（MA-1/MA-2，2 个迭代）**：先让现有固定 Hive 可测、可归因、可整体再认证；不改默认 rollout。
3. **并行可靠性线（MA-3，2 个迭代）**：将现有 todo/compact 从会话能力升级为耐久执行合同，优先服务 P1 长生成链。仅依赖 MA-0，资源允许时可与 MA-1/MA-2 并行；与线性编号不矛盾。
4. **P2-4 前置（MA-4，1–2 个迭代）**：记录每步 DispatchDecision，并用任务特征裁剪无收益拓扑。
5. **决策轮（MA-5，1 个冻结实验窗口）**：运行预注册 A/B/C；只接受可复算证据包。
6. **条件阶段（MA-6）**：仅在 DecisionRecord 为 GO 时开发 P2-5；否则保留固定模板并优化单 Agent champion。

### 12.4 工作包验收

- `WP-METRICS`：三指标均有版本化公式、原始分子/分母、边界值和故障注入测试；Agent 步骤过程 span 须可映射到 OTel GenAI semantic conventions（验收要求；供应商中立导出为后续对齐，本轮不强制接入完整 OpenTelemetry 栈）；
- `WP-FAILURE`：九类首批 MAST 失败均有正例、反例、低置信度保留原码测试；
- `WP-TEMPLATE`：成员变化导致内容哈希变化、旧认证拒绝、整套回归重跑；
- `WP-CONTEXT`：20k Token 以上结果卸载后可按哈希回查；压缩前完整轨迹可检索；
- `WP-PLAN`：Worker 中断后从持久计划续跑，不重复已完成副作用；
- `WP-DISPATCH`：每一步能查询候选、选择、理由、证据、权限范围和输出摘要；
- `WP-EVAL`：单 Agent 与固定 Hive 在冻结模型/工具/预算下重复运行并产生 95% 置信区间。

### 12.5 明确不做

- 不引入 CrewAI/LangGraph 等框架替换自研 Kernel；
- 不把 A2A 不透明协作语义用于内部 Agent；
- 不用更多 Agent 数、消息量或更长上下文作为成功指标；
- 不在 P2-4 DecisionRecord 前开放自适应自由拓扑；
- 不以 Prompt 调整替代状态机、角色契约、独立验证和恢复机制。

### 12.6 实现状态（2026-07-31）

| 批次 | 状态 | 说明 |
|---|---|---|
| MA-0 合同冻结 | ✅ 已完成 | `multiagent_metrics` / `mast_failure` / `member_contract` Schema + 合同测试 |
| MA-1 可观测与归因 | ✅ 已完成（OTel GenAI 后续对齐） | Token 分类、三指标复算、MAST 分类器（低置信保留原码）；OTel GenAI conventions 已写入验收，供应商栈未接 |
| MA-2 固定模板加固 | ✅ 已完成并强制接线 | 成员三要素、五类摘要、迁移 `0040` 回填认证；候选加载时摘要不一致即 fail closed |
| MA-3 长任务耐久化 | ✅ 已完成并接入生成主链 | `execution_plan_items`、大工具结果卸载、压缩前 Transcript Artifact、结构化 rehydration；Artifact 读取按 Goal 隔离 |
| MA-4 路由与过程评估 | ✅ 固定 Hive 主链完成 | TaskFeatures 保守裁剪接入 OrganizationEngine；opt-in 不得绕过裁剪；PM→Dev→QA 派工写入 `dispatch_decisions` |
| MA-5 P2-4 冻结实验 | ✅ 半落地（骨架） | `p24_frozen_experiment` A/B/C 报告与 DecisionRecord 载荷；完整生产盲评窗口待独立实验运行 |
| MA-6 条件激活 | ✅ Gate 钩子（未激活） | `p25_adaptive_gate` + A2A 投影；无正净收益证据时保持 `ROLLOUT_NOT_ALLOWED` / `activation_allowed=false` |

工作包验收对照：`WP-METRICS`/`WP-FAILURE`/`WP-TEMPLATE`/`WP-CONTEXT`/`WP-PLAN`/`WP-DISPATCH` 已有单元测试；`WP-EVAL` 提供可复算实验骨架，完整 95% CI 生产对照仍属实验窗口交付物。
### 12.7 过度修复复核与纠正（2026-07-31）

- fixed Hive opt-in 只能对裁剪后仍获准的候选提高优先级，不能恢复被高基线/强顺序规则排除的模板；
- 缺少安全单 Agent champion 时组织选择 fail closed，不以首个多 Agent 候选兜底；
- `CERTIFIED` 名称或旧状态不足以启用模板，必须通过嵌入式五类摘要复算；
- 持久计划终态不可由普通 upsert 改写；上下文 Artifact 查询必须携带 Goal 范围；
- A2A 未知状态拒绝投影；第三方框架仅在试图替换 Kernel 时拒绝，能力池内单 Agent 封装仍允许。

## 13. 单 Agent 生成质量基线计划（2026-07-31）

本计划对应诊断报告 `docs/diagnosis-output-quality-2026-07-31.md`（v2）。结论：当前主要质量瓶颈是主 Worker 未将已有 Agentic 生成循环接入默认交付路径；组织层补强（MA-0..MA-6）不能替代单 Agent 生成闭环的修复。固定 Hive 的净收益未经真实任务实验确认，不应假定必然改善或必然放大；其评估必须以强单 Agent 基线为前提（与 §10.5、Tech-Spec §13.4–§13.7 对齐）。

### 13.1 原则

- 优先修复单 Agent 生成闭环，而非继续增加组织层复杂度；
- `generation_strategy` 是运行时契约，Worker 必须真正遵循；
- 真实构建 / 测试 / smoke 失败必须回灌至生成会话；
- agentic 默认切换由成功率 / 成本 / 延迟门槛门控，不得凭直觉；
- 固定 Hive 与自适应组织评估一律推迟到强单 Agent 基线建立之后（与 MA-5/MA-6 衔接）。

### 13.2 交付批次

| 批次 | 依赖 | 交付物 | 完成门禁 |
|---|---|---|---|
| GQ-0 合同冻结 | 无 | 生成器元数据协议、FailureEnvelope/RepairAttempt、独立生成策略实验合同、冻结任务集、预注册门槛/样本量/停止规则、canary 隔离与回滚合同、现状基线报告 | PRD/Tech/Plan 一致；门槛在实验前登记；合同测试先失败 |
| GQ-1 生成器选择一致性 | GQ-0 | Worker 按 `generation_strategy` 分派生成器；`generator_ref` 与实际类型一致性检查 | 标注 agentic 但实际 artifact-backed 的 Run 被 fail closed；单测覆盖两种策略 |
| GQ-2 会话内验证反馈闭环 + Verification 扩展 | GQ-1 | 真实构建/测试/smoke 失败回灌生成循环；`VerificationAgent` 支持 pytest/项目测试命令 | 真实报错（非仅 gap reasons）进入下一轮；测试缺失有明确降级而非静默跳过 |
| GQ-3 影子 / Canary 对照 | GQ-2 | artifact-backed vs agentic 按独立生成策略实验合同对照；影子隔离副作用，canary 稳定分桶 | 报告含 95% CI、用户结果与护栏；不得占用 P2-4 组织实验维度 |
| GQ-4 默认切换决策 | GQ-3 | 按 GQ-0 预注册门槛生成唯一 DecisionRecord；达标后 `agentic` 设为默认 | kill switch、在途 Run 语义及回滚验证通过 |
| GQ-5 / MA-5 固定 Hive 重评估 | GQ-4 + MA-5 实验骨架 | 基于强单 Agent 基线运行真实组织实验并重评固定 Hive 净收益 | 形成 DecisionRecord；未证明正净收益不扩大 Hive 启用 |

### 13.3 建议实施顺序

1. **GQ-0（1 短迭代）**：冻结选择器契约、一致性不变式、回灌合同与 canary 合同；产出现状基线报告。
2. **GQ-1（1–2 迭代）**：修复 `worker/main.py` 生成器选择；加 `generator_ref` 一致性检查与 fail-closed。
3. **GQ-2（2 迭代）**：打通真实失败回灌；`VerificationAgent` 接入 pytest/项目测试。
4. **GQ-3（1 实验窗口）**：影子/canary 对照，产出可复算报告。
5. **GQ-4（1 迭代）**：按 GQ-0 预注册门槛形成切换 DecisionRecord；达标才默认 agentic。
6. **GQ-5 / MA-5（决策轮）**：运行真实组织实验，基于强单 Agent 基线重评固定 Hive（见 §12）。

### 13.4 与 MA-0..MA-6 的关系

- 本计划建立的「强单 Agent 基线」是 MA-5 真实实验与 MA-6 条件激活的前提；现有 MA-5 仅指实验骨架，真实组织实验与 GQ-5 合并在 GQ-4 后执行。
- `aar1_certified_hive` 代码默认值为 False；生产当前已在既有范围 opt-in。GQ-5 前保持该范围且不得扩容，并继续受认证摘要和 TaskFeatures 裁剪约束；P2-5 保持 `ROLLOUT_NOT_ALLOWED`。
- 不引入框架替换 Kernel（沿用 §12.5）；`generation_strategy=agentic` 使用自研 `AgenticCodeGenerator`，非第三方编排内核。

### 13.5 工作包验收

- `WP-GEN-SELECT`：两种策略下生成器分派正确；`generator_ref` 标签、对象类型与策略三者不一致时 fail closed，并写入 Evidence；
- `WP-GEN-FEEDBACK`：构建/测试/smoke 真实失败进入下一轮修正的结构化输入；`ArtifactBackedCodeGenerator` 再生成携带真实报错；
- `WP-VERIFY-TEST`：VerificationAgent 能解析并执行 pytest/项目测试命令；测试缺失有明确降级而非静默跳过；
- `WP-CANARY`：对照在冻结模型/工具/预算下可复算，产出成功率/成本/延迟与 95% CI；`GeneratorSelector` + `canary_gate` 已使 canary 真正按 `goal_id` 选生成器，且强制 GQ-2→GQ-3 顺序；
- `WP-DEFAULT-GATE`：默认切换由版本化门槛门控，且具备回滚路径；`apply_gq4_promotion` 已把 `gq4_default_switch_gate` 接成强制门，未过即 `DomainError`，kill switch 运行时覆盖。

### 13.6 明确不做

- 不把「开蜂巢」当作提升单 Agent 输出质量的首要手段；
- 不在强单 Agent 基线建立前默认 agentic 或扩大 Hive；
- 不假定固定 Hive 必然改善或必然放大质量问题（结论留给 GQ-5）；
- 不以 LLM 裁判替代真实构建/测试/smoke 验证。

### 13.7 实现状态（2026-07-31）

| 批次 | 状态 | 说明 |
|---|---|---|
| GQ-0 合同冻结 | ✅ 已完成 | 元数据协议、FailureEnvelope/RepairAttempt（迁移 `0041`）、独立生成策略实验合同、预注册门槛、影子/kill-switch 合同、`docs/gq0-baseline-report-2026-07-31.md` |
| GQ-1 生成器选择一致性 | ✅ 已完成 | Worker/`app_delivery` 经 `generator_factory` 分派；`assert_generator_consistency` fail-closed + Evidence |
| GQ-2 会话内反馈 + Verification | ✅ 半落地 | FailureEnvelope 注入再生成；VerificationAgent pytest/项目测试；AgentRunner 一次受控修正；完整生产成功率窗待观测 |
| GQ-3 影子 / Canary | ✅ 控制流已实现 | `GeneratorSelector` 按 `goal_id` 选生成器；`canary_rollout_allowed` + `canary_gate` 强制 GQ-2→GQ-3；canary% 默认 0、闸门默认 False（不开流量） |
| GQ-4 默认切换决策 | ✅ 控制流已实现 | `drive_generation_strategy_experiment` + `apply_gq4_promotion` 强制门（未过则 `DomainError`）；**代码默认**仍 `artifact-backed`，正式晋级需 DecisionRecord + 翻转 env |
| GQ-5 / MA-5 固定 Hive 重评 | ⏳ 未开 | 依赖 GQ-4；生产既有 CERTIFIED_HIVE opt-in **保持不扩容** |

工作包：`WP-GEN-SELECT`/`WP-GEN-FEEDBACK`/`WP-VERIFY-TEST`/`WP-CANARY`/`WP-DEFAULT-GATE` 控制流均已落地并有单测（`test_generation_quality.py`）。`WP-CANARY` 的流量开关（`canary_gate`/`canary_percent`）与 `WP-DEFAULT-GATE` 的晋级门（`apply_gq4_promotion`）为代码强制不变式；**完整真实任务实验窗口**（真实模型/工具/预算下跑双臂、产出 95% CI）仍属后续交付，但此时已有可驱动、可复算的控制流支撑，不再只是空钩子。生产运行时策略可由运维以 `REGENT_GENERATION_STRATEGY` 覆盖（≠ GQ-4 晋级）；部署不得擅自改写。详见 `docs/gq34-promotion-control-flow-2026-07-31.md`。

**阻塞更正（2026-07-31）**：GQ-3 真实流量窗另受 Tech-Spec §13.8 约束——agent 工具须先改走 Docker 沙箱（统一计划 CD-0.1）后方可合规打开 canary。详见 §14。

**状态更新（2026-07-31 重订）**：CD-0.1 名义沙箱已完成，但 **N-3 族**（entrypoint 吞命令、**N-3c** uid 写盘失败、**N-3d** 路径静默挂空、N-3b DinD）使 docker 模式尚未「真执行」。**CD-6 全绿前打开 canary 只会得到无效或假绿数据**。权威下一步：[`docs/conversational-delivery-next-plan-2026-07-31.md`](./docs/conversational-delivery-next-plan-2026-07-31.md)；CD-6 工作包：[`docs/cd6-execution-plan-2026-07-31.md`](./docs/cd6-execution-plan-2026-07-31.md)。

## 14. 对话式完整交付计划（2026-07-31）

> CD-0…5：[`docs/conversational-delivery-plan-2026-07-31.md`](./docs/conversational-delivery-plan-2026-07-31.md)。  
> **下一步（CD-6…CD-12，ACTIVE 重订）**：[`docs/conversational-delivery-next-plan-2026-07-31.md`](./docs/conversational-delivery-next-plan-2026-07-31.md)。  
> **CD-6 执行级**：[`docs/cd6-execution-plan-2026-07-31.md`](./docs/cd6-execution-plan-2026-07-31.md)。  
> 需求：[Regent-PRD.md](./Regent-PRD.md)；技术：[Regent-Technical-Spec.md](./Regent-Technical-Spec.md) §13.8 / §25。

### 14.1 批次与依赖（CD-0…CD-5）

| 批次 | 名称 | 依赖 | 状态 |
|---|---|---|---|
| CD-0 | 止血：沙箱 / transcript 审计 / AC1 门禁可信 | 无 | ✅ 已完成（名义隔离；真执行见 CD-6） |
| CD-1 | 交付状态机接线 + 类型化拒绝 + goal_intent 早交人 | CD-0.3/0.4 | ✅ 已完成 |
| CD-2 | 合规 GQ-3 窗 + 统一 Verification 闸门 + GQ-4 | CD-0.1 + §13 GQ-2 | 🟡 控制流就绪；**阻塞于 CD-6/7** |
| CD-3 | WorkBuddy 体验：审阅面 / 交人选项 / 成本 / 工具轨迹 | CD-1 | ✅ 已完成 |
| CD-4 | 对话层 agent loop（PRD §4.4 新需求） | DecisionNote §4.4 ACCEPTED | ✅ 已完成 |
| CD-5 | 恢复度量 / SSE 自适应轮询 / 结构瘦身 | CD-1 | ✅ 最小完成（Coordinator/token 流持续） |

### 14.4 下一步（CD-6…CD-12）— 2026-07-31 重订

| 批次 | 名称 | 依赖 | 状态 |
|---|---|---|---|
| CD-6 | 沙箱真执行：N-3 族 + T1–T6 | CD-0.1 | ✅ S0 已验证（镜像+worker e2e） |
| CD-7 | 技 P1-1…4 + N-4/N-6 | CD-6 全绿 | 🟡 7.1 marker + 7.4 预算隔离已落地；7.2/7.3/7.5 待 |
| CD-8 | GQ-3 真实 canary 实验窗 | CD-6+7 | 🟡 待运维 |
| CD-9 | GQ-4 条件晋级 | CD-8 报告 | 🟡 PENDING |
| CD-10 | capability 执行适配器 | CD-8 后 | ⚪ 优先于推流 |
| CD-11 | token 流 / LISTEN | 不阻塞 | ⚪ |
| CD-12 | Coordinator + F-10 | CD-7 稳定后 | ⚪ 现在不抽 |

### 14.2 门禁

1. **CD-6 未全绿**（含 N-3c/N-3d，且不得仅用 `echo ok` 验收）：禁止生产 `canary_gate` / 提高 `canary_percent`。
2. **CD-7 未绿**：禁止开 GQ-3 窗。
3. CD-6 期间默认禁 `_NETWORK_PREFIXES` 裸开网；N-4 完整治理在 CD-7.5（除非 Owner 加速）。
4. 不删除 Permit / Outbox / Evidence / Audit / Reconciler；`.env=agentic` ≠ GQ-4。

### 14.3 与同事评审文档关系

[`docs/conversational-delivery-architecture-review-2026-07-31.md`](./docs/conversational-delivery-architecture-review-2026-07-31.md) 为 REVIEW 输入（§9 修正为准）。编码基线：§14 + CD-0…5 计划 + **重订 next-plan CD-6…12** + CD-6 执行级。

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
