# Regent 产品需求文档 v2

> 状态：CURRENT  
> 性质：权威执行基线（Owner 批准升 CURRENT）  
> 日期：2026-07-22（升 CURRENT：2026-07-23）  
> 取代：`Regent-PRD.md` 中的当前产品定义；旧文件仅作历史参考  
> 配套技术规范：`Regent-Technical-Spec-v2.md`  
> 编码门禁：`P2StartDecisionRecord` 已签署 → **允许** `p2-scheduler-01`  
> 测量框架：`Regent-Measurement-Decision-Framework.md`（须与本文件及 P2-4 对齐）

## 复审意见摘要

### 首轮（2026-07-22）

```text
产品方向：批准｜架构原则：批准｜P2 路线：有条件批准｜P2 编码：不批准
```

### 二次交叉复审（2026-07-22）

| 审批项 | 结论 |
|---|---|
| PRD / Tech Spec / 附录升级为 `CURRENT` | **已批准**（2026-07-23 Owner） |
| P2 路线与阶段顺序；P2-4 在自适应组织之前 | 批准 |
| `p2-scheduler-01+` 开工 | **已批准**（见 P2StartDecisionRecord） |
| P1 Graduation / G0 收尾（含 Durable External Effects） | **可以继续** |

推荐准入顺序（冻结）：

```text
A. 定义保护（规范源 + CI 哈希）
→ B. 文档剩余阻塞关闭
→ C. P1/G0 Durable External Effects
→ D. 故障注入与 G8
→ E. SYSTEM_GRADUATED
→ F. PRODUCT_EVIDENCE_GRADUATED
→ G. GraduationDecisionRecord
→ H. 文档升级 CURRENT
→ I. P2StartDecisionRecord
→ J. p2-scheduler-01
```

---

## 0. 共同保留原则

以下原则完整保留，不得在修订中削弱：

1. **当前产品创建场景**的价值路径是**目标执行证据链**（GoalSpec→…→Decision），不是一次性页面生成；该路径是阶段性实现，**不是** Regent 产品身份定义（身份见 §1.1 / `REGENT-DEFINITION-1.0`）。
2. 证据优先、需求权威、最小组织、失败关闭。
3. Generated App 与 Core 分离。
4. PostgreSQL、Outbox、不可变 Artifact、Permit、UNKNOWN 对账。
5. 内部 Smoke 不得满足产品价值 Gate。
6. Provider 不得注入缺失的业务功能。
7. 多 Agent 不是默认架构，必须通过冻结实验验证净收益。
8. 投票不能替代机器验证或真实 Observation。
9. 生成者不能自行评价、批准和发布自己的成果。
10. 禁止用源码字符串、类名存在或伪 Observation 代替行为验收。

### 0.1 永久产品定义与变更门禁

规范性定义的**唯一机器可读源**为 [`docs/definitions/REGENT-DEFINITION-1.0.txt`](docs/definitions/REGENT-DEFINITION-1.0.txt)，内容哈希见同目录 `.sha256`。CI 在定义 ID/哈希漂移、或出现第二规范副本时阻止合并。

本文件第 1.1 节**引用并逐字展示**该源，不得另写同义“新定义”。

---

## 1. 产品使命与差异化

### 1.1 Regent 永久定义

> **规范源**：[`docs/definitions/REGENT-DEFINITION-1.0.txt`](docs/definitions/REGENT-DEFINITION-1.0.txt)（`REGENT-DEFINITION-1.0`，冻结，不得修改）  
> Regent 接收人的自然语言 Goal，在明确的约束、资源、授权和治理边界内，自主解释目标，发现并补齐能力，形成当前最合适的最小人机组织，创建并运营可脱离 Core 独立运行的应用，并依据可验证的外部结果持续调整计划、能力和组织，直至目标达成、资源耗尽、不可恢复失败或被 Goal Owner 取消。

这一定义包含且仅包含以下不可分割的恒定属性（全文以规范源为准）：

