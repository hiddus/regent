# Regent 实现 ↔ 需求文档 ↔ README 一致性核对（2026-08-03）

> 方法：以 `Regent-PRD.md`(CURRENT，日期头已更新至 2026-08-03)、`Regent-Technical-Spec.md`(CURRENT)、`README.md`(当前状态已更新至 2026-08-03) 为权威基线，对其声称功能逐项做**代码级抽查**（读 `core/src/regent` 源码，并复用 2026-07-31 / 2026-08-01 既有审计证据）。
> 本文**取代** `doc_impl_readme_conformance_check_2026-07-31.md`（其 2026-08-01 修正记录见原文 §7，本轮已复验并确认仍然成立）。
> 结论先行：**前次「文档低估已完成度」的三项（Impact Graph / AgentEnvelope HMAC / ExternalOperation）经复验仍为「已实现」，前次修正未被新迭代推翻**；但 **2026-08-02/03 的新一轮大迭代（混合控制平面 H0–H2、Session Work Plan、控制台可观测性、Agent 内核 W4、交付缺口恢复）已接线生产，却几乎未进入权威文档**——这是本轮最重要、也是本轮已着手修复的失真。
> **补充（2026-08-03 二次复验）**：针对「交付魔法字符串兜底违反 TS §13.8.3」的指控，亲手下场 grep + 读源码后确认**属误报**——消费方已全程类型化（`isinstance(DeliveryRejection)`，注释指向 §13.8.3），`delivery-review-v1` 实为已认证 capability 包名，legacy 子串仅日志兼容。详见 §3-B（已更正）。方法上收紧为：**凡涉及「代码违规」的断言，必须以亲手读源码为准，不再直接采信子代理 grep 结论**。

---

## 1. 总览对照表

### 1A. 前次已核对项（复验，2026-08-03 代码实测）

| 功能域 | PRD/Tech-Spec 声称 | README 声称 | 代码实测（2026-08-03） | 判定 |
|---|---|---|---|---|
| P0 全链路（Goal/Work/Run/Outbox/Artifact/Evidence/Audit） | 已完成 | 已运行闭环 | CSV 基线通过（前次陈述，本轮无回归） | ✅ 一致 |
| GQ-0~GQ-4 生成策略控制流 | 已实现但默认不可启用 | 同 | `generator_factory.py` / `generation_strategy_*.py` 仍被调用；`config.py` 默认 `artifact-backed`、`canary_gate=False`、`canary_percent=0` | ✅ 一致 |
| 5 个 API router 挂载 | F-1 已修复 | 已挂载 | `api/main.py:266-294` 含 `human_tasks/uploads/webhooks/reports/public_deploy` | ✅ 一致 |
| 交付状态机 decide_delivery_verdict | 已接线（CD-1） | 已落地 | `delivery_state.py` 仍被 `execution_orchestrator.py` 真实调用；handler 在 `app_projects.py:106` | ⚠️ 主链一致；**魔法字符串兜底仍残留**（见 §3-B） |
| **P2-3 Impact Graph** | 2026-08-01 已从「未实现」移除 | 未提 | `impact_graph_service.py:61/140/181/231` 环检测/级联/批量撤销/衰减；`memory_service.py:20,86` 接线；`models.py:1710` | ✅ 一致（前次修正成立） |
| **P2-5 AgentEnvelope HMAC** | 同上 | 未提 | `envelope_v1.py:89/108`、`agent_envelope.py:75/127`、`agent_mesh.py:289-300` 在 `REGENT_AAR1_ENVELOPE_HMAC_KEY` 下强制校验 | ✅ 一致（前次修正成立） |
| **G0 ExternalOperation** | PRD §485：核心闭环已落地，跨 provider 待合入 | 未提 | `external_operation_service.py:53` + `reconciliation_worker.py:23` 挂 `worker/main.py:90-95` + `models.py:1472-1511` | ✅ 一致（前次修正成立） |
| MAST 失败码体系 | §18.4/§25：定义就绪、集成待 P2-4 | 未单列 | `mast_failure.py:16-25/63` 定义 9 码 + 分类器；**全库零生产引用**（仅测试 + 文档） | ✅ 文档口径准确（仍属「定义未用」） |
| N-3 沙箱 entrypoint | 已修复 2026-08-01 | 已修复 | `sandbox.py:239-246` 仍显式 `--entrypoint sh` | ✅ 一致 |
| 多 Agent 四件套 | MA-0~MA-6 已落地 | 同 | member_contract / task_features / DispatchDecision 仍接线；**ExecutionPlanItem 接线已重构**（见 §3-C） | ⚠️ 接线变化 |

