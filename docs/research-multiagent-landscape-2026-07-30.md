# Multi-Agent 方向调研与 Regent 借鉴清单

> 状态：RESEARCH（非执行基线）  
> 日期：2026-07-30  
> 性质：外部框架与研究调研 + 结合项目目标的吸收建议  
> 对照基线：`Regent-PRD.md`（§0.3 / §10 / §12）、`Regent-Technical-Spec.md`（§4.6 / §5 / §17 / §18）

---

## 1. 调研范围

- 主流 multi-agent 框架 2026 年中现状（LangGraph、CrewAI、Microsoft Agent Framework、OpenAI Agents SDK、Google ADK、Claude Agent SDK、Deep Agents、Mastra、PydanticAI）。
- 最新研究：Agent 系统扩展定律、失败分类学、Orchestrator 过程评估、协作基准。
- 协议标准化：MCP + A2A（Linux Foundation 治理）。
- 长任务工程模式：agent harness、上下文工程、持久执行。

---

## 2. 框架格局（2026-07）

| 框架 | 定位 | 关键事实 |
|---|---|---|
| LangGraph | 状态图编排运行时 | 生产复杂工作流默认选择；检查点、持久化、time-travel debug、图中断实现 HITL |
| Deep Agents（LangChain） | 「Agent Harness」 | 内置计划工具、子 Agent 隔离上下文、虚拟文件系统卸载、自动压缩（2026-03 起模型自主决定压缩时机） |
| CrewAI | 角色协作 | 业务流程友好；角色前缀导致 Token 足迹最重；复杂状态治理需额外设计 |
| Microsoft Agent Framework | AutoGen + Semantic Kernel 合并 | 2026-04 达 1.0 GA；AutoGen 2025-09 起维护模式，新项目不推荐 |
| OpenAI Agents SDK | 供应商官方 | Handoff = 显式工具调用式控制转移；内置 Guardrails/Tracing；供应商锁定 |
| Google ADK | 供应商官方 | 层级树（每个 Agent 单一父节点）；A2A 原生支持 |
| Claude Agent SDK | 供应商官方 | 子 Agent 抽象 + 长上下文优化；Claude Code 底座 |
| Mastra / PydanticAI | TS-first / 类型驱动 | 生态补位角色 |

**共识趋势**：
1. 供应商官方 SDK（OpenAI/Google/微软/Anthropic）全部下场，终结 LangChain 一家独大；
2. 「harness」成为新层次——真正的工程量在模型外的脚手架（计划、记忆、上下文管理、委托）；
3. 可观测性收敛到 OpenTelemetry GenAI 语义约定 + 供应商中立评测层；
4. 行业主流评论明确警告：「multi-agent 不是天然更优，单 Agent + 好工具定义通常打败编排糟糕的三 Agent 团队」。

---

## 3. 关键研究结论

### 3.1 Google/DeepMind/MIT《Towards a Science of Scaling Agent Systems》（arXiv:2512.08296，2025-12）

180 个受控配置（5 种拓扑 × 3 模型家族 × 4 基准，统一工具/提示/Token 预算）：

- **总体均值 −3.5%**：盲目多 Agent 是净损失；方差极大（结构化金融分析 +80.8%，顺序规划 −39%~−70%）。
- **能力饱和（45% 规则）**：单 Agent 基线成功率 >~45% 时，加协调收益递减甚至为负（ρ=−0.408, p<0.001）。
- **工具-协调权衡**：固定预算下，工具密集任务被协调开销不成比例拖累；协调 Token 超预算 ~45%（工程经验值 20%）质量骤降。
- **拓扑决定误差放大**：无验证的独立 Agent 放大误差 **17.2×**；有中心验证瓶颈的架构控制在 **4.4×**。
- 由任务属性（工具数、可分解性、单 Agent 基线、顺序依赖）可 **87% 准确率预测最优架构**。

### 3.2 UC Berkeley MAST 失败分类学（arXiv:2503.13657，v3 2025-10）

1000+ 真实执行轨迹、14 种失败模式、3 大类（占比稳定）：

