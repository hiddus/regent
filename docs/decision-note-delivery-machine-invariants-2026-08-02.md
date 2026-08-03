# DecisionNote: 交付机器不变量 + 过渡观测态标注

**日期**：2026-08-02  
**状态**：DRAFT（运作哲学已由方向注记裁定；I-* 全文待按人步切片修订后，再议是否 ACCEPTED）  
**方向已定**：[`direction-note-run-think-learn-2026-08-02.md`](direction-note-run-think-learn-2026-08-02.md)（模型主理 · 人辅助 · 边跑边学 · 无退出门哲学）  
**规范身份源**：[`definitions/REGENT-DEFINITION-1.0.txt`](definitions/REGENT-DEFINITION-1.0.txt)（FROZEN，本文只引用不改写）  
**相关**：[`decision-note-auto-start-journey-2026-07-31.md`](decision-note-auto-start-journey-2026-07-31.md)（C1 ACCEPTED）、[`agent-core-vs-claudecode-audit-2026-08-02.md`](agent-core-vs-claudecode-audit-2026-08-02.md)、[`console-observability-gap-2026-08-02.md`](console-observability-gap-2026-08-02.md)、[`regent-repair-plan-from-claudecode-2026-08-02.md`](regent-repair-plan-from-claudecode-2026-08-02.md)

---

## 0. 决策摘要（草案）

1. **Regent 的产品身份是可治理的 Goal 交付机器**，且按方向注记：**边跑边干边想**；大模型尽量自己理清意图并给方案，人只在推演不清时辅助决断。  
2. **不设「澄清毕业才能干活」的退出条件**；错了就报错、累计经验、再争预算/资源重开。I-* 须与此对齐后再锁。  
3. **已落地的 `ProgressEvent` / `activity_log` / `tool_events` / 进程内 Agent 名册是 TRANSITIONAL（过渡观测）**，不是终态事件架构。

### 0.1 模糊 Goal（已由方向注记关闭错误二分）

- **否决**：Goal 前外挂工作室；「形成阶段退出条件」门闩哲学。  
- **采纳**：模型能拆清 → 给方案并按人步推进；拆不清 → 有限选项请人决断；全程同一 Goal，边跑边学。  
- **真正要打的结**：经验吸收闭环（例：cache 差却学不会优化）——见方向注记 §3 / §6。

---

## 1. 身份一句话（共识）

> Regent 接收自然语言 Goal，在约束、资源、授权与治理边界内，解释目标、补齐能力、形成最小人机组织，创建并运营可脱离 Core 的独立 App，依据可验证外部结果持续调整，直至 ACHIEVED / EXHAUSTED / FAILED / CANCELLED。

**不是**：更强的单一 Agent；会话内存即真相；生成者自评即成功；默认多 Agent / 开放工具市场。

证据链（差异化）：

`GoalSpec → Evidence → Hypothesis → Requirement → Build → Preview → Observation → Gate → Decision`

---

## 2. 交付机器不变量（团队共识）

每条：**必须保持** / **实现锚点** / **禁止的抄法**。

| ID | 不变量 | 实现锚点（示意） | 禁止 |
|---|---|---|---|
| I-1 | Goal 是唯一必需用户输入；GoalSpec 是解释结果 | DEFINITION ATTR_1；Goal 创建/解释路径 | 要求用户先写完整 PRD 才能启动 |
| I-2 | 领域状态只经版本化 Command 转移 | `domain/transitions.py` GoalCommand / expected_version | Agent 或会话直接改 `goal.status` |
| I-3 | PostgreSQL + Outbox 是事实源；Conversation/UI metadata 不是 | Tech-Spec §2；`execution_events` / dispatcher | 以 REPL 内存或 message.metadata 当权威 |
| I-4 | 主链 = Outbox → Worker lease → Orchestrator | `runtime/dispatcher.py`；`worker/main.py` | 进程内长会话驱动一切副作用 |
| I-5 | 外部/不可逆副作用须经 Permit（及 ExternalOperation） | `permit_service.py`；Run PERMIT_* | Skill/MCP/工具自行授权 bash·部署·外发 |
| I-6 | LLM/Agent 只提议；Application Service 写聚合 | `execution_orchestrator.py` | Agent 直写 Goal/Work/Run |
| I-7 | 真审批 ≠ 交付缺口软暂停 | `delivery_gap_recovery` soft-pause；HumanTask | 缺口做成「总是允许」权限卡 |
| I-8 | 能力阶梯 REUSE→…→BUILD→人类最后 | `delivery_gap_recovery`；`delivery_success_policy` 硬顶 | 无限自动重试或跳过人类顶 |
| I-9 | 成功 = 外部可验证结果，非生成者自评 | DEFINITION ATTR_6；`verification_allows_achieve` | 内部 smoke / 聊天满意 = ACHIEVE |
| I-10 | 独立 VerificationAgent；生成者不得自批自发布 | `agent/verification.py` | 把 verifier 并回 coding agent 自评 |
| I-11 | Submit 契约：无 submit + 非空文件 ⇒ 拒 RC | `agent_runner` / `tools.submit` | 「模型说完」即交付完成 |
| I-12 | 同轨迹预算化 repair；禁冷启动递归自修 | `repair_policy`；`agent_runner` | 验证失败就 `self.run()` 新开世界 |
| I-13 | Accepted Snapshot + 晋升哈希；REVISE 从 accepted | `accepted_workspace.py` | 失败草稿当成功基线 |
| I-14 | 生成策略资格阶梯 + kill switch + in-flight 冻结 | `generation_strategy_policy` | 默认宣称 agentic；飞行中换生成器 |
| I-15 | 组织是手段；默认强单 Agent | PRD §10 / DEFINITION ATTR_4 | 默认自由多 Agent / 自适应拓扑 |
| I-16 | 子代理侧链：父上下文只收 summary+artifact+usage | `subagent.py` `sidechain_omitted` | 把子对话全文灌回父上下文 |
| I-17 | Prompt 布局：volatile 在 conversation 后（前缀缓存） | `context_assembler.py` | 每轮把 workspace 全文塞进稳定前缀 |
| I-18 | Core ≠ Generated App | DEFINITION ATTR_5 | 把业务场景反向固化进 Core |

