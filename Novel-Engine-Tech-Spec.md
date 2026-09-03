# Novel Engine 技术规范

> 版本：v4.2
>
> 更新：2026-09-03
>
> 状态：ACTIVE — 当前技术实现唯一权威源

## 1. 架构原则

### 1.1 执行拓扑

持续 Agent loop 是唯一主流程：

`intake → global plan → rolling chapter plan → context compile → character performance → direct → render → extract/verify/commit → evaluate`

- Agent loop 是主核心流程，负责持续规划、执行、检查、提交、恢复和人机互动。
- Hive 是 loop 内的局部执行器，不是候选架构、质量增强开关或研究变量。
- 仅当一个步骤同时满足两个条件时启用 Hive：① 各工作线程必须持有彼此隔离的信息；② 工作线程之间没有前置依赖、可以并发执行。任一条件不满足，均由主 loop 顺序执行。
- 典型 Hive 场景是同一场景内多个角色基于各自 `InformationSet` 独立表演。导演依赖角色表演结果，不得与其输入并行；普通候选评价若无信息隔离要求，也不因“可以并行”而启用 Hive。
- 不存在“单 Agent 对多 Agent”的全局架构二选一，也不实现一次调用生成复杂长篇。
- 人工 gate 仅用于目标变化、不可逆节点、证据冲突或自动修复超限。

### 1.2 分层

| 层 | 所有权 |
|---|---|
| Novel Domain | 作品、目标、关键路径、章、场景、角色、知识边界、Canon、裁决 |
| Novel Application | loop、状态机、局部重演、发布、分享、导出、评测 |
| Regent Core | TaskRuntime、ModelGateway、ArtifactStore、Permit、Observation、lease/timer/outbox |
| Novel Web | C 端路由、用户态投影、SSE、PWA、阅读 |
| Internal Ops | 现有 Regent Console，不进入 C 端 bundle |

禁止把通用 `Goal`/`Work`/`ProjectAgentSession` 直接暴露为小说 API；它们只能通过适配器承载领域行为。

## 2. 仓库与构建边界

建议新增：

```text
core/src/regent/novel/
  domain/
  application/
  ports/
  infrastructure/
apps/novel-web/
tests/novel/
fixtures/novel_eval/
```

- `apps/regent-console` 固定为 `/internal/ops` 或独立构建产物。
- C 端不得 import `ArtifactPanel`、`ToolTrace`、源码浏览器、经营面板等内部组件。
- `apps/regent-desktop` 不作为阶段一交付入口。
- 私有 `novelMaker` 以冻结 commit 迁移；迁移清单逐模块标注直接迁移、适配、重写或不迁移。

## 3. 领域模型

### 3.1 核心聚合

| 对象 | 关键字段 |
|---|---|
| `StoryWork` | id, owner_id, title, genre, state, public_state, branch_id, version |
| `StoryGoal` | raw_intent, normalized_goal, assumptions, locked_at, version |
| `CriticalPath` | nodes, dependency_edges, frozen_through_chapter, version |
| `CriticalNode` | type, promise, preconditions, consequences, requires_human |
| `ChapterRun` | work_id, branch_id, chapter_no, state, current_step, version |
| `SceneCard` | goal, participants, location, must_reach, forbidden, pov |
| `PersonaSpec` | identity, drives, voice, stable_traits, version |
| `InformationSet` | persona_id, scene_id, grants, exclusions, context_hash |
| `Performance` | intention, action, utterance, provenance, model_call_ids |
| `CanonCommit` | parent_version, facts, source_hash, validation_id, created_at |
| `DecisionRequest` | node_id, options, default, deadline, impact, version |
| `PublicWork` | immutable published snapshot; later phase only |
| `OnboardingSession` | user_id, work_id, clarify_round, question_count, assumptions, locked_at |
| `ExportNotice` | user_id, work_id, notice_version, satisfied_at |
| `ModerationCase` | work_id, chapter_no, target_type, decision, reason_code, appealed_at, resolved_at |

`ExportNotice` 承载 PRD FR-23 的“首次导出前告知”：**拦截判据为 `satisfied_at` 为空或 `notice_version` 不等于当前条款版本**——条款升级后必须重新告知，不能让老用户永久停留在旧版本。每次告知另写一条 append-only 告知日志用于举证，但**拦截只查 `ExportNotice`，不查日志**。

