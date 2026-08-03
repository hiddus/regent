# Regent 产品定义与需求文档

> 状态：CURRENT  
> 日期：2026-08-03（吸收 M6 5% canary 开窗、交付缺口软暂停、prompt-cache 成本修复；并登记 2026-08-02/03 迭代：混合控制平面 H0–H2、Session Work Plan W0–W4、控制台可观测性、Agent 内核 W4 收口、交付缺口恢复）  
> 性质：权威执行基线（Owner 批准）  
> 配套技术规范：[`Regent-Technical-Spec.md`](./Regent-Technical-Spec.md)  
> 测量框架：[`Regent-Measurement-Decision-Framework.md`](./Regent-Measurement-Decision-Framework.md)  
> 编码执行清单：[`Regent-Plan.md`](./Regent-Plan.md) §14；CD-0…5：[`docs/conversational-delivery-plan-2026-07-31.md`](docs/conversational-delivery-plan-2026-07-31.md)  
> **下一步（ACTIVE）**：[`docs/m6-canary-watch-plan-2026-08-01.md`](docs/m6-canary-watch-plan-2026-08-01.md)（M6 观察窗）；内核：[`docs/agent-core-restoration-executable-plan-2026-08-01.md`](docs/agent-core-restoration-executable-plan-2026-08-01.md)  
> 对齐审计：[`docs/doc-implementation-alignment-audit-2026-07-31.md`](docs/doc-implementation-alignment-audit-2026-07-31.md) §8  
> 编码门禁：`P2StartDecisionRecord` 已签署 → **允许** `p2-scheduler-01`；生产小比例 canary 须满足沙箱前置与窗记录；**GQ-4 默认切换仍禁止**（需独立 DecisionRecord）

---

## 0. 永久定义

> **规范源**：[`docs/definitions/REGENT-DEFINITION-1.0.txt`](docs/definitions/REGENT-DEFINITION-1.0.txt)（`REGENT-DEFINITION-1.0`，冻结，不得修改）  
> CI 在定义 ID/哈希漂移或出现第二规范副本时阻止合并。

### 0.1 公式化定义

Regent 接收人的自然语言 Goal，在明确的约束、资源、授权和治理边界内，自主解释目标，发现并补齐能力，形成当前最合适的最小人机组织，创建并运营可脱离 Core 独立运行的应用，并依据可验证的外部结果持续调整计划、能力和组织，直至目标达成、资源耗尽、不可恢复失败或被 Goal Owner 取消。

```text
Regent(G, C, V, R_t, S_t) = arg max_{O_t ∈ 𝒪_Regent} U(O_t | G, C, V, R_t, S_t)

约束条件：
- C(O_t) ≤ 0          满足所有约束
- V(O_t) = True       符合治理与合规要求
- R_t(O_t) ≥ R_min    资源可用且不低于阈值
- S_{t+1} = Transition(S_t, O_t)  状态随执行不断演进
- lim GoalAchieved(S_t, G) = True  持续迭代直至目标达成
```

其中：
- `G`：用户输入的自然语言目标，经解释后形成版本化 `GoalSpec`。唯一必需用户输入。
- `C`：显式与推断的约束集合（业务规则、预算、时效、资源上限、安全合规、数据隔离）。
- `V`：治理规则（身份权限、审计、Permit、合规检查、副作用授权）。
- `R_t`：实时资源（Agent/技能/工具、数据/模型/算力、外部服务、人工窗口）。
- `S_t`：系统状态（目标/工作/执行/证据状态、能力注册表、组织运行状态、记忆）。
- `𝒪_Regent`：所有人机组织方案（单 Agent、固定模板、多 Agent 动态组织、人类参与）。
- `U`：业务效用函数（成功概率、成本、延迟、人工负担、安全风险、可解释性）。

**Regent 的产品身份不是「更强的单一 Agent」，而是管理 Agent 组织在约束与治理下自治运行的目标操作系统。**

### 0.2 不可分割的恒定属性

1. **Goal 驱动**：唯一必需的用户输入是自然语言 Goal；GoalSpec 是解释结果，不是用户必须预先完成的需求。
2. **边界内自治**：自治始终受约束、资源、授权、治理和人工接管约束。
3. **能力可演进**：能发现能力缺口，并按复用、配置、组合、构建或请求人类的顺序补齐。
4. **组织为执行手段**：根据目标形成最小人机组织；单 Agent、固定团队、多 Agent 都是候选实现。
5. **独立 App 是目标成果**：生成的 App 拥有独立源码、依赖、数据、测试、部署，可脱离 Core 运行。
6. **外部结果闭环**：依据可验证的外部 Evidence 与 Observation 调整，不是生成者自评或内部 smoke。
7. **明确终止**：必须在成功、耗尽、失败或取消时形成有证据的终态。

### 0.3 不变原则

1. 证据优先、需求权威、最小组织、失败关闭。
2. Generated App 与 Core 分离；业务模型不反向固化进内核。
3. PostgreSQL + Outbox + 不可变 Artifact + Permit + UNKNOWN 对账。
4. 内部 Smoke 不得满足产品价值 Gate。
5. Provider 不得注入缺失的业务功能。
6. 多 Agent 不是默认架构；必须通过冻结实验验证净收益。
7. 投票不能替代机器验证或真实 Observation。
8. 生成者不能自行评价、批准和发布自己的成果。
9. 禁止用源码字符串、类名存在或伪 Observation 代替行为验收。
10. 变更门禁：定义以规范源为准；如需修改则新建定义 ID，禁止原地修改。

### 0.4 产品边界

```text
regent/
├─ core/   # 通用自治组织运行内核（G/C/V/R/S/O 的通用实现）
└─ apps/   # Regent 创建和运营的独立应用（目标成果）
```

- Core 只表达目标、能力、组织、工作、执行、证据、策略和资源。
- 每个 App 拥有独立源码、依赖、数据、测试与部署，可以脱离 Core 运行。
- 具体内容类型、订阅方式和业务指标不得成为 Core 领域对象。

