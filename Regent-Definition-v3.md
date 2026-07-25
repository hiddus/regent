# Regent v3 产品定义与技术架构（以 AgentOS 定义为基准）

> 状态：CURRENT（2026-07-25 冻结） / 以 `定义.png` 中 AgentOS 公式与六要素为准  
> 日期：2026-07-24（冻结：2026-07-25）  
> 性质：权威执行基线（Owner 批准升 CURRENT）  
> 取代：本文件冻结为 CURRENT，成为 `Regent-PRD-v2.md` 的 v3 执行基线。  
> 配套技术规范：`Regent-Architecture-v3.md`（同目录，同为 CURRENT）

---

## 0. 基准定义

> **基准来源**：`D:/workfiles/其他项目/AgentOS/定义.png`

AgentOS 是在目标、约束和治理规则下，基于实时资源和系统状态，动态组织（或重构）Agent 组织以持续自治达成目标的操作系统。

在时刻 `t`，AgentOS 从所有可行的 Agent 组织方案集合 `𝒪` 中，选择能够在目标 `G`、约束 `C` 和治理规则 `V` 下，利用实时资源 `R_t` 和系统状态 `S_t`，使业务效用函数 `U` 最大化的最优组织 `O_t^*`，并通过持续执行与反馈迭代，不断优化直至目标达成。

```text
O_t^* = arg max_{O_t ∈ 𝒪} U(O_t | G, C, V, R_t, S_t)

约束条件：
- C(O_t) ≤ 0          满足所有约束
- V(O_t) = True       符合治理与合规要求
- R_t(O_t) ≥ R_min    资源可用且不低于阈值
- S_{t+1} = Transition(S_t, O_t)  状态随执行不断演进
- lim_{t→∞} GoalAchieved(S_t, G) = True  持续迭代直至目标达成
```

**Regent v3 将以上定义作为不可违背的架构锚点**：Regent 是一个目标驱动的受治理操作系统，其内部全部机制（目标解释、能力补齐、组织构建、运行调度、状态演进、资源治理、应用生成）都服务于在约束与治理下寻找并执行最优组织 `O_t^*`。

---

## 1. Regent 公式化定义

```text
Regent(G, C, V, R_t, S_t) = arg max_{O_t ∈ 𝒪_Regent} U(O_t | G, C, V, R_t, S_t)
```

其中：

- `G`：用户输入的自然语言目标，经解释后形成版本化 `GoalSpec`。`G` 是唯一的必需用户输入，其余均为可选。
- `C`：显式与推断的约束集合，包括业务规则、预算、时效、资源上限、安全合规与数据隔离。
- `V`：治理规则，包括身份与权限（I&A）、决策与执行审计、风险控制、合规检查与副作用授权。
- `R_t`：实时资源，包括可用 Agent/技能/工具、数据与模型、算力、渠道、外部服务与人工介入窗口。
- `S_t`：系统状态，包括目标/工作/执行/证据状态、能力注册表、组织运行状态、记忆与历史反馈。
- `𝒪_Regent`：Regent 在时刻 `t` 可形成的所有人机组织方案，包括单 Agent、固定模板、多 Agent 动态组织及人类参与形态。
- `U`：业务效用函数，默认综合成功概率、成本、延迟、人工负担、安全风险和可解释性。具体场景可替换或加权。

**Regent 的产品身份不是「更强的单一 Agent」，而是管理 Agent 组织在约束与治理下自治运行的目标操作系统**。

---

## 2. 六要素在 Regent 中的具体映射

### 2.1 G（Goal）目标