### 3.2 Canon 与知识边界

- Canon 为 append-only 版本链，不原地修改。
- 模型不能直接写 Canon，必须经过 `extract → verify → commit`。
- `ContextCompiler` 是确定性纯函数：相同 snapshot、角色和场景产生相同 `ContextManifest`。
- `KnowledgeGrant` 至少标注事实、角色、获得时间、来源、版本和可见范围。
- 角色 Agent 只获得一次性、限定 work/scene/persona/context_hash/expiry 的 capability，不能查询完整 Canon。
- 路径依赖边类型至少包括 causal、temporal、knowledge、foreshadow、object_state。

### 3.3 状态机

`StoryWork`：

`ONBOARDING | READY | RUNNING | PENDING_DECISION | PAUSED_QUOTA | PAUSED_COST | RECOMPUTING | FAILED | DONE | CANCELLED | ARCHIVED`

`ChapterRun`：

`QUEUED | RUNNING | PENDING_DECISION | RETRYABLE_FAILED | TERMINAL_FAILED | CANONIZED | SUPERSEDED | CANCELLED`

`ChapterStep`：

`ASSEMBLE | PERFORM | DIRECT | WEAVE | REVIEW | CANON`

步骤状态：`PENDING | RUNNING | SUCCEEDED | FAILED | SKIPPED`。

人工等待和配额暂停必须释放 worker。状态转换采用 expected_version 条件更新，失败返回冲突，不允许静默覆盖。

## 4. 章执行协议

1. 锁定输入 snapshot 与路径版本。
2. 生成确定性 ContextManifest 和角色 InformationSet。
3. 为每个逻辑模型调用创建预算预留。
4. 模型调用在数据库事务外执行，结果写不可变 `ModelCall`。
5. 硬导演检查泄密、时间/空间、人物状态、必达节点和规则。
6. 软导演检查节奏、声纹、趣味性；不得覆盖硬规则结果。
7. 失败按依赖图重演最小子图；超过上限创建 DecisionRequest。
8. 正文通过后抽取 FactCandidate。
9. 短事务锁 ChapterRun：校验版本，写正文、CanonCommit、成本结算、状态和 outbox 事件。
10. 失败不得出现“Canon 已提交但成本/正文不可达”或“记费但成功产物丢失”。

声纹质量不属于 Canon 状态机：声纹不达标阻断 `Performance/Prose` 接受并触发重演，不得表述为“阻断入 Canon”。

## 5. 幂等与恢复

| 操作 | 逻辑幂等键 |
|---|---|
| 新建作品 | `user_id:client_nonce` |
| 章节步骤 | `work_id:branch_id:chapter_no:step:input_version` |
| 模型调用 | `provider:model:prompt_hash:context_hash:purpose` |
| 裁决提交 | `decision_id:decision_version:client_nonce` |
| Canon 提交 | `work_id:branch_id:chapter_no:source_output_hash` |
| 配额结算 | `logical_call_id:funding_pool` |
| 分享/撤回/导出/发布/结算 | 强制 `Idempotency-Key` |

- logical call 与 attempt 分离；已成功 logical call 恢复时复用，不重新付费。
- 同键同参数返回首个结果；同键异参数返回 409。
- checkpoint 至少为 `work + branch + chapter + step + input_version`。
- 恢复响应包含 reused_calls、avoided_cost、last_sequence，允许用户验证未重跑。

## 6. 成本、额度与账本

- 金额统一 `amount_minor BIGINT + currency CHAR(3)`；禁止 Float。
- `ModelCall` 是成本事实源：估算、预留、实际、缓存 token、供应商请求 id、usage 来源、价格版本。
- 额度采用 `reserved → consumed/released` 两段式；失败或取消释放未消费额。
- `QuotaLedger`、`WorkLedger` 和收入流水 append-only；余额由流水派生并与物化账户校验。
- 资金来源分为 `platform_grant | user_paid | onboarding`。
- generation scope 必须有 work_id/chapter/step；reading scope 禁止生成。
- 结算顺序：广告/打赏净额确认 → 平台成本回收 → 用户分成。
- 任何条件账户更新必须检查 row count，否则事务失败。
- 现有非幂等 `record_cost` 不得用于小说生成路径。

