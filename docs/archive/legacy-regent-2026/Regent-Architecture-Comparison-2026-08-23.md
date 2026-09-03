# Regent 架构对齐分析：对标市面竞品的三条硬要求

> 日期：2026-08-23（同日两次更新：P0 两项已实现并通过单测，见 §3 R3 与 §4；并发加固——steering 合并写入 / 行锁原子护栏 / retrigger claim——同日追加，见 §3 R3 加固段）
> 基线代码：commit `846e262`（分析初稿基于 `40e5378`；R3 修复主体随 `ad14ec9` 入库，steering 合并/行锁/claim 加固随 `846e262` 入库）
> 性质：架构差距分析 + 对齐路线建议。事实均以代码与公开资料为据，"已接线 / 半闭环 / 未接线"逐项标注。

---

## 0. 结论速览

用户提出三条硬要求，逐条评估结果：

| # | 硬要求 | 当前状态 | 判定 |
|---|---|---|---|
| R1 | 单 Agent 架构与 Codex 的 ARC-AGI-3 打法差别不能过大 | `AgentRunner` 已是标准 ReAct 工具循环 + 上下文压缩 + 轨迹留存 + 同轨迹修复 | **范式已对齐，3 个具体缺口**（推理过程保留、步数效率度量、环境工具面） |
| R2 | 在单 Agent 基础上，多 Agent 必须能自适应编排 | 目标分类→组织模式已接线，但为**规则式一次性选择**；团队主体仍是固定模板 hive | **半对齐，3 个剩余缺口**（G2-3 `max_iterations` 已于 2026-08-23 兑现；余：LLM 参与分类判断、运行中重选、动态组队） |
| R3 | 目标不能一次输入后卡死，必须能不断调整优化 | 控制台人机环**已闭环**（CORRECT→spec v+1→steering 注入）；架构自进化有人工门；运行时反馈环**已于 2026-08-23 打通**（REPAIR 自动再调度 + worker 周期监测 tick，护栏内运行） | **三通道全通** ✅ |

一句话：**R1 底子好，R2 有骨架缺自适应，R3 三通道闭环（自动环为当日修复）。** 下一步性价比最高的是 R2 的运行中重选与 R1 的推理状态保留。

---

## 1. 当前架构梳理（commit 846e262 后的真实状态）

### 1.1 单 Agent 执行循环（Axis 1）——已接线，真实现

`agent/agent_runner.py::AgentRunner.run()` 是标准 ReAct 式 LLM 工具调用循环：

```
while turn < max_turns:
    组装上下文（ContextAssembler）
    → provider.chat(messages, tools=TOOL_SPECS)   # ChatProvider Protocol，模型可替换
    → 逐个执行 assistant.tool_calls
    → 工具结果以 role="tool" 回填
    → 直到 submit 被调用
→ VerificationAgent.verify() 验证
→ 失败则同轨迹修复：gaps 作为新 user turn 注入（不递归 self.run）
   受 repair_policy.plan_repair 与 agent_nested_repair_max 控制
```

- **工具注册表**（`agent/tools.py::TOOL_SPECS`，13 个）：`list_files / glob / grep / read_file / write_file / edit_file / read_artifact / run_command / todo_write / plan_list / plan_update / delegate_plan_item / ask_user_question / submit`。每个 spec 声明 `max_cost` 供预算预留。
- **上下文管理**：`ContextCompactor`（LLM/启发式摘要）在逼近 `context_window_tokens` 时自动压缩，用真实 prompt_tokens 校准估算；`micro_compact(keep_recent=8)` 每轮裁剪工具消息；大结果经 `context_artifact.offload_tool_result` 落盘为 artifact；Session 恢复时从 `.regent_agent_transcript.json` 播种对话。
- **预算纪律**：每轮在 `budget_ledger` 做最坏成本 reserve→claim→settle。
- **防自循环**：滑动窗口指纹（`_recent_call_fps` deque(maxlen=12)），同指纹 ≥3 次回注错误、≥6 次抛 `AskUserRequiredError` 交用户裁决；另有 todo 无进展检测。
- **hub-and-spoke 纪律**（`application/agent_invocation_guard.py`，接线于 agent_runner.py:439）：`MAX_EFFECTIVE_DELEGATE_DEPTH=1`——主 agent 可 delegate，子 agent 不可再委托；`check_cross_deployment_invocation`（delivery 角色互调环检测）**已实现未接线**。