| 类别 | 占比 | 典型模式 |
|---|---|---|
| FC1 系统设计/规格 | 41.8% | 步骤重复（17.1%，第一大单项）、不知终止条件、角色越界 |
| FC2 Agent 间失配 | 36.9% | 推理-行动不一致（14.0%）、不问澄清（11.7%）、忽略他人输出、隐式决策冲突 |
| FC3 任务验证 | 21.3% | 无/浅验证、验证者自身错误、过早终止 |

结论：失败主要源于**系统设计与协调结构**，非模型能力；提示工程只带来 ~15.6% 边际改善；结构性干预（状态机终止控制、外部验证 Gate、明确角色规格）才有效。干预实验：仅澄清角色/加验证步，ChatDev 成功率 +9%~+16%。

### 3.3 Orchestrator 过程评估（ICML 2026）

- 对 Deep Research / Agent Coder / GUI Browser / Agentic RAG 四类系统做失败归因：**Orchestrator 承担主要失败责任**（派错 Agent、误读输出、重复循环、提前终止、错误反馈后无法恢复）。
- 提出「调度熵」：任务推进带来聚焦，上下文累积带来扩散；系统稳定性取决于 Orchestrator 能否在上下文膨胀中维持判断清晰。
- 主张过程级评估（每步调度决策可检查），而不仅是结果评估。

### 3.4 其他

- **CRAFT 基准**（arXiv:2603.25268）：更强推理能力**不**可靠地转化为更好协作；小模型常打平/超过前沿模型——协作是独立于推理的未解难题。
- **TeamTR**（ICML 2026）：微调共享上下文团队中单个成员会移动全队上下文分布（compounding occupancy shift）——启示：**认证的组织模板应整体认证/整体回归，不能只换单个成员而不重新评测**。
- **Cognition「共享完整轨迹」论点**：并行 Agent 各自合理但隐式决策冲突 → 拼出不连贯产物；共享权威轨迹/状态是解药。

---

## 4. 协议标准化（MCP + A2A）

- 双协议分层已成事实标准：**MCP = Agent↔工具（纵向）**，**A2A = Agent↔Agent（横向）**；均已捐入 Linux Foundation（MCP → AAIF，A2A → LF AI & Data），2026-06 发布融合草案。
- **A2A v1.0（2026-04）**：150+ 组织、生产部署；签名 Agent Card（JWS，`/.well-known/agent-card.json`）、任务生命周期 `submitted → working → completed/failed/canceled`（含 `input_required` / `auth_required`）、多租户、gRPC/SSE/webhook 三种传输。
- **信任层仍是短板**：签名卡只证明「发布者是谁」，不证明「当前在谁的委托下做什么」；已有 rogue Agent 用夸大 Agent Card 操纵编排者路由的攻击演示。缓解手册＝密码学验卡 + 身份 allowlist + mTLS/PKI——**这正是 Regent Permit/fencing/能力认证已覆盖的层**。
- A2A「设计上不透明」（不暴露内部推理）适合跨组织协作，但与 Regent 的证据链要求相反——内部 Agent 应保持全轨迹可审计，A2A 语义只用于未来跨边界互操作。

---

## 5. 长任务工程模式（Deep Agents / Claude Agent SDK 收敛出的 harness 范式）

四件套已成行业标配：
1. **持久计划**（write_todos 状态化任务清单，压缩后仍存活）；
2. **子 Agent 上下文隔离**（脏活在隔离窗口做，只回传紧凑结论）；
3. **文件系统卸载**（>20k Token 工具结果落盘，正文只留路径+预览；写/编辑历史参数超阈值截断为指针）；
4. **压缩/Compaction**（阈值触发 + 2026-03 起「模型自主决定压缩时机」的 compact 工具，保留末尾 ~10%，全量原文落盘可回查）。

---

## 6. 对 Regent 的意义：立场验证 + 借鉴清单

### 6.1 外部证据强力验证了 Regent 的既有立场（不需改）