---

## 1. 当前阶段产品使命

当前阶段把永久定义落实为一个可治理、可审计、可恢复的自主目标执行内核。P1/P2 聚焦产品创建与运营场景：获取证据、比较产品假设、冻结需求、补齐能力、生成和验证独立应用、发布受控版本，并根据真实使用证据形成继续、修订或停止决策。

**当前差异化**：竞品与内部脚本多停留在「生成可打开的页面」；Regent 把未知目标转化为一条可追溯、可验证、可迭代的**目标执行证据链**。产品创建场景表现为：`GoalSpec → Evidence → Hypothesis → Requirement → Build → Preview → Observation → Gate → Decision`。

---

## 2. ICP、购买者、主用户与首发场景

### 2.1 首发范围（冻结）

> 低风险、无敏感数据的内部工具或 Web MVP 产品验证。

**首批支持**：内部运营工具、内容/资讯类 MVP、简单表单与工作流原型、静态或轻后端 Web 演示产品。  
**明确排除**：医疗诊断、金融交易、招聘/信贷决策、未成年人数据、高风险基础设施控制、大规模 PII 处理、支付清算。

### 2.2 第一批核心用户（ICP）

| 角色 | 是谁 | 痛点 |
|---|---|---|
| 购买者（Buyer） | 小型产品团队负责人、创业者 | 需要可控成本把想法推进到可验证 MVP |
| 操作者（Operator） | 产品经理 / 技术负责人 | 需要看见阻塞原因、批准高风险动作、组织真实用户验证 |
| Goal Owner | 提出意图并确认 GoalSpec 的人 | 对约束与成功标准负责 |
| Product User | 生成 App 的真实外部或内部试用用户 | 其行为才是产品证据 |

### 2.3 首个高频问题

「我有一个模糊的产品想法，怎样在预算内得到一个**可被独立验证**的 Preview，并根据真实使用决定继续、修订还是停止？」

---

## 3. RACI

| 活动 | Goal Owner | Operator | Capability Provider | Product User | 独立评价者 | 安全/合规签署 |
|---|---|---|---|---|---|---|
| 确认 GoalSpec | A/R | C | I | I | I | C（高风险） |
| 授权外部证据源 / Permit | A | R | C | I | I | C |
| Discovery / Generation 执行 | I | C | C | I | I | I |
| Preview 核心任务验收 | C | R | I | A | C | I |
| Gate / IterationDecision | A | R | I | I | R（盲评） | C |
| 生产发布批准 | A | R | I | I | I | VETO |
| 能力包认证与撤销 | I | C | R | I | C | A |
| P1 Graduation 签署 | C | R | I | C | R | A |

图例：R=执行，A=问责，C=咨询，I=知会，VETO=独立否决权。

---

## 4. 用户旅程

每一步必须向用户展示：**当前状态解释**、**已有证据引用**、**下一步可操作命令**。

### 4.1 成功路径

> 旅程语义以 DecisionNote [`docs/decision-note-auto-start-journey-2026-07-31.md`](docs/decision-note-auto-start-journey-2026-07-31.md) 为准（ACCEPTED）。

1. 创建意图 → 可解释 GoalSpec（显式约束、推断、未知项、非目标）。
2. **快照启动**：主链路允许系统在低风险范围内以机器身份写入执行快照并 Start（Audit：`SNAPSHOT_GOAL_SPEC_FOR_EXECUTION`；`confirmed_by` 可为 `regent-core:auto-snapshot`）。这**不是**人类确认意图。
3. **事后纠偏**：用户随时可通过确认/修订入口更正约束、范围或停止；高影响行动（权限、根目标变更、生产发布等）仍须显式 HumanTask / 审批。
4. Evidence → 实质不同假设 → 唯一 HypothesisDecision。
5. Requirement → Capability Resolution → Generation → 隔离 Build → Preview。
6. 真实 Observation → Gate → CONTINUE / REVISE / STOP。

控制台「确认」卡在主链路上的产品角色是**纠偏与知情**，不是「允许系统开始干活」的门闩。`/confirm` 端点保留给对话修订路径。

### 4.2 失败与例外路径

| 场景 | 用户可见结果 | 下一步 |
|---|---|---|
| GoalSpec 需修订 | SUPERSEDED 旧版，新 DRAFT | 编辑后重新冻结 |
| Evidence 不足或冲突 | RESEARCH_MORE / BLOCKED | 授权来源、绑定能力或停止 |
| 人工审批节点（发布 / 质量 / 外部效应 / Permit） | WAITING_HUMAN + HumanTask | 批准 / 拒绝 / 超时；可对真确认卡使用「总是允许」 |
| 交付缺口软暂停 / 进度停滞提示 | 对话提示 + `DELIVERY_SOFT_PAUSE`（或等价） | 用户补充方向后系统续跑，或停止；**非**审批卡 |
| BLOCKED / UNKNOWN / 超预算 | 明确 failure_code | 对账、扩预算、取消 |
| 暂停 / 恢复 / 取消 | PAUSED / ACTIVE / CANCELLED | 取消不回滚已发生副作用 |
| Preview 拒绝 | 用户拒绝或 Journey 失败 | REVISE 或 STOP；保留证据 |

### 4.3 控制台交互体验

Web Console 是主交付面；下列为产品语义，不规定具体组件实现。