- 用户以自然语言输入目标；结构化 `GoalSpec` 是 Regent 的解释产物，不是用户必须预先完成的需求文档。
- `GoalSpec` 必须区分：显式目标、显式约束、系统推断、未知项、非目标项。
- 目标可分解为子目标/工作单元；子目标本身也受 G/C/V/R/S 约束。
- 目标终态：`ACHIEVED`（证据充分）、`EXHAUSTED`（约束与资源下无可行路径）、`FAILED`（不可恢复完整性错误）、`CANCELLED`（Owner 终止）。
- 可恢复状态：`PAUSED`（用户 resume）、`WAITING_HUMAN`（HumanTask 唤醒）、`BLOCKED`（资源/环境/授权变化或重规划唤醒）。

### 2.2 C（Constraint）约束

- **业务规则与政策约束**：允许/禁止的操作、数据不出区域、不得联网、不得修改输入等。
- **资源/预算/时效约束**：Token/算力/时间/成本上限，预算超出时进入 `BLOCKED` 或 `CANCELLED`。
- **安全与合规约束**：凭据不泄露、PII 最小化、最小权限、无未经批准的生产发布。
- **运行时约束**：App 与 Core 隔离、业务模型不得反向固化进 Core、生成者不能自行评价自己的成果。
- 约束是 `C(O_t) ≤ 0` 的硬边界；任何组织方案若违反约束，不得进入候选集合 `𝒪_Regent`。

### 2.3 V（Governance）治理

- **身份与权限（I&A）**：所有 Agent、工具、用户和外部服务都有身份与最小权限；越权访问即时触发安全停（`>0 立即停`）。
- **决策与执行审计**：每一次重规划、组织变更、副作用执行、审批都生成不可变审计记录。
- **风险控制与合规检查**：高风险行动必须获得一次性 `ExecutionPermit`；人类审批通过 `HumanTask` 等待，等待期间不占用 Worker。
- **数据治理**：外部网页、工具输出、Generated App、Agent 消息、Candidate Memory 默认 `UNTRUSTED_DATA`，不得成为指令或授权来源。
- 治理是 `V(O_t) = True` 的准入条件；任何候选组织必须先通过治理检查才能执行。

### 2.4 R_t（Resource）实时资源

- **Agent/技能/工具**：能力注册表、技能包、MCP 工具、A2A 可发现 Agent。
- **数据与模型/算力/渠道**：文件、数据库、模型端点、向量索引、外部 API、发布渠道。
- **人力与外部服务**：HumanTask 响应窗口、外部审价者、支付/邮箱/托管服务等。
- **资源可用性动态变化**：R_t 是时间的函数；资源不足时进入 `BLOCKED`，资源恢复后重新寻优。
- **资源阈值 R_min**：组织方案 `O_t` 所需资源 `R_t(O_t)` 不得低于最小可运行阈值；否则该方案从 `𝒪` 中剔除。

### 2.5 S_t（State）系统状态

- **环境与业务实时状态**：文件系统、外部系统响应、网络可达性、产品运行指标。
- **任务进度与历史反馈**：Goal/Work/Run 状态机、Evidence、Observation、Permit 状态、审计链。
- **组织运行状态与记忆**：当前组织拓扑、Agent 分工、协作关系、运行中会话、长/短期记忆。
- **状态演进**：`S_{t+1} = Transition(S_t, O_t)`；每次执行都使状态转移，所有状态变更持久化、可恢复、可审计。
- **记忆层次**：Working Memory（当前任务上下文）、Episodic Memory（经历/事件）、Semantic Memory（行业知识/规则/经验）。

### 2.6 O（Organization）组织

- **Agent 拓扑与角色分工**：Regent 根据目标将能力映射为角色（如 Planner、Coder、Evaluator、Reviewer、Human Proxy）。
- **协作关系与流程编排**：Agent 之间通过 A2A 或内部消息协议协作；工具调用通过 MCP 完成。
- **动态重构与扩缩容**：组织不是静态的。Regent 在每次状态转移后重新评估是否需要替换、新增、解散 Agent 或切换为人类介入。
- **最小组织原则**：默认使用单 Agent；动态组织是需要对照实验验证的候选增益机制，不是产品预设。
- 组织 `O_t` 是效用最大化的决策变量，不是架构装饰。