1. **Goal 驱动**：唯一必需的用户输入是自然语言 Goal；结构化 GoalSpec 是 Regent 的解释结果，而不是用户必须预先完成的产品需求。
2. **边界内自治**：自治始终受约束、资源、授权、治理和人工接管约束；自治不等于无限权限或无人负责。
3. **能力可演进**：Regent 不只调用预置工具；它必须能够发现能力缺口，并按复用、配置、组合、构建或请求人类的顺序补齐能力。
4. **组织为执行手段**：Regent 根据目标形成、调整和解散最小人机组织；单 Agent、固定团队或多 Agent 都只是候选实现，不是产品定义或预设结论。
5. **独立 App 是目标成果**：生成的 App 拥有独立源码、依赖、数据、测试、部署和生命周期，可以脱离 Regent Core 运行；业务场景不得被反向固化进 Core。
6. **外部结果闭环**：Regent 依据可验证的外部 Evidence 与 Observation 调整计划、能力和组织，而不是以生成者自评、内部 smoke 或多数投票代替目标结果。
7. **明确终止**：持续调整不是无限运行；系统必须在成功、耗尽、失败或取消时形成有证据的终态。

变更门禁：后续材料只能引用或解释；ICP/阶段/指标可演进但不是定义；若必须改定义则新建定义 ID，禁止原地修改或追溯改写；冲突时改文档/实现，不改定义。

### 1.2 当前阶段的产品使命

当前阶段把永久定义落实为一个可治理、可审计、可恢复的自主目标执行内核。P1/P2 聚焦产品创建与运营场景：获取证据、比较产品假设、冻结需求、补齐能力、生成和验证独立应用、发布受控版本，并根据真实使用证据形成继续、修订或停止决策。这是当前验证路径，不是对 Regent 永久定义的替代。

**当前差异化**：竞品与内部脚本多停留在「生成可打开的页面」；Regent 把未知目标转化为一条可追溯、可验证、可迭代的**目标执行证据链**。产品创建场景当前表现为 GoalSpec → Evidence → Hypothesis → Requirement → Build → Preview → Observation → Gate → Decision；这条链是定义的阶段性实现，不是 Regent 本身的定义。

---

## 2. ICP、购买者、主用户与首发场景

### 2.1 首发范围（冻结）

> 低风险、无敏感数据的内部工具或 Web MVP 产品验证。

**首批支持**：内部运营工具、内容/资讯类 MVP、简单表单与工作流原型、静态或轻后端 Web 演示产品。

**明确排除或要求独立治理阶段**：医疗诊断、金融交易、招聘/信贷决策、未成年人数据、高风险基础设施控制、大规模 PII 处理、支付清算。

### 2.2 第一批核心用户（ICP）

| 角色 | 是谁 | 痛点 |
|---|---|---|
| 购买者（Buyer） | 小型产品团队负责人、创业者、内部创新负责人 | 需要可控成本地把想法推进到可验证 MVP，而不是无限 Prompt 试错 |
| 日常操作者（Operator） | 产品经理 / 技术负责人兼任 | 需要看见阻塞原因、批准高风险动作、组织真实用户验证 |
| Goal Owner | 提出意图并确认 GoalSpec 的人 | 常与 Buyer 同一人；对约束与成功标准负责 |
| Product User | 生成 App 的真实外部或内部试用用户 | 其行为才是产品证据，不是开发者自测 |

### 2.3 首个高频问题

「我有一个模糊的产品想法，怎样在预算内得到一个**可被独立验证**的 Preview，并根据真实使用决定继续、修订还是停止？」

### 2.4 当前替代方案

手工写原型、无治理的 AI 编码助手、外包一次性交付、内部 Wiki + 脚本拼装。这些方案缺少：统一证据链、Permit 治理、UNKNOWN 对账、独立评价与可证伪 Graduation。

### 2.5 为什么选择 Regent

- 目标 → 证据 → 决策闭环，而非只生成代码；
- 失败关闭与副作用治理，适合「还不能全自动上生产」的团队；
- App 与 Core 分离，避免把垂直业务吸进平台内核。

