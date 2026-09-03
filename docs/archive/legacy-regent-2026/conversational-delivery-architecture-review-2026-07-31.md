# 对话式完整交付 — 架构深度核对（2026-07-31）

> 状态：REVIEW（评审结论，非编码基线）
> **编码基线已迁移至**：[`conversational-delivery-plan-2026-07-31.md`](./conversational-delivery-plan-2026-07-31.md) + `Regent-Plan.md` §14；
> 需求/技术同步见 `Regent-PRD.md` §4.1/§4.4、`Regent-Technical-Spec.md` §13.8、DecisionNote `decision-note-auto-start-journey-2026-07-31.md`。
> 目标问题：如何让 Regent 像 Claude Code / WorkBuddy 一样，通过自然对话让 Core 完整交付结果。
> 方法：产品专家 + 技术专家双线独立核对，冲突项由第三方复核代码裁决。

> **⚠️ 修订提示（2026-07-31 第二轮，请先读）**
> 本文 §1–§8 为**未对照 PRD / Technical-Spec 时的第一轮结论**，其中 **S3 已撤销**、**S2 已降级**、
> 产品专家 D4 已证伪。请以文末 **§9 基线文档对照与结论修正** 为准。
> 依项目惯例（参见 `docs/regent-verification-2026-07-30.md`）**不改写历史正文**，修正集中在附录。

---

## 0. 一句话结论

**Regent 缺的不是「agent 能力」，而是「agent 被接通」。**

真正的 agent loop（think → act → observe → iterate）已经实现在
`core/src/regent/agent/agent_runner.py:105`，40 轮 / 200k token / 900s 预算齐备，
但它被三道默认关闭的开关挡在门外，且只覆盖「写代码」这一段。
对话层则是一个把用户意图压成 8 个枚举的**单次分类器**。

所以现状是：**两个半截 agent，中间用 Outbox 事件焊接，任何一端都不掌握全局。**

---

## 1. 双专家冲突项裁决

| 冲突 | 产品专家 | 技术专家 | 复核结论 |
|---|---|---|---|
| 生成器选择路径 | `worker/main.py:252` 硬编码 `ArtifactBackedCodeGenerator`，忽略 `generation_strategy` | 走 `generator_factory.build_generator_selector` → 可达 `AgenticCodeGenerator` | **技术专家对机制**（当时复核成立）。**现行口径（2026-08-11）**：代码默认已是 `agentic`；下表旧默认摘录仅历史核对 |

裁决依据（`core/src/regent/config.py`）：


> **口径更新（2026-08-11）**：成稿时 Settings 仍写 `artifact-backed` 默认；现行 `config.py` 代码默认已是 `agentic`，`artifact-backed` 为 kill-switch / scaffold fallback；canary gate/percent 仍关闭。下列代码摘录仅作历史核对。
```python
generation_strategy = "artifact-backed"        # L27  默认走模板
generation_strategy_canary_percent = 0         # L32  灰度 0%
generation_strategy_canary_gate = False        # L36  灰度总闸关闭
generation_strategy_fallback = "artifact-backed"  # L30
```

成稿时：`resolve_effective_generation_strategy()` 在默认配置下**恒返回 artifact-backed**（暗启动从未点亮）。**现行**：默认返回 `agentic`；canary 仍关；`artifact-backed` 仅作 kill-switch / scaffold fallback。

---

## 2. 现状链路（已核实）

```
POST /v1/app-projects/drafts                    api/app_projects.py:70
  └─ create_draft()                             LLM 单次 → ProductUnderstanding
  └─ GoalExecutionService.start()               api/app_projects.py:74-78
       idempotency_key = "auto-start:{goal_id}"  ← 创建即启动，spec 自动 FROZEN

POST /v1/app-projects/{id}/guidance             api/app_guidance.py:29   ← 真实 NL 入口
  └─ AppGuidanceService.guide()                 app_guidance_service.py:163
       ├─ _conversation_history(limit=10)
       ├─ provider.generate_structured(...)     单次 LLM → 8 枚举分类
       └─ handler 表 → 写 Outbox

Outbox → worker/main.py → runtime/dispatcher.py
  └─ execution_orchestrator.py:3919-3941        事件 → handler 表
       GENERATION_RUN_REQUESTED :3926
         └─ generator_factory.py:125            ← 此处决定模板 or agentic
              └─ agent/agent_runner.py:79       ← 唯一的 agent loop（默认不可达）

POST /v1/conversations/{id}/messages            api/conversations.py:99
  └─ conversations.append()                     纯 CRUD，不触发任何执行
```