1. **对话流动态进度**：执行过程中进度信息应详略得当——进行中默认展示足够细节以便跟进；节点结束后默认收敛为概览，并允许用户进一步压缩已完成内容，避免长会话被历史进度淹没。用户可主动切换详略。
2. **工作区证据面（2026-08-03）**：右侧为可切换工作区（清单 / 运行 / 改动 / 预览 / 审阅）。**运行中默认「清单」**；参与 Agent 名册与活动流在「运行」Tab；产物预览与审阅按需切换。预览就绪时可首次自动切到预览 Tab。顶栏以清单完成比与运行控制（停止一等）为主，批准/拒绝仅在对话闸门卡上完成。
3. **与 Core 信号对齐**：名册与活动态应对齐 Core 提供的参与 Agent 列表及实时活动信号（含 live 活动摘要）；无列表时允许控制台用既有 Goal/组织信息做最小回退，但不得编造未部署的 Agent。Core 已在 guidance status 中提供 `agents` 时，控制台**必须**优先使用该列表，不得长期运行在推导回退分支。
4. **可审阅交付物**：用户须能查看本轮改动摘要（文件/计划）、验证结论（含 smoke/测试日志摘要）与成本/剩余预算；仅预览 URL + zip 不足以宣称「完整交付」。审阅默认结构化卡片，裸 JSON 仅作高级展开。
5. **失败交人带答案**：当且仅当需要**真审批或主观产品判断**（发布、质量门、外部效应、Permit、goal_intent 等）时，交人界面须提供 2–3 个可执行选项及大致代价（轮次/预算），不得只给「允许 / 拒绝」而把开放式难题甩回用户。闸门卡统一为同一视觉族（计划批准 / 权限 / 问人 / 恢复）。
6. **人工介入边界（2026-08-01）**：人只处理权限与危险效应。交付缺口、进度停滞、自动修复耗尽后的软暂停属于**系统自愈 / 对话续跑**路径，不得要求用户点「允许」或「总是允许」才能继续。「本会话允许」仅绑定真确认类 Permission HumanTask；控制台不得对交付缺口 / 软暂停 / 纯进度停滞消息渲染该按钮。
7. **停止一等**：Abort/停止控件须在运行控制区始终可发现；不得依赖翻开折叠面板才能停止。

### 4.4 对话式完整交付（新增需求）

> 本条为**新增产品条目**（见架构评审 §9.4），不是对既有 bug 的修复说明。编码前须遵循 [`docs/conversational-delivery-plan-2026-07-31.md`](docs/conversational-delivery-plan-2026-07-31.md) CD-4 门禁。

目标体验：用户通过自然对话让 Core **完整交付**可验证结果，接近 Claude Code / WorkBuddy 的「边说边做、过程可见、预算内自纠正」。

产品要求：

1. **单一自然语言入口**：用户消息应进入同一对话执行环；不得长期并存一条「可执行」入口与一条「纯 CRUD 死路」而不在文档中标明。
2. **对话层具备多步工具循环**：对话编排不得止步于单次意图分类枚举；须能在同一轮用户意图下多步澄清、调用已注册工具（含现有 guidance handler 升格为工具）并推进执行——状态写入仍由确定性 Application Service 完成。
3. **过程可见**：生成/修复过程须展示具名工具轨迹（读/写/命令与结果摘要），而非仅轮次计数。
4. **上下文可引用**：对话历史与相关 Evidence 须可被检索进入执行上下文（压缩与预算约束见 §10.4）。
5. **治理不降级**：沙箱内可逆效应可事后审计全速循环；不可逆/外部效应仍须前置 ExecutionPermit。不得为了顺滑删除 Outbox / Evidence / Audit / Reconciler。

验收锚点（摘要；细节见统一开发计划 CD-3/CD-4）：用户一句话可触发多步执行；工具轨迹可观测；失败交人带选项与产物；交付物可审阅。

### 4.5 边跑边想：方案可见与有限选项拍板（2026-08-02）

> 产品方向见 [`docs/direction-note-run-think-learn-2026-08-02.md`](docs/direction-note-run-think-learn-2026-08-02.md)；切片执行见 [`docs/execution-plan-run-think-learn-2026-08-02.md`](docs/execution-plan-run-think-learn-2026-08-02.md)。与 C1 快照启动兼容：能自洽推演则可早开；推演不清时先拍板再 Start。

1. **方案可见**：意图进入后，控制台展示拟议方案（理解、步骤、未知项），消息类型 `GOAL_PLAN_PROPOSED`，而非仅 JSON 元数据。
2. **人辅助决断**：模型判定无法自洽推演时置 `needs_user_fork`，提供 2–4 个有限选项；用户选择后经 guidance `SELECT_OPTION` 写回 GoalSpec / metadata，再继续执行。无「澄清毕业门」。
3. **失败即经验**：生成失败与交付缺口写入 `failure_lessons`，后续轮次注入 acceptance / 生成上下文；怕的不是花预算，是花了不沉淀经验。

---

## 5. P0 能力与验收

### 5.1 P0 核心能力

1. 保存原始 Goal，生成版本化 GoalSpec，区分显式约束、系统推断和未知项。
2. 持久化 Goal、Work、Run、Artifact、Evidence、HumanTask 和 Audit，进程重启后恢复。
3. 从 Goal 与计划推导能力需求，区分能力、权限、资源、信息缺口和普通失败。
4. 通过复用、配置、组合、构建或请求人类补齐能力。
5. 根据能力覆盖组建最小组织，并能收缩、替换和解散。
6. 在 `apps/<app-id>` 创建、构建和测试独立 App。
7. 使用外部 Observation 和 Evidence 评价进展并重规划。
8. 所有副作用行动经过策略判断和一次性 ExecutionPermit。
9. 人工输入与审批使用独立 HumanTask，等待期间不占 Worker。
10. 支持暂停、恢复、取消、预算限制、无进展停止和完整审计。

### 5.2 P0 固定验收

```text
名称：CSV_SUMMARY_BASELINE
Goal：读取授权目录中的 orders.csv，生成 summary.json。
数据：1,12.50 / 2,7.50 / 3,INVALID / 4,10.00
约束：禁止联网；不得修改输入；只能写入 output/。
输出：{"row_count":4,"valid_count":3,"invalid_count":1,"total_amount":30.0}
```

自动验收必须证明：原始 Goal 保存；约束与推断分离；形成 Work 和 Run；输出逐字段相等；Evidence 包含输入与输出哈希；Worker 中断后恢复；幂等重放不产生第二份输出。

```text
名称：EVT_PARSER_GAP
输入：timestamp|category|value|crc32，共 6 行，1 行 CRC32 错误
预置能力：无 EVT Parser
输出：valid_count=5, invalid_count=1
约束：断网；fixtures/ 只读；只写 output/
```