---

## 3. RACI

| 活动 | Goal Owner | Operator | Capability Provider | Product User | 独立评价者 | 安全/合规签署 |
|---|---|---|---|---|---|---|
| 确认 GoalSpec | A/R | C | I | I | I | C（高风险场景） |
| 授权外部证据源 / Permit | A | R | C | I | I | C |
| Discovery / Generation 执行 | I | C | C | I | I | I |
| Preview 核心任务验收 | C | R | I | A（完成任务） | C | I |
| Gate / IterationDecision | A | R | I | I | R（盲评时） | C |
| 生产发布批准 | A | R | I | I | I | VETO（可否决，≠第二 A） |
| 能力包认证与撤销 | I | C | R | I | C | A |
| P1 Graduation 签署 | C | R | I | C | R | A |

图例：R=执行，A=问责（每项活动唯一），C=咨询，I=知会，VETO=独立否决权（不等于第二 A）。

---

## 4. 完整成功与失败用户旅程

每一步必须向用户展示：**当前状态解释**、**已有证据引用**、**下一步可操作命令**（确认 / 修订 / 批准 / 暂停 / 取消 / 导出）。

### 4.1 成功路径（摘要）

1. 创建意图 → 可解释 GoalSpec（显式约束、推断、未知项、非目标）。
2. 用户确认 → FROZEN GoalSpec → Start。
3. Evidence → 实质不同假设 → 唯一 HypothesisDecision。
4. Requirement → Capability Resolution → Generation → 隔离 Build → Preview。
5. 真实 Observation → Gate → CONTINUE / REVISE / STOP。

### 4.2 失败与例外路径（必须产品化）

| 场景 | 用户可见结果 | 下一步 |
|---|---|---|
| GoalSpec 需修订 | SUPERSEDED 旧版，新 DRAFT | 编辑后重新冻结 |
| Evidence 不足或冲突 | RESEARCH_MORE / BLOCKED + 缺口说明 | 授权来源、绑定能力、或停止 |
| 人工审批节点 | WAITING_HUMAN + HumanTask | 批准 / 拒绝 / 超时 |
| BLOCKED / UNKNOWN / 超预算 | 明确 failure_code 与解释 | 对账、扩预算、取消 |
| 暂停 / 恢复 / 取消 | PAUSED / ACTIVE / CANCELLED | Operator 命令；取消不回滚已发生副作用，rollback 为新操作 |
| Preview 拒绝 | 用户拒绝或 Journey 失败 | REVISE 或 STOP；保留证据 |
| 数据导出与删除 | 导出包 / 删除回执 | 见 §6 |
| Owner 与 Operator 交接 | 角色变更审计 | 新 Actor 重新授权关键 Permit |

### 4.3 从真实使用到迭代（保留）

版本化指标绑定 → 签名幂等 Observation → 排除不合格流量 → GateEvaluation → 唯一 Decision；REVISE 创建诊断 Work。

---

## 5. P1 Graduation 验收矩阵

未满足本矩阵时产品状态为 `P1_GRADUATION_REQUIRED`，不得把剩余工作改名为 P2。  
Graduation 拆为两层，**不得**用单 Goal / 单用户 / 单 Journey 同时宣称两层通过：

| 层级 | 含义 | 最低门槛 |
|---|---|---|
| `SYSTEM_GRADUATED` | 系统主链、构建、治理与耐久副作用可证 | G1–G5、G8–G12 签署 |
| `PRODUCT_EVIDENCE_GRADUATED` | **演进闭环**可证（外部结果 → 决策 → 调整），非「首轮 App 已达用户预期」 | G6–G7 按下方签署 |

**阶段分界（冻结读法，对齐 `REGENT-DEFINITION-1.0` 属性 6/7）**

| 层 | 本阶段是否收敛 | 说明 |
|---|---|---|
| 流程诚信（SYSTEM） | 是 | 能跑、可审计、耐久副作用 |
| 演进闭环（PRODUCT 最低） | 是 | 不满意/失败可观测并产生 REVISE（或明确 STOP），且可再进入发现/生成 |
| 结果质量 / 体验达预期 | **否** | 由多轮 REVISE 与后续测量窗承担；**禁止**因「短期未达预期」永久卡在 P1 |

