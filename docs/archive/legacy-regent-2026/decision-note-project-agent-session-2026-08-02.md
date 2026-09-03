# DecisionNote: ProjectAgentSession 作为项目运行底盘（不新建第三套 Agent loop）

**日期**：2026-08-02  
**状态**：ACCEPTED（编码基线）  
**相关**：[`agent_session_kernel` 计划]、[`decision-note-delivery-machine-invariants-2026-08-02.md`](decision-note-delivery-machine-invariants-2026-08-02.md)、[`conversational-delivery-architecture-review-2026-07-31.md`](conversational-delivery-architecture-review-2026-07-31.md)

---

## 0. 决策摘要

1. Regent 已有两套半截 Agent 相关循环：**对话** `AppGuidanceService.guide`（CD-4）、**执行** `AgentRunner`（工具环 + 同轨迹 repair）。
2. **禁止新建第三套** `ProjectAgentLoop` / 平行 Observe-Plan-Act 引擎。
3. 新增 `ProjectAgentSession` **只做底盘**：身份、权威工作区、checkpoint、epoch；用它把两套既有循环焊成跨 GenerationRun 的持续主体。
4. `GenerationRun` 降为 Worker lease 壳；`DeliveryGapRecovery` / ATTRIBUTE_3 在 Session 可续跑时不得替 Agent 选下一步。

---

## 1. 不变量

| ID | 不变量 |
|----|--------|
| I-A | ACTIVE Project ⇒ exactly one active `ProjectAgentSession` |
| I-B | 唯一执行 loop = `AgentRunner`；ArtifactBacked ⇒ scaffold/bootstrap / kill-switch fallback only |
| I-C | VerificationGap ⇒ 同 Session 续跑 `AgentRunner`（同 workspace + checkpoint） |
| I-D | 用户 CONTINUE/RESUME ⇒ 恢复同 Session；禁止无 session 的空白新 Run |
| I-E | DeliveryGap / ATTRIBUTE_3 **不得**在 Session 仍可续跑时替 Agent 选下一步 |

兼容既有交付机器不变量：尤其 **I-3**（DB+Outbox 事实源）、**I-4**（Outbox→Worker lease，非进程常驻 REPL）、**I-12**（同轨迹 repair，禁冷启动递归）。

---

## 2. 实现锚点

| 构件 | 路径 | 职责 |
|------|------|------|
| 模型 | `infrastructure/models.py` `ProjectAgentSessionModel` | 表 `project_agent_sessions` |
| 服务 | `application/project_agent_session.py` | ensure / require / pause / stop / bump_epoch |
| 激活挂钩 | `application/goal_execution_service.py` `start` | Goal→ACTIVE 时 `ensure_active_session_in` |
| 执行 loop | `agent/agent_runner.py` | **唯一**工具执行循环（不复制） |
| 对话入口 | `application/app_guidance_service.py` | **唯一**人对会话命令链（不升级成写码 loop） |

**不是**新 loop：`SubagentRunner`、`DeliveryBatchPipeline`、Hive PM→Dev→QA。

---

## 2.1 落地补强（2026-08-03）

相对初版实现的缺口已补齐：

| 项 | 落点 |
|----|------|
| AgentRunner Session 对话续种 | `agent/agent_runner.py` `_seed_session_conversation` |
| GenerationRun 主路径 stamp `session_id`+`epoch` | `execution_orchestrator.handle_capability_resolution_satisfied` |
| 无 Session 拒生成（I-A fail-closed） | orchestrator ensure 失败 → `DomainError` |
| CONTINUE/RESUME/PAUSE 焊 Session | `app_guidance_service` |
| Gate 失败旁路 ATTRIBUTE_3（Session ACTIVE） | `delivery_gap_recovery.prepare_gate_reorganization` |
| 活动流 Session 文案 | `live_action.EVENT_LIVE_SUMMARY` |

---

## 3. 明确不做

- 不新增与 `AgentRunner` 平行的 chat+tools 主循环类
- 本批次不扩 Hive / 自适应组织 / 平级 AB↔agentic Canary 翻转
- 不把 Session checkpoint 塞进 Conversation metadata 当权威（I-3）

---

## 4. 评审检查句

1. 是否仍只有一套执行 `AgentRunner`？（禁止第三套 loop）  
2. ACTIVE 项目是否必有 Session？（I-A）  
3. 失败/用户继续是否回到同 Session，而非 DeliveryGap 彩票？（I-C/I-D/I-E）  
4. Session 是否仍经 Outbox/lease 续跑，而非进程常驻？（I-4）