### 1.2 多 Agent 编排（Axis 2）——分类器已接线，"自适应"仍是规则开关

- **GoalClassifier**（`application/goal_classifier.py`）：**纯规则、无 LLM**。产出 scale（SMALL/MEDIUM/LARGE，按输入长度）、domain（static-web/interactive-app/api-service/data-pipeline/other，正则计分）、complexity、iteration_need、monitoring_need + confidence/signals。
- **OrganizationModeSelector**：4 种模式 WATERFALL（全管线）/ AGILE / HUB_SPOKE（enable_monitoring + enable_repair_loop，max_iterations=10）/ BATCH。已接线：`execution_orchestrator.py:318` 启动执行时调用 `select_mode_from_metadata`，分类结果持久化进 `goal.metadata_json`；agile/hub_spoke/batch 走 `_bypass_pipeline_to_generation` 旁路。
- **关键局限**：一次性规则选择，**运行中不重选**；`max_iterations` 定义后**无任何消费方（stub 字段）**。
- **团队主体仍是固定模板**：`member_contract.py` 冻结 `pm-dev-independent-qa-v1` hive 三角色契约（职责/工具白名单/停止/澄清/交接条件），`config.aar1_certified_hive=True` 为产品默认，由 `generation_hive_executor.py` 执行。`dispatch_decision.py` 只做审计记录（记录选中 agent/候选权重/entropy），**不做实际调度**。
- **hub_spoke 具体形态**：主 AgentRunner 为 hub，`delegate_plan_item` 派发 milestone 给 `agent/subagent.py::SubagentRunner` spoke，spoke 回写 todo 状态、hub 持久化 ExecutionPlan——即 depth-1 委托，非多 hub 协商。
- 另存在 `organization_engine/organization_service` 动态组织 + `p25_adaptive_gate`、`organization_experiment_service` 实验路径（未在主链默认启用）。

### 1.3 目标持续调整（Axis 3）——人工环闭环，自动环断链

三条优化通道的现状：

**通道 A：控制台对话框（已闭环 ✅）**
`api/app_guidance.py` → `app_guidance_service.py`：`POST /v1/app-projects/{id}/guidance`，LLM 把用户消息分类为 QUERY / MODIFY / CONTINUE / PAUSE / RESUME / **CORRECT** / APPROVE / REJECT / SELECT_OPTION（fork 选择），支持 ≤5 步链式 follow-up。其中 **CORRECT** 对运行中目标：追加 `active_corrections`、写 `session_steer_brief`（下一次 AgentRunner 播种时注入 "[Human steering — Goal is evolving]"）、并**创建新 GoalSpec 版本**（旧 spec 置 SUPERSEDED，新 version=FROZEN）。spec 版本由 guidance CORRECT 路径 bump。这是"目标不死锁"的人机主干道，**已经是市面竞品少有的完整实现**（Claude Code 的 steering 是会话级的，Regent 做到了 spec 级版本化）。

**通道 B：架构自进化（有门，assistive ✅）**
`api/self_improvement.py`：propose（target_file + hypothesis）→ 沙箱生成 replacement → **人工 APPROVE/REJECT 后**才 materialize。`api/harness_evolution.py`：从 gaps 生成 skill LESSONS 覆盖层写入 workspace，下次运行 `select_skills_for_goal` 注入。`api/eval_runs.py` 只产出报告。符合项目"现实影响分级治理"的既定口径——自进化是受治理辅助，不是全自动。