---

## 3. 约束条件（必须满足）

Regent 在任意时刻 `t` 选择组织 `O_t` 时，必须同时满足：

| 条件 | Regent 表达 | 含义 |
|---|---|---|
| `C(O_t) ≤ 0` | 约束检查通过 | 业务规则、资源、预算、安全合规全部满足 |
| `V(O_t) = True` | 治理检查通过 | 身份权限、审计、Permit、合规检查全部通过 |
| `R_t(O_t) ≥ R_min` | 资源足够 | 所需资源可用且不低于最小运行阈值 |
| `S_{t+1} = Transition(S_t, O_t)` | 状态转移 | 执行后状态可预期地演进并持久化 |
| `lim GoalAchieved(S_t, G) = True` | 持续收敛 | 系统持续迭代直至目标达成、耗尽或取消 |

这五个条件是 Regent 架构设计的铁律。任何技术组件、协议、接口或数据模型都不得破坏它们。

---

## 4. 产品边界与不变原则

### 4.1 边界

```text
regent/
├─ core/   # 通用自治组织运行内核（G/C/V/R/S/O 的通用实现）
└─ apps/   # Regent 创建和运营的独立应用（目标成果）
```

- Core 只表达目标、能力、组织、工作、执行、证据、策略和资源。
- 每个 App 拥有独立源码、依赖、数据、测试与部署，可以脱离 Core 运行。
- 具体内容类型、订阅方式和业务指标不得成为 Core 领域对象。

### 4.2 不变原则（从 PRD v2 继承并保留）

1. **证据优先、需求权威、最小组织、失败关闭**。
2. **Generated App 与 Core 分离**：业务模型不反向固化进内核。
3. **PostgreSQL + Outbox + 不可变 Artifact + Permit + UNKNOWN 对账**。
4. **内部 Smoke 不得满足产品价值 Gate**。
5. **Provider 不得注入缺失的业务功能**。
6. **多 Agent 不是默认架构**：必须通过冻结实验验证净收益。
7. **投票不能替代机器验证或真实 Observation**。
8. **生成者不能自行评价、批准和发布自己的成果**。
9. **禁止用源码字符串、类名存在或伪 Observation 代替行为验收**。
10. **变更门禁**：本文件定义以规范源为准；如需修改则新建定义 ID，禁止原地修改或追溯改写。

---

## 5. 阶段化目标（与 AgentOS 组织寻优对齐）

### 5.1 P0：受治理的目标执行内核

- 目标：证明 Regent 能在约束与治理下，完成一条从 `G` 到 `O_t` 再到 `S_{t+1}` 的最小完整链。
- 默认组织：单 Agent。
- 关键验收：`CSV_SUMMARY_BASELINE` —— 读取输入、应用约束、生成输出、Evidence 可审计、Worker 中断可恢复、幂等重放。
- 必须同时满足：
  1. Core 在空 Apps 条件下通过 `CSV_SUMMARY_BASELINE`；
  2. 仅凭普通 Goal 形成可解释的最小组织，补齐至少一个能力缺口，并通过 `EVT_PARSER_GAP`；
  3. 在独立 `apps/` 目录创建可运行产品候选，新 App 接入不改变 Core 领域模型；
  4. 运行可恢复、副作用幂等、高风险行动受控，状态、Evidence、Permit 和审计可追溯；
  5. 完成 A/B/C 冻结任务集的首轮对照实验，并依据预先冻结的门槛形成继续、转向或停止的产品 DecisionRecord。

### 5.2 P1：基于证据的独立应用创建与运营