## 7. 身份、权限、审核与数据保留

- 服务端从 session/token 解析 principal，不接受客户端 `actor` 作为授权依据。
- 私有 work、chapter、canon、ledger、decision、export 查询必须过滤 owner_id/tenant_id。
- 越权访问不得泄露资源是否存在。
- 公共阅读与创作使用不同 router、依赖和数据库权限；阅读身份不能调用生成、修改 Canon 或读取私有 trace。
- 裁决深链 token 绑定 user、decision、version、expiry、nonce；预览不消费，提交后失效。
- 用户删除作品采用产品软删除；财务、授权和创作证据按法务确认的保留策略归档，不级联物理删除。
- 内容审核结论写入 `ModerationCase`，记录 decision、reason_code 和证据引用；用户可申诉，结果回写 `appealed_at`/`resolved_at`。
- **无审核结论不得视为已通过**——待审状态对作者可见，不得静默放行也不得静默吞掉作品。
- 审核、投诉、申诉记录与创作证据适用同一保留策略；误判纠正路径必须留痕。

## 8. API 契约

OpenAPI 是唯一传输契约，并在 CI 中生成 TypeScript DTO。Novel 核心响应禁止 `Record<string, unknown>` 和任意 string 状态。

最低 API：

```text
POST   /v1/novel/works
GET    /v1/novel/works
GET    /v1/novel/works/{work_id}
POST   /v1/novel/works/{work_id}/directions
PUT    /v1/novel/works/{work_id}/critical-path
POST   /v1/novel/works/{work_id}/runs
POST   /v1/novel/works/{work_id}/pause
POST   /v1/novel/works/{work_id}/resume
GET    /v1/novel/works/{work_id}/chapters/{chapter_no}
GET    /v1/novel/works/{work_id}/decisions/{decision_id}
POST   /v1/novel/works/{work_id}/decisions/{decision_id}/resolve
POST   /v1/novel/works/{work_id}/facts/report
POST   /v1/novel/works/{work_id}/shares
DELETE /v1/novel/works/{work_id}/shares/{share_id}
POST   /v1/novel/works/{work_id}/exports
GET    /v1/novel/works/{work_id}/events
```

所有 mutation 支持幂等键。关键路径更新携带 expected_version/ETag；409 返回 current_version 和 conflict_summary。

统一错误 envelope 含 code、message、request_id、retryable、available_actions。429 返回 Retry-After；204 不含 JSON body。

## 9. 持久事件与 SSE

```json
{
  "event_id": "uuid",
  "sequence": 42,
  "schema_version": 1,
  "type": "decision.requested",
  "occurred_at": "...",
  "work_id": "...",
  "branch_id": "...",
  "chapter_no": 8,
  "decision_id": "...",
  "causation_id": "...",
  "correlation_id": "...",
  "data": {}
}
```

最低事件：work snapshot/state、story phase、critical path、chapter progress/done、decision requested/resolved/expired、pause/resume、quota/cost pause、recompute、recoverable failure、completion、ETA change、share revoked。

- SSE 使用 `id: sequence`，支持 Last-Event-ID 或 after_seq。
- 客户端按 sequence 去重；发现缺口立即请求 snapshot resync。
- 超出保留窗返回 `resync_required`。
- heartbeat 只表示连接存活；数据查询失败必须发送 `stream.degraded`，不能吞异常后保持伪健康。
- transient 内存进度仅作提示，不能驱动权威状态。

## 10. 前端架构

### 10.1 构建与路由

新建 `apps/novel-web`，使用 React + TypeScript + Vite。路由：

```text
/works
/create
/works/:workId/path
/works/:workId/progress
/works/:workId/decisions/:decisionId
/works/:workId/read/:chapterNo
/works/:workId/share
/works/:workId/export
```

URL 是状态；刷新后只凭 URL 和服务端 snapshot 恢复。登录和通知跳转保留 return URL。403、404、410 有不同页面。

### 10.2 用户态投影

后端提供 `UXProjection`：public_stage、last_completed_artifact、next_milestone、eta_range、safe_to_leave、stale_at、action_required、available_actions。

前端不得自行从内部 step 猜用户态；未知状态映射为 `unknown_recoverable`，不能猜成成功或失败。