### 5.3 P0 完成定义

P0 作为整体完成，必须同时满足：

1. Core 在空 Apps 条件下通过 `CSV_SUMMARY_BASELINE`；
2. 仅凭普通 Goal 形成可解释的最小组织，补齐至少一个能力缺口，并通过 `EVT_PARSER_GAP`；
3. 在独立 Apps 目录创建可运行产品候选，新 App 接入不改变 Core 领域模型；
4. 运行可恢复、副作用幂等、高风险行动受控，状态、Evidence、Permit 和审计可追溯；
5. 完成 A/B/C 冻结任务集的首轮对照实验，并形成产品 DecisionRecord。

---

## 6. P1 产品创建与运营

### 6.1 P1 目标

让 Regent Core 在未知具体产品实现的前提下，从 Goal 和治理约束出发，自主完成证据发现、产品假设比较、需求修订、能力解析、应用生成、隔离构建、发布和观测闭环。

### 6.2 强制边界

- Core 预置治理和生成机制，不预置各种垂直 App 功能。
- 生成结果必须由版本化需求、证据引用、生成计划和文件变更集共同约束。
- 构建与发布均视为受治理副作用，必须可幂等、可恢复、可审计。
- 未知外部结果不得推定成功；必须进入 UNKNOWN 并通过查询或对账收敛。

### 6.3 P1 Graduation 验收矩阵

未满足本矩阵时产品状态为 `P1_GRADUATION_REQUIRED`，不得把剩余工作改名为 P2。

| 层级 | 含义 | 最低门槛 |
|---|---|---|
| `SYSTEM_GRADUATED` | 系统主链、构建、治理与耐久副作用可证 | G1–G5、G8–G12 签署 |
| `PRODUCT_EVIDENCE_GRADUATED` | 演进闭环可证（外部结果 → 决策 → 调整） | G6–G7 签署 |

#### SYSTEM_GRADUATED

| ID | 验收项 | 成功阈值 | 失败结果 |
|---|---|---|---|
| G1 | 事前未知 Goal | ≥3 个独立 Goal 全部到达 Decision | FAIL_KNOWN_GOAL |
| G2 | 合法外部证据 | 决策引用 EvidenceRef | FAIL_NO_EVIDENCE |
| G3 | 实质不同假设 | ≥2 假设，差异维度可机器列出 | FAIL_NO_DIFF |
| G4 | Requirement 驱动生成 | Plan 哈希含 Requirement 摘要 | FAIL_TEMPLATE |
| G5 | 可复现构建 | 哈希一致且 PASSED | FAIL_BUILD |
| G8 | Durable 副作用 | 0 重复副作用；UNKNOWN 在 15 min 内收敛 | FAIL_SIDE_EFFECT |
| G9 | 凭据治理 | 0 明文密钥 | FAIL_SECRET |
| G10 | 质量门禁 | Ruff/mypy/Pytest/迁移全绿 | FAIL_CI |
| G11 | 发布基线 | 可回滚上一 tag | FAIL_RELEASE |
| G12 | 证据包 | 100% 可解析 | FAIL_PACK |

**G0 前置（阻塞 P2）**：最小 `ExternalOperation + Permit 原子消费 + operation_key 幂等 + 恢复/对账` 必须在 G8 之前合入。

#### PRODUCT_EVIDENCE_GRADUATED

> 废除强制 ≥7 天观察窗。不要求首轮 Preview 满足用户预期；用户「不是想要的」是合法输入，必须进入 REVISE。

| ID | 验收项 | 成功阈值 | 失败结果 |
|---|---|---|---|
| G6 | 演进闭环可演示 | ≥1 次完整闭环（不满→Gate→REVISE→新 Preview）+ 1 次独立路径 | FAIL_NO_LOOP |
| G7 | 真实 Observation 决策 | ≥1 次唯一决策，所依 Observation ≥3 条合格 | FAIL_FAKE_OBS |

---

## 7. 隐私、同意与数据治理

1. **告知与同意**：收集 Observation、Evidence、对话内容前展示用途；可撤回。
2. **PII 最小化**：默认不采集敏感 PII；必要字段分级。
3. **保留**：运营日志与 Observation 默认保留期可配置；超期匿名化或删除。
4. **导出与删除**：Goal Owner 可请求 Goal 级导出包与删除；删除回执入审计。
5. **隔离**：tenant / org / project / goal 四级隔离（首发可简化为 org/project/goal）。
6. **驻留**：数据不出声明区域，除非独立批准。
7. **UNTRUSTED_DATA**：外部网页、工具输出、Generated App、Agent 消息默认不可信，不得成为指令或授权来源。

---

## 8. 北极星指标与护栏

### 8.1 北极星

```text
CostPerVerifiedSuccess
= (模型成本 + 工具成本 + 基础设施成本 + 人工成本 + 失败恢复成本)
  / 独立验证成功的 Goal 数
```

| 字段 | 冻结定义 |
|---|---|
| 窗口 | 滚动 28 天，按 UTC 对齐 |
| 最小样本量 | 分母 ≥10 个 VerifiedSuccess Goal；否则 INSUFFICIENT_EVIDENCE |
| 目标 | 相对基线 ≤100% 且全部护栏绿 |
| 决策阈值 | 连续 2 窗：北极星 > 基线×1.20 或任一护栏红 → STOP 投资评审 |

### 8.2 护栏（不可被收益抵消）

| 指标 | 红线 |
|---|---|
| 核心任务完成率 | < 70% |
| 证据不足率 | > 30% |
| 端到端 P95 延迟 | > 4h |
| 人工介入分钟/VerifiedSuccess | > 120 min |
| 重复副作用 | > 0 立即停 |
| 未对账 UNKNOWN | > 15 min 立即停 |
| 安全违规/凭据泄露 | > 0 立即停 |
| 内部流量误入产品决策 | > 0 立即停 |

---