**通道 C：运行时问题反馈→自我修复（已闭环 ✅，2026-08-23 修复并加固）**
`RuntimeBehaviorMonitor`（HTTP 抓 preview HTML + SPA JS 文件，检查内容量/角色数量/作息周期/对话冷却等）→ `BehaviorRepairLoop.evaluate_and_repair`：MEDIUM+ 异常、15 分钟冷却后，REPAIR 决策**合并写入** `goal.metadata_json["session_steer_brief"]`（用户/系统 steering 保留在前），并经 `GoalExecutionService.start` 以 `guidance-continue:behavior-repair:` 幂等键**自动再调度**（护栏：ACTIVE + 无存活 run + `org_mode.max_iterations` 上限 + 预算未 blocked，全部在 goal 行锁事务内原子判定）。触发点两处：`PREVIEW_DEPLOYMENT_SUCCEEDED` 部署回调 + worker 主循环周期 tick（`behavior_monitor_tick.py`，默认 600s）。详见 §3 R3。

---

## 2. 市面竞品格局（2026-08 时点）

### 2.1 单 Agent 范式：ARC-AGI-3 给出的裁判标准

ARC-AGI-3（ARC Prize，2026-03 发布）是交互式智能体基准：agent 进入无说明的陌生环境，须自行探索规则、构建世界模型、自定目标、按反馈调整。指标 RHAE（相对人类行动效率）同时考核完成度与**步数效率**。人类 100% 通关（中位 7.4 分钟/关），前沿模型裸考 <1%。

对 Regent 最有参考价值的三条公开事实：