| Regent 冻结原则 | 外部印证 |
|---|---|
| 「多 Agent 不是默认架构；必须冻结实验验证净收益」（PRD §0.3-6） | Scaling 论文均值 −3.5%；45% 饱和规则；全行业「multi-agent is a tool, not a goal」 |
| P2-4 Eval Harness 先于 P2-5 自适应组织 | 论文证明架构选择可预测、必须实证；87% 预测器本身依赖冻结基准 |
| 独立评价者/生成者不得自评（§0.3-8） | 17.2× vs 4.4× 误差放大——验证瓶颈是拓扑级要求 |
| 状态机 + 明确终止 + 预算即终态 | MAST FC1 41.8%（步骤重复/不知终止）正是缺这些的系统的头号死因 |
| PostgreSQL 唯一事实源、Artifact 引用而非消息传递 | Cognition「共享权威轨迹」论点；MAST FC2 隐式决策冲突 |
| AgentEnvelope 权限只减不增、UNTRUSTED_DATA | A2A 信任层短板 + rogue Agent Card 攻击，业界 2026 才开始补这一课 |
| 自适应自由拓扑 ROLLOUT_NOT_ALLOWED | 全连接 swarm 因消息爆炸最早崩溃（消息密度是负向指标） |

**结论：Regent 的治理姿态领先于行业平均实践约一年；不要因框架热度动摇「单 Agent champion」默认叙事。**

### 6.2 可吸收借鉴项（按优先级）

#### A. 近期可做（不改验收口径，增强现有承诺项）

1. **P2-4 Eval Harness 增加三个冻结指标**（写入任务集协议，与 PRD §10 兼容）：
   - `coordination_token_share`：协调消息 Token / 总 Token（论文阈值 ~45%，工程告警线 20%）；
   - `error_amplification_factor`：注入受控错误后放大倍数（对照 17.2×/4.4× 基线）；
   - `dispatch_entropy`：Orchestrator 每步调度分布熵的时间序列（发散趋势 = 失稳前兆）。
2. **引入 MAST 作为多 Agent 运行的 `failure_code` 词表扩展**：14 种失败模式映射为稳定失败码子集（如 `MAST_STEP_REPETITION`、`MAST_PREMATURE_TERMINATION`、`MAST_IGNORED_PEER_OUTPUT`），供 Hive 模板运行归因与 A/B/C 对照实验分析。MAST 有 pip 库与开放数据集可参考口径。
3. **认证 Hive 模板 `pm-dev-independent-qa-v1` 补齐「每 Agent 显式规格」三要素**：角色边界、工具 allowlist、停止条件（MAST FC1 对策）；并在模板契约中加入「强制澄清动作」——不确定时必须发起澄清而非继续（FC2 第二大失败模式的直接对策）。
4. **模板整体认证原则**（吸收 TeamTR 启示）：认证组织模板中替换任一成员（模型版本/Prompt/工具）都必须整体重新过回归，禁止逐件替换后沿用旧认证——可加进 Runtime/组织注册表的认证语义。

#### B. P2-5 自适应组织设计输入（条件承诺激活时使用）

5. **用任务属性先验初始化 `UtilityFunction U(O_t)` / `TopologyPlanner`**：以可测特征（工具数量、子任务可分解性、顺序依赖强度、单 Agent 基线成功率）作路由输入；显式编码三条经验律——
   - 单 Agent 基线 >45% → 不提议多 Agent；
   - 强顺序依赖 → 保持单 Agent；
   - 可分解 + 需验证 → 只提议「中心化 + 验证瓶颈」拓扑。
   这把 87% 预测器思想变成 OrganizationSpace 的裁剪规则，缩小搜索空间也降低实验成本。
6. **Orchestrator 过程可检查**：调度决策（选谁、为何、基于哪些证据）落为可重放对象——Regent 的 `SchedulingDecision` 已有此意，扩展到组织内派工层；对齐 ICML 2026「过程评估」方向，也直接服务控制台「参与 Agent 名册 + live 活动」体验。

#### C. Worker/Agent 运行时工程改进（与治理正交，随时可做）

7. **上下文卸载三板斧**（Deep Agents 模式，落到 Regent 的 WorkingMemory/ArtifactStore）：
   - 大工具结果（阈值 ~20k Token）写入 Artifact/文件，正文只留引用+预览；
   - 长会话超阈值时把历史写/编辑参数截断为 Artifact 指针；
   - 压缩时全量原文落盘为可检索记录，摘要含「会话意图 + 已产工件 + 下一步」结构化字段。
   这与 P1 R1–R6 的长生成链直接相关，可降低 Token 成本（北极星分子）且不触碰治理边界。