两层均通过后写入唯一 `GraduationDecisionRecord`；另需唯一 `P2StartDecisionRecord` 才允许 `p2-scheduler-01`。

### 5.1 SYSTEM_GRADUATED

| ID | 验收项 | 输入与测试集 | Evidence 要求 | 样本/窗口 | 成功阈值 | 排除规则 | 证据 Artifact | 独立评价者 | 失败结果 | 签署 |
|---|---|---|---|---|---|---|---|---|---|---|
| G1 | 事前未知 Goal | 冻结「已知集」外 **≥3** 个独立 Goal；统一 Start | 每 Goal ≥1 非 declared-intent | 14 天内完成 | ≥3/3 到达可查询 Decision | fixture/已知 Goal 不计 | GoalSpec、事件链 | Operator+评价者 | FAIL_KNOWN_GOAL | 产品 |
| G2 | 合法外部证据 | Goal 授权或能力包源 | ≥2 独立 sourced-observation，或 RESEARCH_MORE→能力恢复可审计 | 每 Goal 至少 1 轮 | 决策引用 EvidenceRef | Core 硬编码产品 feed 不计 | Snapshot 哈希、审计 | 技术 | FAIL_NO_EVIDENCE | 技术 |
| G3 | 实质不同假设 | 同轮 ≥2 ProductHypothesis | 每假设 ≥1 独立证据 | 每 Goal 1 轮 | 差异维度可机器列出 | 仅文案改写不计 | Hypothesis+Decision | 评价者盲评 | FAIL_NO_DIFF | 产品 |
| G4 | Requirement 驱动生成 | RequirementRevision→GenerationPlan | build-verification | 每 Goal 1 次 | Plan 哈希含 Requirement 摘要 | 模板硬编码业务不计 | Plan Artifact | 技术 | FAIL_TEMPLATE | 技术 |
| G5 | 可复现构建 | 隔离 Build+锁文件 | Report、依赖哈希、SBOM | 同输入重放 1 次 | 哈希一致且 PASSED | passthrough 不计 | Report+SBOM | 技术 | FAIL_BUILD | 技术 |
| G8 | Durable 副作用 | **G0 ExternalOperation** 故障剧本：Worker 杀进程、重复投递、响应丢失、UNKNOWN 对账 | ExternalOperation+Permit 审计 | 规定剧本各 ≥1 次 | **0 重复副作用**；UNKNOWN 在 15 min 内进入 RECONCILING 或 MANUAL_REVIEW | 无 ExternalOperation 的“口头对账”不计 | 故障注入报告 | 技术 | FAIL_SIDE_EFFECT | 技术 |
| G9 | 凭据治理 | 仓库+镜像扫描 | — | 发布前 | 0 明文密钥 | — | 扫描+轮换回执 | 安全 | FAIL_SECRET | 安全 |
| G10 | 质量门禁 | Ruff/mypy/Pytest/迁移 | — | 合并前 | 全绿 | 跳过钩子不计 | CI 日志 | 技术 | FAIL_CI | 技术 |
| G11 | 发布基线 | Git tag+可回滚部署 | — | 1 次 | 可回滚上一 tag | — | Release 清单 | 技术 | FAIL_RELEASE | 技术 |
| G12 | 证据包 | 上述 Artifact | 索引清单 | 评审时 | 100% 可解析 | — | DoD Pack | 三方 | FAIL_PACK | 会签 |

**G0 前置（阻塞 P2）**：最小 `ExternalOperation + Permit 原子消费 + operation_key 幂等 + 恢复/对账` 必须在 G8 之前合入主链；**不得**推迟到 P2-1 Scheduler。

### 5.2 PRODUCT_EVIDENCE_GRADUATED