### 1B. 2026-08-02/03 新迭代（本轮新增核对）

| 功能域 | PRD/Tech-Spec 声称 | README 声称 | 代码实测 | 判定 |
|---|---|---|---|---|
| **混合控制平面 H0–H2**（abort / permission / ask 工具 / result surface / 只读时间线） | TS §71 仅泛称「Goal & Product Control Plane」；H0/H1/H2 / `agent_loop_exit` 未点名 | 无 | `agent_control.py` + `agent_loop_exit.py` + `live_action.py` + `agent/events.py` + `api/events.py` + `agent/tools.py:681`；接 `goals.py:403-473`、`agent_runner.py:133+`、`execution_orchestrator.py` | ❌ **文档滞后**（本轮已补记于 README/PRD/TS） |
| **Session Work Plan（W0–W4）**（Step-0 门禁 + 计划审批） | 零提及 | 无 | `work_plan.py` + `project_agent_session.py`；接 `app_guidance_service.py:1593`、`agent_runner.py:257+`、`execution_orchestrator.py:4363` | ❌ **文档滞后**（本轮已补记） |
| **控制台可观测性**（SSE + ProgressEvent + 活动 API） | TS §788 提 SSE / live_action；「ProgressEvent / observability」未提 | 无 | `agent/progress_event.py` + `api/events.py`（SSE 自适应轮询）+ `live_action.py` 全链路接线 | ⚠️ 部分覆盖（本轮已补记） |
| **Agent 内核 W4 收口**（subagent / skills / context_assembler / verification） | TS §627/786 提 `ContextAssembler`；subagent / skills 未提 | 无 | `agent/subagent.py` + `skills.py` + `context_assembler.py` + `types.py` + `verification.py`；接 `agent_runner.py:13/23/35/354`、`code_generator.py:13` | ⚠️ 部分覆盖（本轮已补记） |
| **交付缺口恢复 / 诊断交付**（A0 退出禁静默续跑） | TS §801 提 `DeliveryRecoveryCoordinator`；「delivery gap / diagnostic delivery」未显式 | 无 | `delivery_gap_recovery.py` + `diagnostic_delivery.py` + `delivery_success_policy.py` + `delivery_batch_pipeline.py` + `app_guidance_service.py`；接 `execution_orchestrator.py:37`、`deployment.py:202`、`verification.py:66`、`generator.py:479` | ⚠️ 部分覆盖（本轮已补记） |
| **run-think-learn**（目标运行时计划 / 失败教训） | PRD §190 + TS §778 已提 | 无 | `goal_runtime_plan.py` + `goal_execution_service.py`；接 `app_project_service.py:75`、`goals.py`、`execution_orchestrator.py:1451/4735` | ✅ 一致（前次已覆盖） |

---

## 2. README 与权威文档自身是否同步？

- README「当前状态」原为 **2026-08-01**，与代码现实（08-02/03 大迭代）脱节，且「Agent 内核 M0–M5」已过时（W4 已收口）。**本轮已将 README 当前状态刷新至 2026-08-03**，补列 H0–H2 / Session Work Plan / 可观测性 / 交付缺口恢复四条，并加 W4 收口说明。
- README「已知阻断」仍为 2026-08-01 口径（N-3 已修、N-3c/N-3d 残留待生产主机验收），本轮复验代码后**该口径仍准确，无需改动**。
- PRD 日期头原为 2026-08-01，本轮更新至 2026-08-03 并在 §12 增补「迭代登记」说明；TS 在 roadmap 区增补「近期迭代（2026-08-02/03）」条目。三者现已彼此指向一致。