**两条 NL 入口互不连通**，`/v1/conversations` 是死路。

---

## 3. 已核实的关键缺陷

| # | 严重度 | 缺陷 | 证据 |
|---|---|---|---|
| **S1** | 🔴 安全 | agent 工具在 **worker 宿主进程**执行，命令白名单含 `python `/`pip `，等价于任意代码执行 | `agent/tools.py:213` `asyncio.create_subprocess_shell(cwd=root)`；白名单 `tools.py:111-127`；`config.py:17` `sandbox_mode="local"` 默认。真正的 `DockerSandboxDriver`（`infrastructure/sandbox.py`）只服务 build 路径，**agent loop 未使用** |
| **S2** | 🔴 治理 | 创建即 auto-start，GoalSpec 由系统自签冻结，`confirmed_by="regent-core:auto-snapshot"` | `api/app_projects.py:74-78`。与 `Regent-PRD.md §4.1`「用户确认 → FROZEN → Start」直接冲突，**审计链上的「人类确认」是伪造的** |
| **S3** | 🔴 能力 | agentic 分支默认不可达，标称 agentic 实跑模板 | `config.py:27/32/36`（见 §1） |
| **S4** | 🟠 审计 | agent transcript 持久化异常被静默吞掉 | `agent/generator.py:147-148` `except Exception: pass`。「最自由的那部分，审计最弱」 |
| **S5** | 🟠 架构 | `capabilities/` 与 LLM **完全无桥接**。全仓 `ToolSpec(` 仅命中 `agent/tools.py` 与 `agent_runner.py` | 3 个 `capability.json` 是确定性校验规则，由 `infrastructure/*_capability.py` 消费，从不进 LLM 上下文。与「认证能力池供 agent 调用」的架构叙述不符 |
| **S6** | 🟠 体验 | SSE 是 **1s 轮询数据库**，不是推流；agent 中间过程完全不外发 | `api/events.py:16 _poll_changes`；`agent_runner.py:118-122 on_turn` 只吐「第 N/40 轮」，工具调用/结果/思考全不可见；`provider.chat()` 整包返回，无 token 流 |
| **S7** | 🟠 智能 | 对话历史与 Evidence **都不进 agent 上下文** | `context_assembler.py` 分段中无 evidence / conversation；对话历史只喂分类器（limit=10） |
| **S8** | 🟡 前端 | 确认卡永不可点：auto-start 已发 `GOAL_EXECUTION_*` → `goalAlreadyMoving()` → `canConfirm=false` | `MessageList.tsx`。卡片沦为装饰 |
| **S9** | 🟡 前端 | 右侧 Agent 名册是前端**推导**的，非真实执行态 | `app_guidance.py` 无 `status.agents` 填充，回落 `lib/agents.ts deriveAgents` |

---

## 4. 与 Claude Code 的机制对标

| 关键机制 | Claude Code | Regent | 判断 |
|---|---|---|---|
| 单一对话入口 | ✅ | 🟠 两条入口，一条是死路 | **该补** |
| 立即动手、边做边说 | ✅ | ✅ auto-start | 已具备 |
| 对话层本身是 agent loop | ✅ | 🔴 单次 8 枚举分类器 | **该补（最关键）** |
| 工具调用过程可见 | ✅ 流式 | 🔴 只有轮次计数 | **该补** |
| 执行反馈自纠正 | ✅ 不限次 | 🟠 恰好 1 次（`agent_runner.py:268 _allow_nested_repair`） | **该补** |
| 工具真实接地 | ✅ | 🟠 5 个硬编码工具，capabilities 未接入 | **该补** |
| 内联一次确认 + 记忆偏好 | ✅ Allow / Always | 🟠 有 `DecisionPreference` 骨架，前端未通 | **该补** |
| 沙箱隔离 | ✅ | 🔴 宿主执行 | **该补（安全红线）** |
| Permit / Outbox / Evidence / Audit / Reconciler | ❌ 无 | ✅ | **不该抄掉——这是护城河** |
| 交付状态机（非死端） | ❌ 无 | ✅ | **不该抄掉** |