> **读法（Owner 校准 2026-07-22）**：Regent 要的是**不断进化**（外部结果 → 决策 → 调整），不是「等满日历再毕业」。  
> **废除**强制 ≥7 天观察窗作为 PRODUCT 门槛。  
> **不要求**首轮 Preview/App 满足用户预期；用户「不是想要的」是合法输入，必须进入 **REVISE**（或明确 STOP）。

| ID | 验收项 | 输入与测试集 | Evidence 要求 | 样本/窗口 | 成功阈值 | 排除规则 | 证据 Artifact | 独立评价者 | 失败结果 | 签署 |
|---|---|---|---|---|---|---|---|---|---|---|
| G6 | 演进闭环可演示 | 冻结 Journey/拒绝路径 ≥2 条；参与者含非开发（含内部员工） | product-observation（含 **product_rejection**）或 Journey/拒绝报告 | **无日历下限**；以可查询闭环轮次为准（可同日多次） | **≥1** 次完整闭环：真实不满/失败 → Gate → **REVISE** → 新 Discovery/Preview 可查询；另至少 **1** 次独立路径（成功 Journey **或** 另一次拒绝→REVISE） | 开发者自测、仅 HTTP 200、内部 bot、**单次点击假 CONTINUE** 不计；「用户口头抱怨未入系统」不计 | Rejection/REVISE 包 + Preview 对照 | 评价者 | FAIL_NO_LOOP | 产品 |
| G7 | 真实 Observation 决策 | 签名 Observation | 非 internal/bot/test | 与 G6 同一证据集 | ≥1 次唯一 CONTINUE/**REVISE**/STOP，且所依 Observation ≥3 条合格（拒绝类 Observation 计入） | 内部 smoke 不计；无 Observation 的口头决策不计 | Observation+Gate+Decision | 评价者 | FAIL_FAKE_OBS | 产品+技术 |

样本不足时 Gate/Graduation **必须**返回 `INSUFFICIENT_EVIDENCE`，不得降级为 PASSED。  
**明确非门槛**：强制等待 N 天、「体验已达预期」、用户满意率、北极星增长——后三者属长期演进与 §7 测量窗，**不阻塞**本层签署。  
**长期演进**：PRODUCT 毕业后，持续 REVISE/CONTINUE 循环本身即运营常态；不以「再等一个日历窗」代替进化。

---

## 6. 隐私、同意与数据治理

首发默认**单租户 / 单区域**部署；多区域为候选路线图。

1. **告知与同意**：收集 Observation、Evidence、对话内容前展示用途；可撤回。
2. **PII 分类与最小化**：默认不采集敏感 PII；必要字段分级（公开 / 内部 / 受限）。
3. **保留**：运营日志与 Observation 默认保留期可配置；超期匿名化或删除。
4. **导出与删除**：Goal Owner 可请求 Goal 级导出包与删除；删除回执入审计。
5. **隔离**：tenant / org / project / goal 四级隔离（首发可简化为 org/project/goal，tenant=单例）。
6. **驻留**：数据不出声明区域，除非独立批准。
7. **UNTRUSTED_DATA**：外部网页、工具输出、Generated App、Agent 消息、Candidate Memory 默认不可信，不得成为指令或授权来源（见技术规范）。

---

## 7. 北极星指标与护栏（可执行冻结）

责任人：产品 Owner（北极星与护栏决策）；技术 Owner（账本与数据采集）；评价者（分母确认）。  
证据不足：任一指标样本未达最小样本量 → 报告 `INSUFFICIENT_EVIDENCE`，**禁止**用该窗做晋级/STOP 以外的“优化已验证”声称。

### 7.1 北极星

```text
CostPerVerifiedSuccess
= (模型成本 + 工具成本 + 基础设施成本 + 人工成本 + 失败恢复成本)
  / 独立验证成功的 Goal 数
```