- 目标：让 Regent 在未知具体产品实现的前提下，从 `G` 和 `C/V` 出发，自主完成证据发现、产品假设比较、需求修订、能力解析、应用生成、隔离构建、发布和观测闭环。
- 组织：仍以单 Agent 或最小固定组织为主；多 Agent 作为候选实验。
- 关键链：`GoalSpec → Evidence → Hypothesis → Requirement → Build → Preview → Observation → Gate → Decision`。
- 验收：两层 Graduation（SYSTEM_GRADUATED + PRODUCT_EVIDENCE_GRADUATED），写入唯一 `GraduationDecisionRecord` 和 `P2StartDecisionRecord`。

### 5.3 P2：多目标、多运行时、可持续学习与受控生产运营

- 目标：将 Regent 从单 Goal 可信闭环扩展为多 Goal 并发调度、多 Runtime Profile、可评测组织与受控生产运营。
- 组织：此时动态组织 `O_t` 的寻优才成为规模化收益重点；P2-5 自适应组织必须依赖 P2-4 Eval Harness 的统计 Gate 证明正净收益。
- 关键承诺：P2-1 调度与资源治理、P2-2 多 Runtime Profile、P2-4 最小 Eval Harness（先于自适应组织）。
- 条件承诺：P2-3 长期记忆、P2-5 自适应组织、P2-6 Champion/Challenger 实验平台。
- 候选：P2-7 受控生产发布、P2-8 受监管自我改进、P2-9 能力生态。

---

## 6. 与 PRD v2 的映射关系

| PRD v2 概念 | 图片定义六要素 | 说明 |
|---|---|---|
| Goal / GoalSpec | `G` | 自然语言目标 → 结构化解释结果 |
| 约束（显式/推断） | `C` | `C(O_t) ≤ 0` 的硬边界 |
| ExecutionPermit / HumanTask / Audit | `V` | 治理与授权机制 |
| Capability / Agent / Tool / Runtime | `R_t` | 实时资源与能力 |
| Goal/Work/Run / Evidence / Memory | `S_t` | 系统状态与记忆 |
| Organization / 动态组织 | `O_t` | 决策变量：候选组织方案 |
| CSV baseline / P1 Graduation / P2 | `U` 的迭代优化 | 效用最大化与收敛过程 |

本 v3 文件不是推翻 PRD v2，而是将 PRD v2 的实质性内容重新用 AgentOS 六要素框架表达，使其与白皮书级定义一致。

---

## 7. 北极星与护栏（与 v2 保持一致，可执行冻结）

北极星：

```text
CostPerVerifiedSuccess
= (模型成本 + 工具成本 + 基础设施成本 + 人工成本 + 失败恢复成本)
  / 独立验证成功的 Goal 数
```

护栏（红线）摘要：

- 核心任务完成率 < 70% → 停
- 证据不足率 > 30% → 停
- 端到端 P95 延迟 > 4h → 停
- 重复副作用 > 0 → 立即停
- 未对账 UNKNOWN > 15 min → 立即停
- 安全违规/凭据泄露 > 0 → 立即停
- 内部流量误入产品决策 > 0 → 立即停

完整定义与样本量要求继续引用 `Regent-Measurement-Decision-Framework.md`。

---

## 8. 非目标

- 无审批的全自动生产发布。
- 无证据的「自治公司」叙事。
- 用投票替代事实验证。
- 把长上下文、更多 Token 或更多 Agent 数量作为成功指标。
- 为单个生成 App 向 Core 添加业务专用模型。
- 未经认证的开放 Agent/Tool 市场。
- 支付系统和完整商业化后台（除非作为独立 App Goal）。

---

## 9. 下一步动作

1. 本文件需经过 Owner 评审并冻结为 `CURRENT` 后，才能成为执行基线。
2. 同步更新/新建 `Regent-Architecture-v3.md`，将六要素映射为具体技术模块、接口与数据模型。
3. 更新 `docs/definitions/REGENT-DEFINITION-1.0.txt` 机器可读规范源（如需改变定义，则新建 `REGENT-DEFINITION-2.0.txt`）。
4. 更新 CI 定义哈希检查，防止定义漂移与第二规范副本。