8. **持久计划对象**：Worker 内部维护状态化 todo（进行中/完成），使其在上下文压缩后存活——与控制台「进度详略」语义天然对齐。
9. **可观测性对齐 OTel GenAI 语义约定**：Agent 步骤 span 化，便于未来接入供应商中立评测层，也满足 Tech-Spec §22 对协调 Token 的观测要求。

#### D. 协议对齐（低成本前瞻，不新增承诺）

10. **AgentEnvelope 字段向 A2A v1.0 对齐**：`contextId`/任务生命周期（`submitted→working→completed/failed/canceled`、`input_required`≈WAITING_HUMAN、`auth_required`≈Permit 等待）与 Regent Run/HumanTask 状态机几乎同构，映射表成本很低；签名 Agent Card 语义可作为能力池「能力声明」的对外投影格式。收益：未来接入外部生态 Agent 时无需重构。
11. **能力池工具面统一走 MCP**：MCP 已是工具接入事实标准（注册表 + 签名/信任评分在路上）；Regent 缺的审计/细粒度授权恰是自身强项，可在 MCP 之上叠加 Permit——不采纳 MCP 生态则重复造工具接入轮子。

#### E. 明确不借鉴的（记录以防反复）

- **不引入 CrewAI 式角色扮演抽象**：Token 足迹最重、复杂状态治理弱，与证据链/状态机模型冲突。
- **不采用任何框架替换自研内核**：LangGraph 的 checkpoint/持久化与 Regent 的 Outbox/Lease/Permit 职责重叠但治理弱于自研；框架可用于**能力池内封装单个 Agent 能力**，不进入 Kernel。
- **不采纳 A2A「不透明协作」于内部 Agent**：与证据优先原则冲突；仅保留为跨组织互操作预留语义。
- **不跟进「更多 Agent = 卖点」叙事**：外部证据（−3.5% 均值）站在 Regent 一边。

---

## 7. 建议落点摘要

| 借鉴项 | 落点 | 阶段 |
|---|---|---|
| 3 个协调指标入 Harness | P2-4 任务集协议 | 承诺项内增强 |
| MAST failure_code 词表 | 失败码 + 实验归因 | 近期 |
| Hive 模板每 Agent 三要素 + 强制澄清 | 模板契约 | 近期 |
| 模板整体认证原则 | 组织/Runtime 注册表 | 近期 |
| 任务属性先验裁剪 OrganizationSpace | P2-5 设计输入 | 条件激活时 |
| 调度决策过程可检查 | SchedulingDecision 扩展 | P2-1/P2-5 |
| 上下文卸载 + 持久计划 | Worker 运行时 | 随时（正交） |
| OTel GenAI span | 可观测性 | 随时（正交） |
| AgentEnvelope ↔ A2A 映射表 | P2-5 契约附录 | 低成本前瞻 |
| 工具面 MCP 化 | 能力池 | 渐进 |

---

## 8. 主要来源

- Kim et al., *Towards a Science of Scaling Agent Systems*, arXiv:2512.08296（Google Research / DeepMind / MIT）
- Cemri et al., *Why Do Multi-Agent LLM Systems Fail?*（MAST）, arXiv:2503.13657，v3 2025-10；MAST-Data / pip 库
- ICML 2026：Orchestrator 过程评估与调度熵（Mean-Field Entropy Dynamics / IWG）
- *TeamTR: Trust-Region Fine-Tuning for Multi-Agent LLM Coordination*, ICML 2026, arXiv:2605.15207
- *CRAFT: Grounded Multi-Agent Coordination Under Partial Information*, arXiv:2603.25268
- A2A v1.0 发布与 Linux Foundation 一周年通告（2026-04）；MCP+A2A 融合草案（2026-06）
- LangChain Deep Agents 文档与《Context Management for Deep Agents》；autonomous compaction（2026-03）
- 2026 年框架对比：FutureAGI / TrueFoundry / AgentList 等（Microsoft Agent Framework 1.0 GA、AutoGen 维护模式等事实项）

> 注：本文为调研输入，不改变任何验收口径；任何采纳项按仓库规则应通过 ADR 或 DecisionRecord 进入执行基线。