---

## 5. 核心架构建议：两级 Effect 模型

「LLM 只能提出结构化 Command，状态转换由确定性 Application Service 执行」这条原则
**在代码里已被局部放弃，只是没被承认**：`AgentRunner` 内的 LLM 可自由 `write_file` / `run_command`，
无逐步 Command、无 Permit。

这个分层其实**是对的**，应当被正式承认并补齐审计：

| 效应类型 | 治理方式 | 落地 |
|---|---|---|
| 沙箱内可逆效应<br/>（写文件、跑测试、读代码） | **事后 Effect 日志**，不做前置审批 | 把 `TranscriptTurn` 升格为一等 append-only `tool_effects` 表，与 Outbox **同事务、同幂等键** |
| 不可逆 / 外部效应<br/>（部署、付费 API、发消息、发布） | **保留前置 ExecutionPermit** | 复用现有 `REQUESTED→GRANTED→CLAIMED→CONSUMED` |

**收益**：agent 在沙箱内全速自由循环，可审计性不降反升，
且不必为每次 `write_file` 走一遍状态机。治理与顺滑不再互斥。

---

## 6. 改造路线（按依赖排序）

### 第 0 轮 — 止血（不做不能上线）

| 项 | 改造点 | 验收 |
|---|---|---|
| **R0-1** 沙箱隔离 | `agent/tools.py:213` 改走 `infrastructure/sandbox.py DockerSandboxDriver`；`config.py:17` 生产强制 `sandbox_mode="docker"` | agent 无法在宿主执行任意命令；逃逸测试用例通过 |
| **R0-2** 审计不可静默丢失 | 删除 `agent/generator.py:147-148` 的 `except: pass`；transcript 与 Outbox 同事务，失败入 DLQ | 注入持久化故障，Run 阻断而非静默继续 |
| **R0-3** 确认语义二选一 | 方案 A：draft 不 auto-start，确认卡真实可点；方案 B：PRD 承认 auto-start，卡片改为「随时纠偏」入口 | `goal_specs.confirmed_by` 不再出现 `auto-snapshot`，或 PRD 与 audit 语义对齐 |

### 第 1 轮 — 点亮 agent

| 项 | 改造点 | 验收 |
|---|---|---|
| **R1-1** 打开 agentic | 走 `generation_strategy_promotion.py` 既定灰度门禁，逐步 `canary_gate=True` → `canary_percent>0` → 默认 agentic | 标 agentic 的 Run 产生真实工具调用轨迹 |
| **R1-2** 过程可见 | `agent_runner.py:118 on_turn` 扩展为 `on_event(turn, tool, args, result)`；`api/events.py` 由轮询改 PG `LISTEN/NOTIFY` | 前端实时看到「读了什么文件、跑了什么命令、报了什么错」 |
| **R1-3** 自适应修复 | `agent_runner.py:268 _allow_nested_repair` 由「恰好 1 次」改为按 gap 类型的预算化重试，受 `recovery_budget_multiplier` 约束 | 注入可修复缺陷，Run 在预算内自愈，Evidence 完整 |

### 第 2 轮 — 对话即 agent（本次诉求的核心）

| 项 | 改造点 | 验收 |
|---|---|---|
| **R2-1** 对话层升级为 loop | `app_guidance_service.py:163 guide` 由单次分类改为工具循环，把现有 8 个 `_handle_*` 注册为 ToolSpec（复用 `agent/types.py`） | 用户一句话可触发多步澄清+执行，无需枚举命中 |
| **R2-2** 入口合一 | `/v1/conversations/{id}/messages` 接入 guidance loop，废弃双入口 | 单一对话入口可完成全流程 |
| **R2-3** 能力即工具 | 新增 `agent/capability_tools.py`：读 `capabilities/*/capability.json` → 生成 ToolSpec 注入；**前置**需为 capability.json 补 `parameters` JSON Schema | agent 可自主发现并调用认证能力 |
| **R2-4** 上下文补全 | `context_assembler.py` 增 `_evidence_segment()` / `_conversation_segment()`；配套检索而非全量 | agent 能引用 Evidence 与历史对话做决策 |