### 2.1 评审检查句（贴 PR）

合并涉及 Agent / Orchestrator / Console 观测的变更时，作者须能回答：

1. 是否仍由 Outbox/状态机闭环？（I-2/I-3/I-4）  
2. 是否扩大了无 Permit 的副作用面？（I-5）  
3. 是否把会话/metadata 写成了新真相源？（I-3）  
4. 是否削弱 submit / 独立 verify / accepted 链？（I-10–I-13）  
5. 软暂停是否被做成权限卡？（I-7）

任一「是削弱」→ 默认拒绝或升 DecisionRecord。

---

## 3. 过渡观测态（TRANSITIONAL）— 不是终态架构

### 3.1 已落地、明确标为过渡

| 构件 | 位置 | 过渡原因 | 终态方向（未开工，仅方向） |
|---|---|---|---|
| `ProgressEvent` | `agent/progress_event.py` | 结构化进度的**进程内/回调**载体；无持久 event_id/parent 树 | 精简 `RegentEvent` 判别联合 + 持久 append 存储 |
| `goal.metadata.tool_events` | `live_action.py` | 环形快照（≤20），覆盖写、非审计日志 | 同一事件流的派生视图或废弃 |
| `goal.metadata.activity_log` | `live_action.py` | 环形快照（≤80），随 metadata 膨胀 | append-only events 表 / Outbox 旁路投影 |
| `goal.metadata.live_action` | `live_action.py` | 控制台「现在在干什么」投影，合法但非事件史 | 保留为**投影**，非事件源 |
| `GET /v1/goals/{id}/activity` | `api/goals.py` | 读上述 metadata 快照 | 改为读持久事件流（SSE/轮询皆可） |
| `subagent_runtime` 进程字典 | `application/subagent_runtime.py` | worker 重启即丢；非跨副本 | 事件 `agent_*` 落库 + 注册表投影 |
| `GET /v1/goals/{id}/agents` | `api/goals.py` | 合并进程内名册 | 以持久运行态为准，拓扑名册仅 fallback |

### 3.2 过渡态使用规则（共识）

**允许**

- 控制台过程可见、工具名/轮次/粗 token 提示  
- 止血级 UX（SSE 主通道、滚动粘底、hint 分槽）继续依赖 `live_action` 投影  
- 为终态事件协议做适配层时，**暂时**仍写入 metadata（双写期须有下线计划）

**禁止**

- 把 `activity_log` / `tool_events` 当作审计、计费、资格晋升或门禁证据  
- 在 metadata 环形缓冲上实现 parent_tool_use_id 树、跨 Run 回放、正式 SLA  
- 新功能「只接 metadata、永不定持久事件」而不声明 TRANSITIONAL  
- 文档/对外宣称「事件协议已完成」——**未完成**；当前仅为过渡观测

### 3.3 代码标注约定

过渡构件文件头或 API docstring 须含：

```text
TRANSITIONAL OBSERVABILITY — not the durable event truth source.
See docs/decision-note-delivery-machine-invariants-2026-08-02.md §3.
```

终态落地后：删过渡双写、改 API 读持久流、本注记 §3 改为 SUPERSEDED 并链新 ADR。

---

## 4. 与外部借鉴（含 Claude Code）的关系

1. **先通读自身 + 遵守本文不变量**，再谈吸收。  
2. 可讨论的借鉴面（仍须单独设计，本文不批准开工）：精简事件协议、Skills 渐进披露/硬匹配、usage 锚点与 cache 断点、计划驱动受控子代理、只读工具并发、投影层流式。  
3. **明确禁借**（与不变量冲突）：会话作真相、Agent 直写状态、同循环 Esc 隐式改目标、Skill 授权限、缺口「总是允许」、内部 smoke=ACHIEVE、默认自由 fork 拓扑。  
4. [`regent-repair-plan-from-claudecode-2026-08-02.md`](regent-repair-plan-from-claudecode-2026-08-02.md) 在本文 ACCEPTED 前**不得**作为编码基线；仅作对照输入。开工须新 DecisionNote 声明服务哪些 I-*、不碰哪些红线、如何回滚。

---

## 5. 不做（本注记范围）

- 不在本注记内实现 events 表 / RegentEvent / 流式 / AgentTool  
- 不修改 FROZEN 的 `REGENT-DEFINITION-1.0.txt` 正文  
- 不开启 canary、不宣称 GQ-4 / 默认 agentic  

---

## 6. 验收（共识落地）

- [x] 本文进入 `docs/` 并在 `docs/README.md` 登记为 ACCEPTED  
- [x] 过渡观测相关源码带 TRANSITIONAL 标注  
- [ ] 后续涉及观测/Agent 循环的 PR 描述引用 I-* 或显式声明例外  

**一句话**：守住交付机器骨架；过渡观测可以有、但必须标明是脚手架，不是地基。