`DecisionView` 包含 trigger_summary、why_human、options[].near_term_consequence、reversibility、default_option、deadline、impact_level、confirm_nonce。

### 10.3 PWA 与隐私

- app shell 与作品内容缓存分离。
- 私有正文默认不进共享 Cache Storage。
- 离线阅读必须由用户逐作品显式开启，并支持清除本地副本。
- 裁决、公开、导出和付费 mutation 不离线排队；路径可保存本地草稿，但重连必须比较版本并由用户处理冲突。
- Service Worker 不缓存 events、认证、支付、授权和导出接口；登出、撤回和删除触发 purge。
- 任务运行期间禁止 SW 自动 reload。

### 10.4 移动端等价

- 360×640 视口必须完整完成三动作：目标输入、关键路径调整、重大裁决。
- 关键路径调整必须提供上移、下移、替换、增、删、锁等显式操作；**拖拽、hover、右键和多指手势不得作为唯一手段**。
- 桌面端可提高信息密度，但不得拥有移动端不具备的核心创作能力。

## 11. 模型路由与可追溯性

PRD 不固定厂商。路由类别：

- 全局规划和软导演：高推理档；
- 角色表演和正文：创作质量档，允许按题材选择；
- 抽取、分类和检索重排：低成本结构化档；
- 硬约束：代码/规则优先。

每次调用记录供应商、精确模型版本、prompt 版本、sampling、上下文快照、支持时的 seed、usage 和输出 hash。Evaluator 与 Generator 使用不同模型家族并定期做漂移检查。

## 12. 评测规范

### 12.1 Schema

必须定义 `ContextManifest`、`KnowledgeGrant`、`FactCandidate`、`CanonCommit`、`ValidationEvidence`、`ReplayScope`、`EvalRun`，均带版本、provenance 和 hash。

### 12.2 指标

硬指标：关键路径覆盖、每万字矛盾率、未知知识泄露率、Canon precision/recall、局部修复率与 blast radius、恢复副作用、成本和 p50/p95 时延。

软指标：情节、人物、语言、世界、情绪和期待满足；作者与读者分组评价。

Evaluator 每项返回证据片段与 rubric；无证据 abstain。至少 10–20% 样本双人复核，并报告一致性、与人评相关性和置信区间。

### 12.3 阈值

- 安全/逻辑硬门：严重泄密、严重 Canon 冲突、重复副作用原则上 0 容忍。
- 质量门：相对冻结 novelMaker loop 在同模型和预算带下非劣，同时目标机制至少一项显著改善。
- 成本收益门：采用 Pareto gate，不把质量、成本和时延揉成可任意加权总分。
- 最终阈值由 5–10 seed pilot 标定后冻结；候选值不得直接宣传为生产 SLA。

### 12.4 局部质量机制实验

- E1 信息集裁剪 ON/OFF；
- E2 硬导演 ON/OFF；
- E3 全历史、层级摘要、摘要+实体召回；
- E4 最小子图重演与整章重跑；
- E5 未固化窗口 N=1/3/5；
- E6 逐段聚合、摘要和双轨评价；
- E7 20→50→100→150 章升级。

每项预注册主/次指标、样本量、停止规则和失败后的删除/重设计动作。产品纠错保持开放，但能力评测使用 untouched cohort，不能为测量方便关闭真实功能。

Hive 不在实验清单中。角色信息隔离由产品的角色知识边界承诺决定；当隔离后的多个角色任务可并发时，调度器必须使用 Hive。相关测试只验证路由判定、信息不串线、结果收敛和故障隔离是否正确，不验证“该不该使用 Hive”。

## 13. 架构守卫