## 9. P2 范围：承诺 / 条件承诺 / 候选

P2 将 Regent 从「单个 Goal 的可信产品闭环」扩展为「多 Goal、多运行时、可评测、可持续学习与受控生产运营平台」。

### 9.1 承诺（Committed）

| 阶段 | 摘要 | 前置 |
|---|---|---|
| P2-1 多 Goal 调度 | 并发、公平排队、优先级、抢占、无重复副作用 | G0 + G8 已签署 |
| P2-2 多 Runtime Profile | static-web-v1 / python-web-v1 / node-web-v1 等 | P2-1 DecisionRecord |
| P2-4 最小 Eval Harness | 冻结任务集、基线、盲评、统计 Gate | 先于自适应组织 |

### 9.2 条件承诺（需前序 DecisionRecord）

| 阶段 | 激活条件 |
|---|---|
| P2-3 长期记忆 | P2-1/P2-2 DecisionRecord + 安全附录门禁 |
| P2-5 自适应组织 | P2-4 统计 Gate 显示正净收益 |
| P2-6 Champion/Challenger | P2-4 Harness 可用 |

### 9.3 候选（需单独产品 DecisionRecord）

P2-7 受控生产发布 / P2-8 受监管自我改进 / P2-9 能力生态。

---

## 10. 多 Agent 评测协议（冻结要求）

对比自适应组织与**强单 Agent 基线**时必须冻结并报告：

| 维度 | 要求 |
|---|---|
| 模型 | 相同实际版本与端点 |
| Prompt/技能/工具/权限 | 集合与版本哈希一致（除组织形态差异） |
| Token | 总 Token 与缓存 Token 分列 |
| 算力 | 墙钟预算与总计算预算分别报告 |
| 人工分钟 | 计入净收益 |
| 口径 | pass@1 / pass@k 预先定义 |
| 随机性 | 种子、重复次数、95% 置信区间 |
| 惩罚 | 安全违规与不可恢复失败计入负向 |
| 隔离 | 训练/调参/测试集隔离 |

评价器：冻结 Rubric；默认盲评；机器验证优先于 LLM-as-a-Judge。
### 10.1 多 Agent 启用判定与新增冻结指标

多 Agent 是需要被证明的执行策略，不是产品目标。P2-4 Harness 必须先以同一任务、模型、工具、权限和总预算运行强单 Agent champion，再决定是否允许固定模板或自适应组织进入候选。「强单 Agent 基线」的生成质量与闭环要求见 §10.5。

组织路由至少使用以下可测任务特征：工具调用密度、子任务可分解性、顺序依赖强度、单 Agent 基线成功率、独立验证需求和并行收益上限。初始裁剪规则冻结为：

1. 单 Agent 基线成功率高于 45% 时，不默认建议多 Agent；
2. 强顺序依赖任务保持单 Agent；
3. 仅当任务可分解且需要独立验证时，才允许“中心化编排 + 独立验证”进入候选；
4. 规则只裁剪候选空间，不能替代 P2-4 的实际统计 Gate。

除 §10 既有字段外，每个候选组织还必须报告：

| 指标 | 冻结定义 | 用途 |
|---|---|---|
| `coordination_token_share` | Agent 间协调消息 Token / 总 Token | 识别协调开销吞噬执行预算 |
| `error_amplification_factor` | 注入受控上游错误后，下游错误影响量 / 注入错误量 | 识别拓扑级错误放大 |
| `dispatch_entropy` | Orchestrator 每步派工分布熵的时间序列 | 识别调度发散和失焦趋势 |

20% 的 `coordination_token_share` 为工程告警线，不是自动失败线；最终采用与否仍由 `CostPerVerifiedSuccess`、护栏和置信区间共同决定。任何阈值调整必须版本化并形成 DecisionRecord。

### 10.2 协作失败分类与可恢复行为

多 Agent Run 必须把 MAST 类结构性失败映射为稳定 `failure_code`，至少覆盖：步骤重复、提前终止、角色越界、推理与行动不一致、未请求澄清、忽略同伴输出、隐式决策冲突、缺少验证和验证者失败。

- 不确定性达到模板阈值时，Agent 必须进入澄清动作或 `WAITING_HUMAN`，不得静默继续；
- Orchestrator 的“派给谁、为什么、依据哪些证据”必须可回放；
- 失败归因用于实验、恢复和产品决策，不得以增加提示词替代状态机、验证 Gate 或角色边界。

### 10.3 固定组织模板认证

`pm-dev-independent-qa-v1` 及后续固定模板中的每个 Agent 必须冻结：

1. 角色边界与明确非职责；
2. 工具/能力 allowlist 与可委托范围；
3. 停止、澄清、失败和移交条件。

模板认证绑定完整成员清单、模型版本、Prompt/技能/工具哈希、拓扑和验证合同。替换任一成员或上述任一绑定项都产生新模板版本并触发**整体验证与整体回归**；禁止沿用旧模板认证。QA 必须与生成者身份和权限分离。

生产 `REGENT_AAR1_CERTIFIED_HIVE` opt-in 曾早于本小节整体认证合同存在；MA-2（2026-07-31）已落地成员三要素、五类 hash 摘要与回归套件后，该 flag 仅在 digests 与认证合同一致时保持有效，摘要变更须重新核验后方可继续 opt-in（见 Plan §12.6）。

### 10.4 长任务上下文产品要求

现有会话内 todo 与自动压缩能力升级为可恢复合同：

- 计划对象在 Worker 重启和上下文压缩后仍可恢复，至少保存状态、责任 Agent、依赖、完成证据和下一步；
- 大于配置阈值的工具结果写入不可变 Artifact，消息仅保留引用、哈希和短预览；
- 压缩前完整原文写入可检索 Artifact；压缩摘要必须结构化保留 Goal 意图、已产 Artifact、未决风险和下一步；
- 压缩不得删除 Permit、约束、失败码、证据引用或用户尚未解决的决定；
- **前缀稳定性与成本（2026-08-01）**：同一次 Run 内，易变的 workspace / todos / 失败缺口不得破坏可缓存的稳定前缀；默认不把文件全文塞进每轮 prompt（按需 `read_file`）。产品与运维须能观测 prompt cache 命中（`cached_tokens`），区分「模型贵」与「拼装浪费」；成本护栏未达标前不得扩大 agentic 流量。