### 第 3 轮 — 全程闭环

| 项 | 改造点 | 风险 |
|---|---|---|
| **R3-1** 交付 agent | 新增 `agent/delivery_agent.py`，把 `execution_orchestrator.py:3919-3941` 的 handler 包装成工具 | 与事件状态机双写冲突；**必须以状态机为唯一写入方**，agent 只提 Command |
| **R3-2** token 流 | `model/provider.py` 增 `chat_stream()` | 工具调用增量解析复杂 |
| **R3-3** 偏好记忆 | TaskCard 增「总是允许此类」写回 `DecisionPreference`；审批类保持不可超时放行 | 同类第二次不再打断，审计仍留痕 |

---

## 7. 明确不做的事

- ❌ 不为了顺滑而删掉 Permit / Outbox / Evidence / Audit / Reconciler
- ❌ 不让审批类 HumanTask 超时自动放行
- ❌ 不让 agent 直接写状态机（agent 只提 Command，转换仍由 Application Service 确定性执行）
- ❌ 不在 R0-1 完成前把 agentic 灰度打开（当前是可利用的宿主 RCE）

---

## 8. 文档—实现漂移清单（需同步修订）

| 文档 | 主张 | 实况 |
|---|---|---|
| `Regent-PRD.md §4.1` | 用户确认 → FROZEN → Start | 创建即 auto-start，系统自签 |
| `apps/regent-console/README.md` / PRD §4.3 | 右侧名册来自 `status.agents` | 前端推导，后端无此字段 |
| 架构叙述「认证能力池」 | capabilities 供 agent 调用 | 与 LLM 零桥接 |
| 架构叙述「Outbox 驱动前端」 | Outbox → 前端 | SSE 直接轮询表，绕过 Outbox |
| `docs/delivery-state-machine-2026-07-31.md` | 「首版薄层待建」 | `delivery_state.py` 已全量落地（文档滞后） |

---

# §9 基线文档对照与结论修正（第二轮）

对照对象：`Regent-PRD.md`、`Regent-Technical-Spec.md`（均为 CURRENT）、`Regent-Plan.md`。

## 9.1 撤销与降级的第一轮结论

### ❌ S3「agentic 默认不可达是缺陷」— 撤销

这**不是缺陷，是规范明文要求的门禁顺序**：

- `Regent-PRD.md:409`：「canary 仅当 `generation_strategy_canary_gate=True`（GQ-2 反馈闭环验证后）
  **且** `canary_percent>0` 时……**闸门默认 False**」
- `Regent-Technical-Spec.md:431`：「`canary_gate=False`（默认）或 `canary_percent=0` 时，
  任何 goal 都回落默认策略」
- `Regent-Technical-Spec.md:438`：「运行时默认仍由 `generation_strategy` 驱动
  （Settings 代码默认 `artifact-backed`）」
  > **口径更新（2026-08-11）**：成稿时 Tech-Spec 仍写 artifact-backed 代码默认；现行 §0.1/§13.7 已更正为代码默认 `agentic`，artifact-backed 为 kill-switch / scaffold fallback。上引仅作历史核对。

`config.py` 默认值须以**当时**规范与**现行** §0.1 对照；第一轮称之为「暗启动从未点亮」属误判：
它是 GQ-2 → GQ-3 → GQ-4 的强制串行门禁，PRD §10.5 与 Tech-Spec §13.7 有完整的实验、
统计与 DecisionRecord 前置要求。

**真正的状态**（`Regent-Technical-Spec.md:717`）：GQ-0～GQ-4 控制流**均已实现**，
待办的是「GQ-3 真实流量窗与 GQ-4 晋级 DecisionRecord」——**缺的是实验，不是代码**。

### ❌ 产品专家 D4「`status.agents` 后端未填充」— 证伪

实际填充在 `application/app_guidance_service.py:378` `"agents": agents`。
产品专家 grep 的是路由层 `api/app_guidance.py`，未下沉到 service 层。
`Regent-Technical-Spec.md:718` 的描述与实现一致。

### 🔽 S2「审计链上的人类确认是伪造的」— 降级为「字段语义复用」

复核 `goal_execution_service.py:53-98`，代码实际是**诚实的**：