| 字段 | 冻结定义 |
|---|---|
| 分子 | 窗口内归属目标 org 的模型账单+工具+基础设施分摊+人工工时成本+失败恢复工单成本（同一 `price_book_version`） |
| 分母 | Gate PASSED **且** G6 口径 Journey 由独立评价者确认的 Goal 数 |
| 数据源 | BudgetLedger、Provider 账单导出、人工工时单、Gate/Journey Artifact |
| 窗口 | 滚动 **28 天**，按 UTC 对齐 |
| 最小样本量 | 分母 **≥10** 个 VerifiedSuccess Goal；否则 `INSUFFICIENT_EVIDENCE` |
| 基线窗口 | `PRODUCT_EVIDENCE_GRADUATED` 签署后第一个完整 28 天窗；基线值写入 `MetricBaselineRecord` |
| 目标 | 相对基线 **≤100%**（持平或下降）且全部护栏绿 |
| 决策阈值 | 连续 **2** 个完整窗：北极星 > 基线×1.20 **或** 任一护栏红 → 强制 STOP 投资评审 |
| 责任人 | 产品 Owner 发起评审；安全/技术会签 |

### 7.2 护栏（不可被收益抵消）

| 指标 | 定义 | 最小样本 | 红线 | 责任人 |
|---|---|---|---|---|
| 核心任务完成率 | G6 成功 Journey / 尝试 Journey | ≥20 次尝试/窗 | < **70%** | 产品 |
| 证据不足率 | INSUFFICIENT_EVIDENCE 或 RESEARCH_MORE 滞留>24h 的 Goal / 启动 Goal | ≥10 Goal/窗 | > **30%** | 产品+技术 |
| 端到端 P95 延迟 | Start→可查询 Decision 或 Preview 就绪（取合同点） | ≥10 次成功路径 | > **4h** | 技术 |
| 人工介入分钟/VerifiedSuccess | HumanTask+审批分钟 / 分母 | 与北极星同 | > **120 min** | 产品 |
| 重复副作用 | 同 operation_key 产生两次有意义外部效果 | 全量 | **>0 立即停** | 技术 |
| 未对账 UNKNOWN | DISPATCHING/UNKNOWN 超 **15 min** 未 RECONCILING/终态 | 全量 | **>0 超时立即停** | 技术 |
| 安全违规/凭据泄露 | 越权、泄漏、PI 逃逸成功 | 全量 | **>0 立即停** | 安全 |
| 内部流量误入产品决策 | internal/bot/test 进入 Gate PASSED | 全量 | **>0 立即停** | 技术 |

### 7.3 多 Agent 风险调整净收益（组织晋级用）

```text
风险调整净收益
= 质量提升价值
- 模型与工具成本
- 延迟成本
- 人工介入成本
- 协调开销
- 安全与错误风险成本
```

只有冻结实验显示净收益为正，才允许把多 Agent 策略晋级为默认策略。

---

## 8. P2 范围：承诺 / 条件承诺 / 候选

P2 将 Regent 从「单个 Goal 的可信产品闭环」扩展为「多 Goal、多运行时、可评测、可持续学习与受控生产运营平台」。  
**不得**将全部阶段称为无条件已承诺。激活规则：条件承诺与候选阶段必须由**前一阶段 DecisionRecord**（或显式 P2Start/阶段门）激活。

### 8.1 承诺（Committed）

在 Graduation + 文档 CURRENT + `P2StartDecisionRecord` 之后开工：

#### P2-1 多 Goal 调度与资源治理

多 Goal 并发、公平排队、优先级与防饥饿；组织级预算与配额；暂停/恢复/取消/抢占；Worker 故障重分配且无重复副作用。  
**前置**：G0 Durable External Effects 与 G8 故障注入已签署。  
成功标准：20 个并发 Goal 在预算内运行，无重复副作用；高优先级可按策略抢占；资源不足明确 BLOCKED。

#### P2-2 多 Runtime Profile

`static-web-v1`、`python-web-v1`、`node-web-v1`、`python-data-v1`；版本与认证；fail-closed。  
成功标准：至少三类不同 App 通过隔离构建与核心任务验证；旧 Profile 可回放。

#### P2-4 最小 Eval Harness