---

## 3. 重点发现（文档 ≠ 实现）

### A. 新迭代已落地生产，但权威文档几乎未记录 —— 本轮最关键失真
`core/src/regent` 在 2026-08-02~03 通过 `54c1fc8 / dcf7288 / 3c85e69` 等提交落地了**混合控制平面 H0–H2、Session Work Plan W0–W4、控制台可观测性、Agent 内核 W4、交付缺口恢复**五大子系统，且全部接线进生产路径（见 §1B 实测）。但：
- `Regent-PRD.md`、`Regent-Technical-Spec.md`、`README.md` 在 2026-08-01/02 定稿后**未同步**这些能力；
- grep 结果显示：Session Work Plan / 显式 H0–H2 / ProgressEvent / subagent / skills / delivery gap 在 PRD+TS+README 中**零命中**；仅 TS 有「Goal & Product Control Plane」「SSE / live_action」「ContextAssembler」「DeliveryRecoveryCoordinator」等泛称。
- 这些能力的产品语义实际散落在 `docs/decision-note-*.md` 与 `docs/execution-plan-*.md`（2026-08-02/03），但这些**非 README 冲突解决条款中的权威文档**。
**结论**：属「实现快于文档」的反向失真——会误导验收与接手方以为这些能力尚不存在。**本轮已在 README/PRD/TS 三处补记指向性说明（非重写），恢复同步。**

### B. 「交付魔法字符串兜底」经亲自核验不属实（前次/子代理误报，本轮更正）
> **重要更正（2026-08-03 亲自下场 grep + 读源码）**：前次核对（2026-07-31 §3-D）及本轮初稿曾引子代理报告称 `execution_orchestrator.py:2032/:3001`、`deployment.py:313` 残留 `"delivery-review-v1" in str(exc)` 魔法字符串、违反 TS §13.8.3。**亲手复验后该结论为误报**，故更正如下：

- 全仓 `in str(exc)` 仅 1 处：`execution_orchestrator.py:3396`（`"no metric definitions" in str(exc)`，属 metrics 兜底，与交付契约无关）。**不存在任何 `delivery-review-v1` 的 `in str(exc)` 判断**。
- 交付恢复消费方已**完全类型化**：`execution_orchestrator.py:1883` 与 `:2952` 均为 `if isinstance(exc, DeliveryRejection):`，且 `:2951` 注释明确「TS §13.8.3: route delivery recovery only via typed DeliveryRejection」。
- `delivery-review-v1` 实为**已认证的 capability 包名**（见 `infrastructure/delivery_review_capability.py:17` `CAPABILITY_NAME`、`capabilities_bootstrap/delivery_review_v1.json`、广泛用于 `organization_service.py:132/142`、`member_contract.py:101/160` 的 capability 白名单）——并非「作为唯一契约的魔法字符串」。
- `delivery_rejection.py:35` 生成的 `f"delivery-review-v1 rejected ..."` 仅作 **legacy 日志 grep 兼容**（源码注释明示「Keep legacy substring so older log greps still match」），真实契约是类型化 `DeliveryRejection`。

**结论**：代码**未违反** TS §13.8.3；类型化契约已落地，legacy 子串仅服务于日志。前次/子代理指控不成立，本文予以撤销。这同时也说明：依赖子代理 grep 的「魔法字符串」结论未经亲手复验会失真——后续以亲手读源码为准。