| 维度 | 实际写入 | 是否伪装成人类确认 |
|---|---|---|
| Audit action | `SNAPSHOT_GOAL_SPEC_FOR_EXECUTION` | 否，动词是 SNAPSHOT 不是 CONFIRM |
| `confirmed_by` | `"regent-core:auto-snapshot"` | 否，显式机器身份前缀 |
| payload | `confirmation_required: False`、`snapshot_by: actor` | 否，明示无需确认 |
| 代码注释 L60-62 | 「freezes an execution snapshot, not the user's intent forever」 | 设计意图有记录 |

问题降级为两点：① `confirmed_by` 字段被复用来存机器身份，语义混淆；
② **该设计变更没有任何 CURRENT 文档承认**（见 9.2）。

## 9.2 真实冲突（代码与 CURRENT 文档不一致，需 DecisionRecord）

### 🔴 C1 用户旅程第 2 步被架空，且无 DecisionRecord

| 来源 | 表述 |
|---|---|
| `Regent-PRD.md:136` §4.1 | 「2. **用户确认** → FROZEN GoalSpec → Start」 |
| `Regent-Technical-Spec.md:711` | 「GoalSpec DRAFT/FROZEN/SUPERSEDED、**确认闸门**、对话驱动修订（0019）」 |
| `Regent-Technical-Spec.md:714` | 「确认后自主执行闭环：**Confirm/Start 分离**（0022）」 |
| 代码 `api/app_projects.py:69-78` | `/drafts` 创建后**同一请求内** auto-start，绕过 confirm |
| 代码 `api/app_projects.py:105` | `/confirm` 端点存在且完整（含 `expected_spec_hash` 校验），但主链路不经过它 |

三份 CURRENT 文档都描述「确认闸门 / Confirm-Start 分离」，代码把它做成了**旁路**。
`/confirm` 不是死代码（对话修订路径仍可达），但**首次交付主链路完全不经过确认**。

全仓 `*.md` grep `auto-start`：**仅命中本评审文档自身**。
即这是一次**未登记的产品语义变更**。

耐人寻味的是，该设计的原始出处在**归档目录**里：
`docs/archive/AgentOS-Implementation-Plan-v0.2.md:66`——
「GoalSpec 是内部运行快照，不是新的用户输入标准。系统可以在低风险范围内带假设探索；
只有涉及根目标、硬约束、权限和高影响行动时才请求用户确认。」

**代码执行的是一份已归档文档的设计，而 CURRENT 的 PRD 还写着旧旅程。**
这比单纯的"文档滞后"更值得处理——两份文档在打架，代码站在了归档的那一边。

> 补充澄清（避免过度报警）：auto-start **不触碰** PRD §12「无审批的全自动生产发布」这条非目标。
> 发布审批独立存在且已修复（`Regent-Technical-Spec.md:723`，`require_release_human_approval` 默认 true）。
> C1 的影响范围仅限 GoalSpec 冻结环节。

### 🔴 C2 Agent 工具执行环境违反安全规范

| 来源 | 要求 |
|---|---|
| `Regent-Technical-Spec.md:383` | 「断网非 root 构建」 |
| `Regent-Technical-Spec.md:425` | 「影子任务必须运行在**独立 sandbox** 与 Artifact namespace」 |
| `Regent-Technical-Spec.md:595` | 「网络默认拒绝」 |
| `Regent-Technical-Spec.md:620` | 「生成 Agent 不得拥有生产批准权或**长期凭据**」 |
| 代码 `agent/tools.py:204-230` | `create_subprocess_shell` 在 **worker 宿主进程**执行 |
| 代码 `agent/tools.py:111-127` | 白名单含 `pip `、`python `、`curl ` |
| 代码 `config.py:17` | `sandbox_mode` 默认 `"local"` |

`pip ` + `curl ` 的组合等于**联网 + 任意代码执行**，与「网络默认拒绝」「断网构建」直接冲突。
worker 宿主进程持有数据库凭据与 provider API key，等于「生成 Agent 拥有长期凭据」。

这条在第一轮已列为 S1，对照文档后**性质升级**：不只是安全隐患，是**规范违反**。
且它恰好卡住 GQ-3——Tech-Spec §13.7 要求 canary/影子任务跑在独立 sandbox，
当前 agent 路径不满足这个前置条件。