| ID | 守卫 |
|---|---|
| G-01 | 不存在一次调用生成整部作品的正常路径 |
| G-02 | 角色上下文由确定性 ContextCompiler 装配 |
| G-03 | 每个角色只获得自己的 InformationSet |
| G-04 | 硬导演先于软评审，软评审不能覆盖硬失败 |
| G-05 | 重演有上限并限定最小依赖范围 |
| G-06 | 模型不能直接写 Canon |
| G-07 | Canon、账本和创作留痕 append-only |
| G-08 | 所有生成调用有 work/chapter/step/cost_scope/logical_call_id |
| G-09 | 恢复复用成功逻辑调用，不重复计费 |
| G-10 | 金额不用浮点；余额可由流水重放 |
| G-11 | 服务端主体决定权限，不采信客户端 actor |
| G-12 | 无 owner/tenant 条件的私有读取 fail closed |
| G-13 | 裁决和默认 timer 竞争仅一个结果成功 |
| G-14 | 阅读、分享、导出链路不持有生成能力 |
| G-15 | 导出参数白名单，字节流不经过 LLM，始终带 AI 标识 |
| G-16 | C 端 bundle 不包含内部运维组件 |
| G-17 | SSE 有持久 sequence、补帧、缺口和 resync 契约 |
| G-18 | 私有正文默认不进入共享缓存 |
| G-19 | 声纹失败阻断正文接受，不与 Canon 提交混为一个状态 |
| G-20 | 自动 Judge 无人工校准不得作为发布 gate |
| G-21 | 单次 onboarding 澄清轮次 ≤1、每轮问题数 ≤3；信息不足时写入 `assumptions` 后继续，不得无限追问 |
| G-22 | 导出前校验 `ExportNotice`：`satisfied_at` 为空或 `notice_version` 不等于当前版本时阻断导出并重新告知 |
| G-23 | 审核、投诉与申诉必须落 `ModerationCase`；无结论不得视为通过 |
| G-24 | 调度器仅在 `requires_information_isolation=true` 且 `parallelizable=true` 时路由 Hive；其他步骤必须留在主 Agent loop，且该判定不得由模型自由决定 |

守卫按其依赖对象所在里程碑落地，不要求 M0 在对象尚不存在时通过全部守卫。

## 14. 当前代码复用矩阵

| 能力 | 当前状态 | 处置 |
|---|---|---|
| worker lease/heartbeat、outbox、durable timer | 已有 | 直接复用/适配 |
| ProjectAgentSession checkpoint/steering | 已有通用实现 | 适配为 StoryRun 外壳 |
| Budget reserve/settle/release | 已有但金额和原子性需修复 | 迁移后复用 |
| ExecutionEvent 审计 | 已有 | 扩展领域 payload，不能替代状态事件 |
| HumanTask/WAITING_HUMAN | 已有 | 适配 DecisionRequest |
| SSE 连接 | 已有 | 重做持久事件和补帧语义 |
| C 端认证/owner 隔离 | 未满足 | M0 前置 |
| 小说领域模型/API | 未实现 | 新建 |
| Novel Web/PWA | 未实现 | 新建 |
| 小说数据集与评测 | 未实现 | 新建 |
| 公共池/账本/分成 | 未实现 | Later |

## 15. 研究依据

- [LongStoryEval](https://aclanthology.org/2025.acl-long.799/)：采用跨章证据聚合与卷/全书摘要双轨评价。
- [ConStory-Bench](https://aclanthology.org/2026.findings-acl.410/)：采用事实/时间等错误 taxonomy 和前中后位置分桶。
- [TimeChara](https://aclanthology.org/2024.findings-acl.197/)：按角色×时间点×在场情况建立知识边界。
- [StoryWriter](https://arxiv.org/abs/2506.16445)：参考事件图与动态历史压缩，但不外推其短篇实验规模。
- [PostgreSQL Numeric Types](https://www.postgresql.org/docs/current/datatype-numeric.html)：金额不使用浮点。
- [SSE Last-Event-ID](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)：采用持久事件游标恢复。

## 16. 修订记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-09-02 | v4.0 | 基于最新代码和六角色复核重新生成；补齐小说领域、状态机、事务、成本、权限、事件、前端、PWA、评测和守卫，并明确真实复用边界。 |
| 2026-09-02 | v4.1 | 补齐 PRD P0 需求的技术落点：`OnboardingSession`（澄清轮次）、`ExportNotice`（导出告知状态与条款版本重触发）、`ModerationCase`（审核与申诉）、§10.4 移动端等价；守卫增至 G-23。 |
| 2026-09-03 | v4.2 | 将 Agent loop/Hive 关系提升为固定调度契约：Hive 仅由“信息隔离且可并发”双条件触发，删除独立/共享角色采样实验，并新增 G-24 确定性路由守卫。 |