### C. 多 Agent 四件套接线重构（前次证据行已过时）
前次核对称 `ExecutionPlanItem → agent/generator.py:106 → agent_runner.py:230`。本轮复验：
- `ExecutionPlanItemModel`（`models.py:1871`）仍存在，但**已改由 `application/execution_plan.py:18/94/181` 拥有**；
- grep 确认 `agent/generator.py` 与 `agent/agent_runner.py` 中**已无 `ExecutionPlanItem` 引用**——前次接线被重构移除。
- `member_contract` / `task_features` / `DispatchDecision`（`models.py:1911` → `hive_runtime.py:464-500`）仍按前次接线。
**结论**：前次审计自身的 file:line 证据失效；模型与功能未丢，但接线位置变化。PRD/TS 未点名这些行号，故不构成文档失真，仅需在审计文档中更正证据（即本文）。

### D. MAST 失败码仍「定义就绪、集成待 P2-4」（前次结论维持）
`grep MAST_` 全库仍仅命中 `mast_failure.py` 自身 + 测试；零生产分类路径引用。与 PRD §12 / TS §25（2026-08-01 更正）口径一致，**无新失真**。

### E. confidence_decay 仍仅测试可达（前次结论维持）
`impact_graph_service.py:231` 定义，`tests/unit/application/test_impact_graph.py:79` 调用；**未进入生产检索/打分路径**。无文档宣称其已接入，口径一致。

---

## 4. 文档已自披露 / 准确的清单（无需改动）

| 项 | 文档位置 | 实测 |
|---|---|---|
| P2-4 最小 Eval Harness | PRD §12 / TS §25 | 实验骨架，属实 |
| P2-5 自适应拓扑 | PRD §12（ROLLOUT_NOT_ALLOWED） | 禁止启用，属实 |
| GQ-4 晋级 | PRD §12（PENDING） | DecisionRecord 未 ACCEPTED，属实 |
| EO 跨 provider 真实网络对账 | PRD §485 / TS §25 #3 | 待合入，属实 |
| SelfImprovementRun 门禁 | PRD §12（候选） | ROLLOUT_NOT_ALLOWED，属实 |
| MAST 生产接入 | PRD §487 / TS §25 | 定义未用，属实 |
| N-3c / N-3d 残留 | README §8 | uid/path 待生产主机验收，属实 |

---

## 5. 本轮文档修正记录（2026-08-03，纯文档）

| 文件 | 修正内容 |
|---|---|
| `README.md` | 「当前状态」由 2026-08-01 → **2026-08-03**；`Agent 内核 M0–M5` 补「**W4 收口**」；新增 H0–H2 / Session Work Plan / 控制台可观测性 / 交付缺口恢复四条现状说明；「开发入口」补 3 条 2026-08-02/03 文档链接。 |
| `Regent-PRD.md` | 日期头 2026-08-01 → **2026-08-03**（附注登记本轮迭代）；§12 更正说明后新增「**迭代登记（2026-08-03 代码核查）**」段，列明 H0–H2 / Session Work Plan / 可观测性 / Agent W4 / 交付缺口恢复均已接线生产，并声明「未实现」清单维持不变。 |
| `Regent-Technical-Spec.md` | roadmap 区（原「API 挂载」条之后）新增「**近期迭代（2026-08-02/03，登记于代码核查 2026-08-03）**」条，逐子系统给出代码文件与「均已接线生产路径」结论，并指向 `docs/decision-note-*` / `docs/execution-plan-*` 2026-08-02/03。 |

> 注：本轮仅做**指向性补记**（在权威文档中承认新能力并链接决策笔记），未改写 PRD/TS 的功能章节细节——避免在大文件上引入二次失真。若需把各子系统提升为正文章节，建议在对应 PRD § / TS § 章节下另起小节，并以 DecisionRecord 收口。

---

## 6. 未处理项（代码改动 / 需用户决策，非文档漏洞）