这些能力是 Worker/Agent harness 的可靠性与成本增强，不构成启用更多 Agent 的理由。

### 10.5 单 Agent 生成闭环与质量基线

§10.1 要求多 Agent 评测前先建立「强单 Agent 基线」。该基线的质量瓶颈当前不在组织层，而在单 Agent 生成闭环未闭合：

面向用户的目标是提高首次生成可运行率，缩短从首次失败到可用交付的等待时间与修正轮次，并降低人工介入率。GQ-3 报告必须同时给出这些用户结果指标，不能只报告内部 pass rate。

- 主 Worker 必须真正根据 `generation_strategy`（`artifact-backed` / `agentic`）选择生成器，不得无条件硬编码某一种（见 Tech-Spec §13.4）；
- 注入生成器对象的实际类型必须与运行元数据 `generator_ref` 一致，禁止「标注 agentic、实际仍 artifact-backed」（见 Tech-Spec §13.4）；
- artifact-backed 路径保留下游依赖构建、真实构建与部署后浏览器/smoke 验证，但反馈发生在较晚阶段、纠错成本高；必须补充**会话内、低延迟**的「运行—读错—修改」自纠正闭环（见 Tech-Spec §13.5）；
- `VerificationAgent` 现有 `compileall` + 起服务 + 端点探测之外，应补充 pytest / 项目测试命令能力，并把真实构建、测试与 smoke 失败可靠回灌至同一次生成会话，而非仅由下游恢复流程处理（见 Tech-Spec §13.5、§13.6）；
- 将 `agentic` 设为默认前，必须以隔离影子任务或小比例 canary 对照 artifact-backed 与 agentic，在预注册的代表性冻结任务集上比较成功率、成本、延迟、首次可运行率、修正轮次和人工介入率；门槛、样本量、停止规则与安全护栏必须在实验前冻结（见 Tech-Spec §13.7、Plan §13）；
- GQ-3 / M6 canary 必须经强制控制流启用：canary 仅当 `generation_strategy_canary_gate=True`（GQ-2 反馈闭环验证后）**且** `canary_percent>0` 时，按 `stable_canary_bucket(goal_id)` 对**具体 goal** 选 `agentic` 生成器；`canary_rollout_allowed` 在策略解析中强制 GQ-2→GQ-3 顺序；**Settings 代码默认** gate=False、percent=0（见 Tech-Spec §13.7、§13.4）；
- **M6 受控 canary（产品状态，2026-08-01）**：在沙箱前置满足后，运维可打开**小比例**（当前生产 **5%**）agentic canary，**默认策略仍为 artifact-backed**。观察窗目标为 7 天或 100 新 Goal（先到为准），窗末仅可 HOLD / 扩至 10% / 回滚；**不等于 GQ-4**。soft-pass、单次 demo 或开窗前历史流量**不得**宣称 M6 出口达标。权威窗记录与观察计划：`docs/m6-canary-window-2026-08-01.json`、`docs/m6-canary-watch-plan-2026-08-01.md`；
- **GQ-3 合规前置**：agent 工具循环必须在独立 sandbox 内执行（不得在持有 DB/Provider 凭据的 worker 宿主上 `subprocess` 跑白名单命令）。不满足则**禁止**打开 canary / 影子流量，即使控制流代码已就绪（见 Tech-Spec §13.8、统一计划 CD-0）；
- 固定 Hive 的净收益目前未经真实任务实验确认，既不应假定必然改善，也不应假定必然放大；固定 Hive 与自适应组织的评估必须建立在上述强单 Agent 基线之上（见 Plan §13）。
- GQ-4 默认切换必须经强制控制流：运行对照实验后，唯有 `gq4_default_switch_gate` 判定 `PROMOTE_AGENTIC_CANDIDATE` 且无 kill switch 时，`apply_gq4_promotion` 才允许晋级；未通过则 `DomainError` 阻止翻转 `generation_strategy`。运行时默认仍由 `generation_strategy` 驱动，kill switch 始终覆盖。晋级须形成 DecisionRecord（见 Tech-Spec §13.7、Plan §13）。
- **运维 `.env` 覆盖 `REGENT_GENERATION_STRATEGY=agentic` 不等于 GQ-4 晋级**，也不得替代上述沙箱前置与实验窗；打开小比例 canary 同样不等于默认策略翻转。
- **模型 thinking 模式**属运维/成本旋钮（默认关闭），不是面向用户的功能承诺；开启须可配置且预算可观测（见 Tech-Spec）。

---

## 11. 停止投资条件

出现任一条件应触发正式 STOP 投资评审：

1. P1 Graduation 矩阵在约定窗口内不可证伪或连续失败且无可信修复计划；
2. 生产或预发出现重复外部副作用且无法证明已修复；
3. Eval 显示多 Agent 无正净收益，组织仍强推默认多 Agent；
4. 内部 Smoke / 伪 Observation 再次进入产品 Gate；
5. 凭据泄露未轮换或安全红线被突破；
6. 文档仍为 CONDITIONAL 却开工 P2 编码。

---

## 12. 非目标