1. **OpenAI 官方复现（"两个设置翻 3 倍"）**：GPT-5.6 Sol 在官方测试框架 13.3%，换用接近 ChatGPT/**Codex** 的运行方式（Responses API harness）后 38.3%，**模型不变，只改了两个设置**：① 保留智能体自己的推理过程；② 有效压缩历史上下文。结论：**长期 Agent 的上限主要由运行时 harness 决定，不是模型**。
2. **NVIDIA AVO 满分（RHAE 100，Claude Opus 5 后端）**：内置持久记忆、监督管理、独立执行循环；以 6,624 步完成 183 关，比 VISTA 少约 900 步——胜在**状态留存与步数节省**。
3. **范式共识**：从"不完全证据构建假设 → 行动 → 观察 → 保存有用状态 → 修正认知 → 从错误假设恢复 → 长时程持续推进"。跨系统（Tycho 显式世界模型 / VISTA 直接交互 / AVO 通用架构）结论：**跑分反映的是完整智能体系统的能力，而非仅底层模型**。

即"Codex 的 ARC-AGI-3 打法"可操作化为四要件：**单 Agent ReAct 循环 + 推理过程留存 + 上下文压缩 + 同轨迹纠错恢复**。

### 2.2 多 Agent 编排产品格局（生产环境在跑的）

| 产品/框架 | 编排模式 | 与 Regent 相关的要点 |
|---|---|---|
| **Claude Code**（三套并行机制） | Subagents（后台化，主会话逐轮持计划）；Agent Teams（实验性：lead + 共享任务清单，teammate 认领，独立上下文窗口，仅任务清单+邮箱共享）；Dynamic Workflows（GA：Claude 自己写编排脚本，扇出到几十~上百 subagent，检查点续跑） | "谁持有计划"正从框架移向 agent；teammate 间可直接通信 |
| **OpenAI Agents SDK**（4 月重构后） | handoff（控制权移交）vs agents-as-tools（主 agent 保持负责，专家当工具调）；原生沙箱 + model harness（instructions/tools/approvals/tracing/可恢复状态）；Agent Builder 与 Evals 平台已弃用 | 官方指导：**从一个 agent 起步，仅在能力隔离/策略分离/可追溯性需要时加专家**——与 Regent 的 hub-and-spoke depth-1 纪律同向 |
| **Devin（Cognition）** | 层级式（PM 型父 agent 下挂领域专家团队），2-3 层是现实上限 | 复杂 PR/迁移端到端所需的深度参考 |
| **LangGraph / Temporal** | 图状态机 / 持久执行脊柱 | 可复现性与崩溃恢复的工程化路线 |
| **成本现实** | Anthropic 自报：多 Agent 系统相对单会话约 **15x token** | 自适应编排必须"按需升级"，不能默认全员上场——这正是 Regent 分类 bypass 的价值所在 |

---

## 3. 逐条对齐评估（三条硬要求）

### R1：单 Agent 必须贴近 Codex 的 ARC-AGI-3 打法 —— 范式已对齐 ✅，3 个缺口

**已对齐的部分**（对照 2.1 四要件）：

| ARC-AGI-3 四要件 | Regent 现状 |
|---|---|
| 单 Agent ReAct 循环 | ✅ `AgentRunner.run()`，13 工具，submit 收口 |
| 上下文压缩 | ✅ `ContextCompactor` 自动压缩 + prompt_tokens 校准 + `micro_compact` + 大结果落盘 |
| 轨迹/状态留存 | ✅ `.regent_agent_transcript.json` 播种恢复；todo/plan 状态持久化 |
| 同轨迹纠错恢复 | ✅ verify 失败 → gaps 注入同轨迹修复（非递归重启），受修复策略约束 |

**缺口（按影响排序）**：

- **G1-1 推理过程保留未经审计**。OpenAI 两个设置里翻 3 倍的正是这项。Regent 的 `micro_compact` 裁剪工具消息、`ContextCompactor` 摘要历史——**摘要会丢推理状态**（"我试过什么假设、排除了什么"）。需要显式的推理状态保留策略（如压缩时保留最近 N 轮完整 assistant 推理 + 维护一份 hypothesis/evidence 状态块）。
- **G1-2 无步数效率度量**。ARC-AGI-3 用 RHAE 惩罚蛮力。Regent 有预算（token/成本）但没有"每目标行动步数 vs 最优步数"的效率指标，无法发现"用 100 步干 10 步的事"这类退化。建议在 goal metadata 记录 tool_calls 总数 / 里程碑数，纳入 Eval。
- **G1-3 环境工具面偏窄**。ARC-AGI-3 顶级系统都给 agent 终端/浏览器/编辑器级通用工具。Regent 工具面是文件+工作区中心的（无浏览器、run_command 受限）。对"运营经济实体"的定位，浏览器观察类工具是自然缺口（`RuntimeBehaviorMonitor` 已经在做 HTTP 抓取分析，但那是**系统旁路**，不在 agent 工具面里）。

### R2：多 Agent 必须能自适应编排 —— 半对齐 ⚠️，3 个剩余缺口（G2-3 已兑现）

**已有且是对的部分**：分类→模式→旁路已接线；depth-1 委托纪律（`agent_invocation_guard`）与 OpenAI"从单 agent 起步、按需加专家"的官方指导同向；hive 固定模板作为 certified opt-in 保留符合 `ROLLOUT_NOT_ALLOWED` 治理口径；确定性 Application Service 执行状态转换是 Regent 的核心不变式，**不应为了"像竞品"而放弃**。

**缺口**：

- **G2-1 分类是纯规则、一次性**。`GoalClassifier` 用输入长度和正则判 scale/domain——`SMALL/MEDIUM/LARGE` 按字符数切，"做一个带支付的创新应用"和"改一行文案"可能同判。竞品的自适应核心是 **LLM 参与判断**（Claude Code 的 lead 自己决定 spawn 几个 subagent、Agent Teams 的 teammate 自主认领）。建议：规则做护栏（成本/规模上限），LLM 做判断（领域/复杂度/是否需要并行），判断理由入 metadata 可审计。
- **G2-2 运行中不重选模式**。模式在启动时定死；目标被 CORRECT 修改后、milestone 切换时都不重新分类。自适应至少要支持**在里程碑边界重评估 org_mode**（SMALL 目标中途被扩成 LARGE，应从 agile 升级到 hub_spoke/全管线）。
- **G2-3 `max_iterations` 曾是 stub → 已兑现（2026-08-23）**。原先 HUB_SPOKE 定义了 max_iterations=10 但无消费方；现已成为修复环的迭代上限消费方（监控异常→`org_mode.max_iterations` 上限内自动重跑，缺省 3），详见 R3 修复。
- **G2-4 团队组成不动态**。`dispatch_decision` 只审计不调度；`organization_engine` 动态组织路径存在但未默认启用。竞品已跑到"任务清单认领制"（Agent Teams）。务实路径：先让 hub 在 guard 护栏内**自主决定 spoke 数量与角色**（现在 delegate 目标是 plan item，可扩展为 hub 自选专家配置），再考虑任务认领制实验。

### R3：目标不死锁、持续调整优化 —— 人工环通，自动环 ❌→✅（2026-08-23 已修复）

三通道逐个对照用户要求（"控制台对话框 / 架构自进化 / 运行中问题反馈自我修改"）：

| 通道 | 竞品对照 | Regent 现状 | 判定 |
|---|---|---|---|
| 控制台对话框 | Claude Code steering 是会话级；无 spec 版本化概念 | CORRECT → spec SUPERSEDED → v+1 FROZEN → steer 注入，全链路真实现 | **领先** ✅ |
| 架构自进化 | 竞品无对应物（最接近的是 skills/lessons 学习） | self_improvement 人工门 + harness_evolution lessons 注入 | 合格（治理口径内）✅ |
| 运行中反馈→自修改 | Devin 类产品有持续重试；一般 SaaS 监控告警人工处理 | 监测真实（含 SPA JS 深析）；**自动环已打通（2026-08-23）**：REPAIR 决策经 `goal_execution_service` 以 `guidance-continue:behavior-repair:` 通道自动再调度，worker 主循环注册周期监测 tick | **闭环** ✅ |

**原断链位置与修复**：`BehaviorRepairLoop.evaluate_and_repair` 返回 REPAIR 决策后原先只写 metadata + 打 log。2026-08-23 修复后：
1. `evaluate_and_repair(..., retrigger_execution=True)` 在注入 steer brief 后调用 `GoalExecutionService.start`（与用户 RESUME 同通道），护栏为：goal 必须 ACTIVE、无存活 run（CREATED/PERMIT_PENDING/QUEUED/RUNNING 时不重触发，由活 run 自行消费 steering）、修复次数 < `org_mode.max_iterations`、预算未 blocked。
2. 新增 `application/behavior_monitor_tick.py`：worker 主循环按 `behavior_monitor_interval_seconds`（默认 600s）周期扫描 ACTIVE 且 `org_mode.enable_monitoring` 的 goal（用 orchestrator 持久化的 `behavior_monitor_preview_url`），重新观测并进修复环；单 goal 最小复观测间隔 600s。
3. 修复了 orchestrator 监测结果写 metadata 不提交事务的 bug（`behavior_monitor_preview_url` 此前根本不会落库）。

**并发与覆盖加固（2026-08-23 同日追加）**：
4. steering **合并写入**：`_inject_steering_and_claim` 先剥离修复环自己上次的笔记（`behavior_repair_own_brief`），把外来 steering（用户控制台 CORRECT / QA / host guard）保留在前、修复指令追加在后，上限 4000 字符——用户指令不再被机器自动修复覆盖。
5. **护栏原子化**：全部 retrigger 护栏（ACTIVE / 迭代上限 / 存活 run / claim）与 steering 写入、修复历史追加纳入同一 `with_for_update` 行锁事务；并发修复串行化，第二个事务观察到第一个的历史与 claim，check-then-start 竞态窗口消除。
6. **retrigger claim**（`behavior_repair_retrigger_claim`，TTL 300s）：test-and-set 于同一锁内，防止部署回调与 worker tick 双通道对同一 goal 重复触发机器执行；`start()` 返回后即清 claim，崩溃残留靠 TTL 自过期。单测覆盖合并/互斥/过期全分支（两文件共 26 个）。

---

## 4. 建议行动清单（按优先级）

**P0 — 打通 R3 自动环 —— ✅ 已完成（2026-08-23）**
1. ✅ REPAIR 决策 → 自动再调度：`evaluate_and_repair` 新增 `retrigger_execution`（orchestrator 部署回调与 worker tick 均已启用），经 `GoalExecutionService.start` 以 `guidance-continue:behavior-repair:<goal>:<uuid>` 幂等键再入队，护栏 = ACTIVE 状态 + 无存活 run + `org_mode.max_iterations` 上限 + 预算未 blocked。`max_iterations` 从 stub 变为真实消费方（同时解决 G2-3）。
2. ✅ 后台监测 tick：`application/behavior_monitor_tick.py` + worker 主循环注册（`behavior_monitor_enabled` / `behavior_monitor_interval_seconds`，默认 600s），单 goal 复观测间隔 600s，观测→修复→再调度全链路有单测覆盖。

**P1 — 补 R1/R2 的关键缺口**
3. 推理状态保留审计（G1-1）：压缩策略显式保留最近 N 轮完整 assistant 推理 + 维护 hypothesis/evidence 状态块；用 Eval 对照验证（这是 ARC-AGI-3 官方复现中收益最大的一项）。
4. 步数效率指标（G1-2）：goal metadata 记录 tool_calls 总数/里程碑数，纳入 eval-runs 报告。
5. 里程碑边界重评估 org_mode（G2-2）：milestone 切换或 CORRECT 修订 spec 后重新跑 `select_mode_from_metadata`，允许升降级。
6. 混合分类器（G2-1）：规则护栏 + LLM 判断，理由入 metadata。
7. 接线 `check_cross_deployment_invocation`（已实现未接线）。

**P2 — 结构性演进（实验路径，不进默认主链）**
8. 浏览器/通用环境工具进 agent 工具面（G1-3），可复用 `RuntimeBehaviorMonitor` 的 HTTP/JS 分析能力改造成 agent tool。
9. hub 自主选择 spoke 数量与角色配置（G2-4），guard 护栏内。
10. 任务认领制动态团队实验（对标 Agent Teams），走 `organization_experiment_service` 既有实验通道。

**不动的部分（明确不向竞品看齐）**：
- 确定性 Application Service 执行状态转换的核心不变式——竞品的"计划持有权交给 agent"以牺牲可审计性换灵活性，与 Regent 的治理定位冲突；Regent 的正确姿势是**护栏内放权**（guard 纪律 + 预算 + 人工门），而不是照搬。
- hive 固定模板保留为 certified opt-in；自进化保留人工 APPROVE 门。

---

## 附：本次对标的事实来源

- Regent 代码：commit `846e262`（R3 修复主体与 steering 合并/行锁/claim 加固均已入库）；`agent/agent_runner.py`、`agent/tools.py`、`application/{goal_classifier,organization_mode_selector,agent_invocation_guard,runtime_behavior_monitor,behavior_repair_loop,behavior_monitor_tick,execution_orchestrator,app_guidance_service}.py`、`api/{app_guidance,self_improvement,harness_evolution,eval_runs}.py`。
- ARC-AGI-3：ARC Prize 技术报告（2026-03）；OpenAI "How two settings tripled our ARC-AGI-3 scores"（2026-07-30）；NVIDIA AVO 结果（2026-08）。
- 多 Agent 格局：Anthropic Claude Code Subagents/Agent Teams/Dynamic Workflows 文档与周报；OpenAI Agents SDK 2026-04 重构公告与弃用公告；Cognition Devin 架构报道；LangGraph/Temporal/MS Agent Framework 生态综述（2026-08）。