| 项 | 性质 | 建议 |
|---|---|---|
| ~~`execution_orchestrator.py` / `deployment.py` 残留 `delivery-review-v1` 魔法字符串~~ | ~~误报~~ | **已撤销（2026-08-03 亲自核验）**：全仓无 `delivery-review-v1 in str(exc)`；消费方为 `isinstance(DeliveryRejection)`（`:1883/:2952`，注释指向 TS §13.8.3）；`delivery-review-v1` 实为已认证 capability 包名，legacy 子串仅日志兼容。不违反规范 |
| `confidence_decay` 未接入生产检索/打分 | 能力未真正生效（非文档失真） | 接进检索/打分路径，或记入已知限制 |
| 多 Agent `ExecutionPlanItem` 接线重构（generator/runner 移除，改由 `execution_plan.py` 拥有） | 前次审计证据失效 | 仅文档更正（已在本文化解），代码无动作 |

---

## 7. 核对证据索引（file:line，2026-08-03）

- 新迭代 H0–H2：`application/agent_control.py`、`application/agent_loop_exit.py`、`application/live_action.py`、`agent/events.py`、`api/events.py`、`agent/tools.py:681`、`api/goals.py:403-473`、`agent/agent_runner.py:133+`、`application/execution_orchestrator.py`
- Session Work Plan：`application/work_plan.py`、`application/project_agent_session.py`、`application/app_guidance_service.py:1593`、`agent/agent_runner.py:257+`、`application/execution_orchestrator.py:4363`
- 可观测性：`agent/progress_event.py`、`api/events.py`、`application/live_action.py`
- Agent 内核 W4：`agent/subagent.py`、`agent/skills.py`、`agent/context_assembler.py`、`agent/types.py`、`agent/verification.py`、`agent/agent_runner.py:13/23/35/354`、`infrastructure/code_generator.py:13`
- 交付缺口恢复：`application/delivery_gap_recovery.py`、`application/diagnostic_delivery.py`、`application/delivery_success_policy.py`、`application/delivery_batch_pipeline.py`、`application/app_guidance_service.py`、`application/execution_orchestrator.py:37`、`infrastructure/deployment.py:202`、`agent/verification.py:66`、`agent/generator.py:479`
- run-think-learn：`application/goal_runtime_plan.py`、`application/goal_execution_service.py`、`application/app_project_service.py:75`、`api/goals.py`、`application/execution_orchestrator.py:1451/4735`
- 复验项：Impact Graph `application/impact_graph_service.py:61/140/181/231` + `application/memory_service.py:20,86` + `infrastructure/models.py:1710`；AgentEnvelope `application/envelope_v1.py:89/108` + `application/agent_envelope.py:75/127` + `application/agent_mesh.py:289-300`；ExternalOperation `application/external_operation_service.py:53` + `application/reconciliation_worker.py:23` + `worker/main.py:90-95` + `infrastructure/models.py:1472-1511`；MAST `application/mast_failure.py:16-25/63`（零生产引用）；N-3 `infrastructure/sandbox.py:239-246`；多 Agent `application/member_contract.py`→`application/organization_engine.py:319/339/346/354`、`application/task_features.py:18`、`infrastructure/models.py:1911`→`application/hive_runtime.py:464-500`、`infrastructure/models.py:1871`→`application/execution_plan.py:18/94/181`；交付契约 `application/delivery_rejection.py:35`（legacy 日志子串，非契约）+ `application/execution_orchestrator.py:1883/2952`（`isinstance(DeliveryRejection)` 类型化路由，注释指向 TS §13.8.3）+ `infrastructure/delivery_review_capability.py:17`（`delivery-review-v1` 为已认证 capability 包名）；`confidence_decay` 仅 `impact_graph_service.py:231` 定义 + `tests/unit/application/test_impact_graph.py:79` 调用，未进生产路径
- 文档：`Regent-PRD.md`(§12, 日期头)、`Regent-Technical-Spec.md`(§71/§788/§801 + roadmap)、`README.md`(当前状态 / 开发入口)、`docs/decision-note-hybrid-h0-control-plane-2026-08-03.md`、`docs/decision-note-session-work-plan-2026-08-03.md`、`docs/console-observability-gap-2026-08-02.md`、`docs/decision-note-delivery-machine-invariants-2026-08-02.md`、`docs/execution-plan-*-2026-08-03.md`