- 无审批的全自动生产发布。
- 无证据的「自治公司」叙事。
- 用投票替代事实验证。
- 把长上下文、更多 Token 或更多 Agent 数量作为成功指标。
- 为单个生成 App 向 Core 添加业务专用模型。
- 未经认证的开放 Agent/Tool 市场。
- 支付系统和完整商业化后台（除非作为独立 App Goal）。
- **Tauri 桌面端**：仓库中若保留骨架，视为探索性非目标；产品交付面仍为 Core + Web Console，桌面端不计入 P0/P1/P2 验收。
- **自适应自由拓扑 Hive**：始终 `ROLLOUT_NOT_ALLOWED`，不得作为默认多 Agent。固定模板的代码配置默认值为关闭；生产当前通过 `REGENT_AAR1_CERTIFIED_HIVE=true` 在既有范围内 opt-in，并受 §10.3 / MA-2 整体认证摘要及 TaskFeatures 裁剪约束。GQ-5 前不得扩大该生产范围；这不等于自适应自由拓扑，也不改变「单 Agent champion」的产品默认叙事。
- 下列能力**已登记但未实现**（不得宣称验收）：

  - P2-4 最小 Eval Harness（仍实验骨架，非统计 Gate 就绪）；
  - P2-5 自适应组织（`ROLLOUT_NOT_ALLOWED`，须 P2-4 正净收益）；
  - GQ-4 默认切换（PENDING：DecisionRecord 未 ACCEPTED）；
  - G0 ExternalOperation **跨 provider 真实网络 query→resolve 全路径**（核心闭环已落地，仅此切片待合入）；
  - SelfImprovementRun 产品门禁（候选，`ROLLOUT_NOT_ALLOWED`）；
  - MAST 失败码生产接入（已定义 9 码，尚未接入生产分类路径）。

> **更正（2026-08-01 代码核查）**：原列于此的 **P2-3 Impact Graph、P2-5 AgentEnvelope HMAC、G0 ExternalOperation 核心闭环** 经代码核查**均已实质实现并有单测**（见 `docs/registered-unimplemented-2026-07-30.md` 与 `core/src/regent/application/impact_graph_service.py` / `envelope_v1.py` / `external_operation_service.py`），已从本"未实现"清单移除。本节此前滞后于实现，以本次核查为准。

> **迭代登记（2026-08-03 代码核查）**：2026-08-02/03 落地的混合控制平面 H0–H2（abort/permission/ask 工具/只读时间线）、Session Work Plan（W0–W4，Step-0 门禁 + 计划审批）、控制台可观测性（SSE + ProgressEvent）、Agent 内核 W4 收口（CJK token、质量门、live golden lane）、交付缺口恢复与诊断交付，均已接线生产路径；其产品语义以各决策笔记与执行计划为准（见 `docs/decision-note-*`、`docs/execution-plan-*` 2026-08-02/03）。本节"未实现"清单（P2-4 / P2-5 自适应 / GQ-4 / EO 跨 provider / SelfImprovementRun 门禁 / MAST 生产接入）维持不变。

---

## 13. 发布顺序

```text
P1 Graduation
→ P2-1 调度（承诺）
→ P2-2 Runtime（承诺）
→ CD-0：agent 沙箱合规 + 审计/门禁可信（对话式交付计划）
→ GQ-0…GQ-4：建立并晋级强单 Agent 基线（GQ-3 依赖 CD-0）
→ CD-1…CD-3：交付状态机接线 + WorkBuddy 级交付体验
→ CD-4：对话层 agent loop（§4.4，独立需求批次）
→ MA-5 / P2-4：以强单 Agent 为 champion 运行组织对照实验
→ GQ-5 / MA-6：固定 Hive 重评与条件激活 DecisionRecord
→ P2-3 记忆（条件） / P2-5 自适应组织（条件） / P2-6 实验平台（条件）
→ P2-7…P2-9（候选）
```

每个激活阶段必须有冻结验收合同和唯一 DecisionRecord。后续阶段不得掩盖前置证据缺口。
---

## 附录 A：2026-07-16 历史快照（非权威）

以下内容仅供版本追溯，不构成当前需求、验收或发布依据；发生冲突时，以本文件附录之前的 CURRENT 正文为准。

# Regent 产品需求文档

> 状态：ARCHIVED 历史快照（非权威）
> 日期：2026-07-16

## 1. 产品定义

Regent 接收自然语言目标，在用户授权、资源、约束和治理边界内，自主解释目标、发现与补齐能力、组建人机组织、创建并运营独立应用，并根据外部证据持续调整计划与组织。

用户唯一必填输入是自然语言 Goal。附件、期限、资源、约束和偏好均为可选。系统内部可以生成 GoalSpec，但不要求用户定义组织结构或解决方案。

Regent 的 P0 产品核心是“可靠、受治理的目标执行内核”。系统默认使用单 Agent；动态组织是需要通过对照实验验证的候选增益机制，不是预设成立的产品前提。

## 2. 产品边界

```text
regent/
├─ core/   # 通用自治组织运行内核
└─ apps/   # Regent 创建和运营的独立应用
```

Core 只表达目标、能力、组织、工作、执行、证据、策略和资源。每个 App 拥有独立源码、依赖、数据、测试与部署，可以脱离 Core 运行。具体内容类型、订阅方式和业务指标不得成为 Core 领域对象。

## 3. P0 能力

1. 保存原始 Goal，生成版本化 GoalSpec，区分显式约束、系统推断和未知项；
2. 持久化 Goal、Work、Run、Artifact、Evidence、HumanTask 和 Audit，进程重启后恢复；
3. 从 Goal 与计划推导能力需求，区分能力、权限、资源、信息缺口和普通失败；
4. 通过复用、配置、组合、构建或请求人类补齐能力；
5. 根据能力覆盖、并行收益、隔离、成本与风险组建最小组织，并能收缩、替换和解散；
6. 在 `apps/<app-id>` 创建、构建和测试独立 App，不向 Core 引入业务模型；
7. 使用外部 Observation 和 Evidence 评价进展并重规划；
8. 所有副作用行动经过策略判断和一次性 ExecutionPermit；
9. 人工输入与审批使用独立 HumanTask，等待期间不占 Worker；
10. 支持暂停、恢复、取消、预算限制、无进展停止和完整审计。

P0 保持整体交付，不拆分为独立版本。开发顺序可以分切片推进，但 P0 只有在第 9 节全部条件满足后才算完成。

## 4. 状态语义

不同层级使用不同完成语义：