冻结任务集、强单 Agent 基线、预算账本、盲评、统计 Gate、可复现种子；**先于**自适应组织。  
成功标准：同一实验可复现；墙钟与总计算预算分列报告；无额外并发算力冒充低延迟。

### 8.2 条件承诺（Conditional — 需前序 DecisionRecord）

| 阶段 | 激活条件 | 摘要 |
|---|---|---|
| P2-3 长期记忆 | P2-1/P2-2 相关 DecisionRecord 允许，且安全附录 Memory 门禁就绪 | Admission→…→Revalidation；不得覆盖硬约束 |
| P2-5 自适应组织 | **P2-4** 统计 Gate 显示相对强单 Agent 正净收益的 DecisionRecord | 否则回退单 Agent |
| P2-6 Champion/Challenger | P2-4 Harness 可用 + 明确实验 DecisionRecord | 完整实验平台 |

### 8.3 候选（Candidate — 非承诺）

- **P2-7** 受控生产发布  
- **P2-8** 受监管自我改进  
- **P2-9** 能力生态  

以及：多区域驻留、公共能力市场、支付/商业化后台（独立 App Goal）、垂直行业治理包。  
候选进入承诺须单独产品 DecisionRecord，不得因“路线图上有编号”自动开工。

---

## 9. 停止投资条件

出现任一条件应触发正式 STOP 投资或阶段回退评审：

1. P1 Graduation 矩阵在约定窗口内不可证伪或连续失败且无可信修复计划；
2. 生产或预发出现重复外部副作用且无法证明已修复；
3. Eval 显示多 Agent 无正净收益，组织仍强推默认多 Agent；
4. 内部 Smoke / 伪 Observation 再次进入产品 Gate；
5. 凭据泄露未轮换或安全红线被突破；
6. 文档仍为 CONDITIONAL 却开工 P2 编码。

---

## 10. 多 Agent 评测协议（冻结要求）

对比自适应组织与**强单 Agent 基线**时必须冻结并报告：

| 维度 | 要求 |
|---|---|
| 模型 | 相同实际版本与端点 |
| Prompt / 技能 / 工具 / 权限 | 集合与版本哈希一致（除组织形态差异） |
| Token | 总 Token 与缓存 Token 分列 |
| 推理强度 | 固定档位 |
| 工具调用次数 | 记录上限与实测 |
| 算力 | **墙钟预算**与**总计算预算**分别报告；禁止用额外并发制造表面低延迟 |
| 人工分钟 | 计入净收益 |
| 口径 | pass@1 / pass@k 预先定义 |
| 随机性 | 种子、重复次数、95% 置信区间 |
| 惩罚 | 安全违规与不可恢复失败计入负向 |
| 隔离 | 路由器训练、调参集与测试集隔离 |

评价器：冻结 Rubric；默认盲评（看不到 Agent 身份与组织形式）；不得改测试/指标/排除规则；机器验证优先于 LLM-as-a-Judge；保存 evaluator model、prompt hash、工具与校准版本；低置信度或冲突升级人工。

---

## 11. 非目标

- 无审批的全自动生产发布；
- 无证据的「自治公司」叙事；
- 用投票替代事实验证；
- 把长上下文、更多 Token 或更多 Agent 数量作为成功指标；
- 为单个生成 App 向 Core 添加业务专用模型；
- 未经认证的开放 Agent/Tool 市场；
- 支付系统和完整商业化后台（除非独立 App Goal）。

---

## 12. 发布顺序（与 §8 承诺层级一致）

```text
P1 Graduation
→ P2-1 调度（承诺）
→ P2-2 Runtime（承诺）
→ P2-4 Eval Harness（承诺；可与记忆并行规划，但组织依赖其 Gate）
→ P2-3 记忆（条件） / P2-5 自适应组织（条件，依赖 P2-4） / P2-6 实验平台（条件）
→ P2-7…P2-9（候选，需单独 DecisionRecord）
```

每个激活阶段必须有冻结验收合同和唯一 DecisionRecord。后续阶段不得掩盖前置证据缺口。