**这解释了为什么 canary_gate 至今是 False 是合理的。**

### 🟠 C3 用户入口 API 与规范清单不符

`Regent-Technical-Spec.md:632-644` 规定的用户入口：

```text
POST /goals                     # 提交自然语言目标
POST /goals/{id}/freeze         # 冻结 GoalSpec
POST /goals/{id}/resume / cancel
POST /humantasks/{id}/approve / reject
```

实际实现是 `/v1/app-projects/drafts`、`/v1/app-projects/{id}/guidance`、
`/v1/app-projects/{id}/confirm`。规范清单里**显式列出了 `/freeze` 独立端点**，
进一步印证 §4.1 的确认闸门是有意设计，而非文档笔误。

`Regent-Technical-Spec.md:626`「`/v1` 保持兼容，破坏性变化进入 `/v2`」——
当前是路径族整体替换，规范未同步。

### 🟠 C4 `/v1/conversations` 与「对话驱动」叙事不符

`Regent-Technical-Spec.md:711` 称「**对话与 App 身份**：Conversation 持久化……对话驱动修订（0019）」。
实际 `api/conversations.py:99 append_message` 是纯 CRUD，不触发任何执行；
对话驱动能力全部在 `/app-projects/{id}/guidance`。两条 NL 入口并存，一条是死路。

## 9.3 文档已登记、无需重复报告的项

第一轮列出但文档**已明确登记为待办**的，不构成"发现"，只是进度：

| 第一轮编号 | 文档登记位置 | 状态 |
|---|---|---|
| S6 SSE 轮询 / 过程不可见 | — | 文档 `:718` 称「SSE 实时推送」，措辞略夸大但非实质冲突 |
| R1-3 自适应修复 | `Regent-Technical-Spec.md:401`「GQ-2 必须至少触发一次受控修正」 | 已实现一次修正（`:717`），扩展为自适应属 GQ-3+ 范畴 |
| R2-4 Evidence 入上下文 | `Regent-PRD.md:393`「压缩摘要必须结构化保留…证据引用」 | 部分登记，检索能力未登记 |
| R3-2 token 流 | 未登记 | 属体验增强，非规范要求 |

## 9.4 对照后的修正结论

第一轮说「Regent 缺的不是 agent 能力，而是 agent 被接通」——**方向对，归因错**。

对照文档后的准确表述：

> **Regent 的 agent 能力已按规范建成，门禁也按规范关闭。
> 真正卡住的是两件事：一是 agent 执行环境不满足自身安全规范（C2），
> 使 GQ-3 影子/canary 实验无法合规开跑；二是对话层从未被规划为 agent loop（这是设计空白，非实现欠账）。**

「对话式完整交付」在现有文档体系里**没有对应的产品条目**——
PRD §4.3 只规定了控制台的展示语义（进度详略、Agent 名册），
没有规定「对话层本身应具备自主规划能力」。第一轮 R2 系列属于**新增需求**，
需要走 PRD 修订而不是当作 bug 修复。

## 9.5 建议的处置顺序（修正版）

| 序 | 动作 | 类型 | 依据 |
|---|---|---|---|
| 1 | agent 工具改走 `DockerSandboxDriver`，生产禁用 `sandbox_mode=local` | **修 bug**（规范违反） | Tech-Spec §13.7/§19/§20 |
| 2 | 就 C1 出 DecisionRecord：确认「auto-start + 事后纠偏」为新语义，并同步修订 PRD §4.1、Tech-Spec §25、§21 API 清单 | **补文档** | PRD §11「每个激活阶段必须有…唯一 DecisionRecord」 |
| 3 | `agent/generator.py:147-148` 去掉 `except: pass`，transcript 与 Outbox 同事务 | **修 bug** | Tech-Spec §22「必须观测…压缩与恢复」 |
| 4 | 在 1 完成后开 GQ-3 真实流量窗 | **走既定流程** | PRD §10.5、Tech-Spec §13.7 |
| 5 | 「对话层升级为 agent loop」立项，先写 PRD 条目再编码 | **新需求** | 现有文档无对应条目 |

> 第 5 项若直接编码，会重演 C1 的问题——**代码跑在文档前面，事后无人能判断这是设计还是漂移**。