- Goal `ACHIEVED`：目标成功条件已有充分证据；
- Work `ACCEPTED`：逻辑工作成果已通过独立验收；
- Run `EXECUTED`：一次执行正常返回，但成果尚不一定被接受。

Goal 其他终态：

- `EXHAUSTED`：当前硬约束和资源上限内已无可行路径；
- `FAILED`：发生不可恢复的系统或状态完整性错误；
- `CANCELLED`：Goal Owner 主动终止。

`PAUSED`、`WAITING_HUMAN` 和 `BLOCKED` 是三种不同的可恢复状态：

- `PAUSED` 只能由用户 `resume`；
- `WAITING_HUMAN` 由 HumanTask 完成、超时或升级策略唤醒；
- `BLOCKED` 由资源、环境、授权变化或重规划唤醒。

## 5. Work 与 Run

Work 是计划中的逻辑工作单元，保存目的、输入引用、验收标准、依赖、优先级和预算。Run 是执行某个 Work 的一次不可变尝试，绑定实际执行者、模型、工具、输入版本和 Permit。

```text
Goal 1 ──* Work 1 ──* Run
```

一个 Work 可以产生多个 Run，但同一时刻最多一个活动 Run。重试或更换执行方式必须新建 Run，历史 Run 不覆盖。Run `EXECUTED` 后，Evaluator 接受 Evidence，Work 才进入 `ACCEPTED`。

## 6. 固定 P0 验收

```text
名称：CSV_SUMMARY_BASELINE
Goal：读取授权目录中的 orders.csv，生成 summary.json。
数据：1,12.50 / 2,7.50 / 3,INVALID / 4,10.00
约束：禁止联网；不得修改输入；只能写入 output/。
输出：{"row_count":4,"valid_count":3,"invalid_count":1,"total_amount":30.0}
```

自动验收必须证明：原始 Goal 保存；约束与推断分离；形成 Work 和 Run；输出逐字段相等；Evidence 包含输入与输出哈希；Worker 中断后恢复；幂等重放不产生第二份输出；Run 为 `EXECUTED`、Work 为 `ACCEPTED`、Goal 为 `ACHIEVED`。

## 7. 测量与产品决策

P0 必须执行冻结的对照实验，而不能仅以功能演示判断动态组织价值。详细实验协议与指标定义以 [Regent-Measurement-Decision-Framework.md](./Regent-Measurement-Decision-Framework.md) 为准。

比较三种执行模式：

- A：强单 Agent；
- B：固定组织模板；
- C：动态组织。

三组必须使用相同模型、初始能力、输入、预算、时间上限和验收标准。核心指标包括端到端成功率、独立质量、完成时间、模型与工具成本、人工负担、安全事件、能力缺口识别质量、协调开销和能力复用率。

产品决策规则：

- C 达到冻结的净收益门槛：继续投资动态组织；
- 能力补齐有效但 C 连续两轮不优于 A：转向“强单 Agent + 受治理能力工厂”；
- B 与 C 表现接近：采用少量固定组织模板；
- 能力缺口无法稳定识别、能力无法独立验证，或复用价值持续不足：停止通用化扩展，转向垂直产品。

这些是必须由数据验证的产品假设，不是 P0 功能存在即可证明的结论。

## 8. 首批长期目标

- 创建并运营面向 AI 从业者的产品，第一阶段达到 100 个有效付费用户；
- 创建并运营面向成年人的短篇童话网站，第一阶段达到 10,000 有效 DAU。

两者从同一 Goal API 进入。业务指标来自外部可验证数据，不作为 P0 编码完成条件，也不得反向固化 Core。

“有效付费用户”的权威来源是支付流水、退款状态、账号与有效使用事件；测试支付、已退款订单、内部账号和欺诈交易不计入。指标口径必须版本化。

“有效 DAU”必须是去重真实用户并满足最低有效阅读行为，排除爬虫、内部账号、自动流量、异常设备/IP 和流量农场。原始事件需要服务端签名或等价防篡改机制、幂等 ID 和 Bot 检测。

长期目标启动时必须另外冻结时间窗口、数据源、归因规则和阶段 Gate。童话网站在 100、1,000、3,000 有效 DAU Gate 完成质量、安全与留存复核后，才扩大到 10,000 有效 DAU。

## 9. P0 完成定义

P0 作为整体完成，必须同时满足：

1. Core 在空 Apps 条件下通过 `CSV_SUMMARY_BASELINE`；
2. 仅凭普通 Goal 形成可解释的最小组织，补齐至少一个能力缺口，并通过 `EVT_PARSER_GAP`；
3. 在独立 Apps 目录创建可运行产品候选，新 App 接入不改变 Core 领域模型；
4. 运行可恢复、副作用幂等、高风险行动受控，状态、Evidence、Permit 和审计可追溯；
5. 完成 A/B/C 冻结任务集的首轮对照实验，并依据预先冻结的门槛形成继续、转向或停止的产品 DecisionRecord。

P0 完成证明 Regent 具备受治理的目标执行、能力补齐和应用创建能力；它不自动证明动态组织优于单 Agent。动态组织是否成为核心卖点，必须服从对照实验结果。

## P1：Core 自主生成应用

P1 的产品目标是让 Regent Core 在未知具体产品实现的前提下，从 Goal 和治理约束出发，自主完成证据发现、产品假设比较、需求修订、能力解析、应用生成、隔离构建、发布和观测闭环。

### 强制边界

- Core 预置治理和生成机制，不预置各种垂直 App 功能。
- AI 业内人员 App 是首个验证对象，不是 Kernel 模块。
- 生成结果必须由版本化需求、证据引用、生成计划和文件变更集共同约束。
- 构建与发布均视为受治理副作用，必须可幂等、可恢复、可审计。
- 未知外部结果不得推定成功；必须进入 UNKNOWN 并通过查询或对账收敛。

### P1 整体验收

输入一个目标和约束后，Core 能生成可运行 App，在隔离环境通过验证，发布预览，采集真实使用信号，并输出 CONTINUE、REVISE 或 STOP 决策及完整证据链。P1 保持整体交付，不拆成子版本。