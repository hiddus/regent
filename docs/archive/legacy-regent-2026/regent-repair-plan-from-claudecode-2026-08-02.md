# Regent 修复计划（取自 Claude Code 对标分析）

> **门禁（2026-08-02）**：本文仅为对照输入，**不得作为编码基线**。  
> 团队共识与不变量见 [`decision-note-delivery-machine-invariants-2026-08-02.md`](decision-note-delivery-machine-invariants-2026-08-02.md)。  
> 已落地的 ProgressEvent / activity_log 等为 **TRANSITIONAL** 观测，不是终态事件架构。  
> 任何吸收开工须新 DecisionNote：服务哪些 I-*、不碰哪些红线、如何回滚。

> 来源：对 `D:\workspace\claude-code-cli-master`（Claude Code CLI 抽取源码，约 1903 文件）的产品 + 技术双视角审计，
> 叠加此前两份 Regent 内核/控制台审计（`agent-core-vs-claudecode-audit-2026-08-02.md`、`console-observability-gap-2026-08-02.md`）。
> 目标：把"用户看不到任何过程"的控制台体验和"Agent 内核缺能力"两件事一次性收敛到一个工程路线里。

---

## 0. 一句话路线

**先补"事件协议"这个地基（让数据从源头流出来），再做控制台的增量渲染与真实运行态面板，最后把缓存/Skills/子 Agent 三个内核能力按 Claude Code 的成熟模式补齐。**
纯前端的小修（滚动劫持、轮询）只能止血，治本是事件流 + 由真实运行态驱动的面板。

---

## 1. 专家共识：最该先抄的 6 件事

### 产品视角（控制台）
1. **判别联合事件流 + 结构化 payload + `parent_id` 归属**，废弃"中文字符串 + `startswith` 反解"——一次性打通数据源头死路（断点 1/2/3）。
2. **append-only 增量 reducer + FlushGate**：历史回填与实时尾巴不交织、心跳类原地替换、状态类追加——解决滚动被劫持、翻不动历史（断点 6）。
3. **计划与 Agent 名册做成由真实运行态驱动的独立面板**：TodoWrite 式按 agentId 分桶的共享状态 + 独立 endpoint；AgentProgressLine 数据形状 + TaskState 注册表——替掉空 endpoint 与假名册（断点 4/5）。

### 技术视角（内核）
4. **事件协议先行**：落一个带 `session_state_changed`(turn 边界) / `post_turn_summary`(submit+状态) / `api_retry`(repair) / `stream_event`(文本增量) / `result`(token/成本) 的判别联合——它是后面所有可观测能力的载体。
5. **缓存三件套**：真实 usage 锚点计 token + 单 message `cache_control` 断点 + 把 skills/tools/agents 清单从 system 移到 delta attachment；再加两阶段断点检测进 probe。
6. **Skills 改成结构化条件激活**：用 `paths`/阶段/产物类型 glob 硬匹配替掉英文 `applies_when` 语义判断；配 1% 预算清单 + 250 字截断 + 调用时才加载全文。

---

## 2. 缺口 → Claude Code 模式 → 采纳动作 总表

| Regent 缺口 | Claude Code 模式（文件） | Regent 采纳动作 |
|---|---|---|
| AgentLoop 只发 `tool_call` 一种事件 | 判别联合 `SDKMessageSchema`（entrypoints/sdk/coreSchemas.ts:1854，24 成员，含 `session_state_changed`/`post_turn_summary`/`api_retry`/`stream_event`/`result`） | 定义 `RegentEvent` Pydantic 判别联合，每条带 `event_id/run_id/parent_tool_use_id`；`agent_runner.py` 补齐 7+ 事件类型 |
| 结构化数据被拼成中文再反解 | `createProgressMessage({toolUseID,parentToolUseID,data})`（utils/messages.ts:603）+ `Tool.ts:305` 判别 `Progress` | 事件体保留 `{type, tool_use_id, parent_tool_use_id, payload}`，中文文案只在前端渲染时生成 |
| 后端写 A 前端读 B（tool_events 死路） | 子 agent 消息靠 `parent_tool_use_id` 归属父调用（utils/queryHelpers.ts:119） | 事件统一写**一张 events 表**，靠 `parent_id` 建树，前端从同一 SSE 流读；拆掉 `goals.metadata_json` / `conversation_messages.metadata` 双写 |
| 前端每秒全量替换数组→滚动劫持 | append-only reducer（screens/REPL.tsx:2644）+ FlushGate（bridge/flushGate.ts:17） | `useWorkspace.ts`/`MessageList.tsx` 改为按 `uuid` 去重追加；历史分页前插、实时流入队 |
| 计划无 endpoint、看不见 | TodoWrite 写入 `appState.todos[agentId]`、render 返回 null（Tool.ts:560） | `ExecutionPlanItem` 加 `GET /v1/goals/{id}/plan` + `plan_updated` 事件；计划按 agentId 分桶 |
| Agent 面板是假名册 | `AgentProgressLine`（components/AgentProgressLine.tsx:6）+ `TaskState` 注册表（tasks/types.ts:12） | 由 `SubagentRunner` 注册表驱动：`agent_started/agent_activity/agent_finished` 三事件写 registry |
| 产出代码看不见 | `StructuredDiff.tsx:32` + `DiffFileList.tsx:9`(MAX_VISIBLE_FILES=5) | 加 workspace 文件树 + 选中文件 hunk diff 面板；被拒编辑也画 diff |
| token/成本不可观测 | 双层计量 `cost-tracker.ts:1`（全局+per-agent `ProgressTracker`）；OTel counter 同源（:291） | 本地账本同一次记账同时打指标；`cacheRead/cacheCreation` 作独立 counter，命中率 = cacheRead/(cacheRead+cacheCreation+input) |
| 缓存命中率不可观测、无断点检测 | `recordPromptState`+`checkResponseForCacheBreak`（promptCacheBreakDetection.ts:247/437）+ 三重 hash | 把 `system_hash/tools_hash/cache_control_hash/per_tool_hash` 进 probe；break 带 reason 进 ops |
| 动态清单（skills/tools/agents）bust 缓存 | delta attachment（AgentTool/prompt.ts:47；utils/attachments.ts:686） | system 按"永不变/每会话/每轮"三段切，只在第一段末放断点；动态清单改对话内 delta 消息 |
| Skills 中文零命中、全量注入 | 1% 预算清单 + 250 字截断 + delta 注入 + 按需全文（SkillTool/prompt.ts:21；loadSkillsDir.ts:159） | `applies_when` 改 structured `paths`/阶段/产物类型 glob 硬匹配；1% 预算 + 调用才加载 |
| 子 Agent 不能主动 spawn | `AgentTool` fork 变体复用父 renderedSystemPrompt 字节（forkSubagent.ts:60） | spawn 做成模型可调用的工具；fork 复用父 system 字节共享缓存；结果只回 summary+artifact_path+usage |
| 无流式/中断 | 反向控制通道 `control_request/control_response`（bridge/bridgeMessaging.ts:243） | 中断做成与事件流对称的双向协议，非 HTTP 特例 |
| 上下文压缩弱 | microCompact 清旧 tool_result 正文（microCompact.ts:40）+ usage 锚点（tokens.ts:226）+ 5 档状态 + 3 次熔断（autoCompact.ts:33/70/93） | 先上 microCompact + usage 锚点 + 5 档状态 + 熔断；摘要 prompt 抄"逐字引用当前工作" |

---

## 3. 分阶段执行计划

### Phase 0 — 控制台即时止血（纯前端，零后端契约改动，1–2 天）
> 对应 `console-observability-gap` 的 P0。不改事件协议也能先让"能正常对话"成立。

- [ ] `useWorkspace.ts:276`：删掉每秒 3 个 REST 轮询；SSE 改用增量订阅，不再每秒整拉。
- [ ] `MessageList.tsx` / `useWorkspace.ts`：`setMessages(msgs)` 全量替换 → 按 `uuid` 去重 `append`（参考 REPL.tsx:2644）。
- [ ] `App.tsx:21`：滚动劫持——仅在"用户在底部且来新消息"时 `scrollIntoView`；用户手动上滚期间锁定。
- [ ] `showHint` 改为独立状态槽，不被每秒 tick 冲掉。
- [ ] 渲染去 O(n²)：消息列表用 `React.memo` + key 稳定；工具活动流折叠。
- [ ] 门禁清零：修 2 个陈旧计数断言（`test_budget_ledger.py` 7==5、`test_goal_execution_contract.py` 17==16）+ 2 个 orchestrator `MagicMock` async 上下文管理器（新增 `_record_delivery_state` 后失效）。
- **验收**：控制台可平滑翻历史、不卡顿、提示稳定；`pytest tests/unit tests/architecture` 全绿。

### Phase 1 — 事件协议地基（内核 + 一个 SSE 出口，2–3 天）★ 最关键
> 两位专家一致判定这是所有"看得见"的前提。

- [ ] 定义 `RegentEvent`（`core/src/regent/agent/events.py`）：Pydantic 判别联合，必带 `event_id / run_id / parent_tool_use_id`；成员至少：
  `turn_start / turn_end / assistant_text / tool_call / tool_progress / plan_updated / budget / repair / submit / result / agent_started / agent_activity / agent_finished / compact_boundary`。
- [ ] `agent_runner.py` 主循环（当前 `:374` 唯一 `on_event` 点）改为在：每个模型调用前/后、每轮 tool_call、plan 变更、repair 阶段进入/退出、submit、子 agent 起止、compact 触发处 emit 对应事件。
- [ ] 新增 `events` 表（或复用 run_ledger 的 append 段），事件落盘；**废弃** `generator.py:151` 的 `"执行工具 {tool}：{preview}"` 拼串，改为结构化 `ProgressEvent`。
- [ ] 新增 `GET /v1/goals/{id}/activity`（SSE 或轮询都可），从 events 表推流；前端 `progressNodes.ts` 不再靠 `startswith` 反解，改为读结构化字段。
- [ ] 拆掉 `live_action.py:78-83` 写 `goals.metadata_json` 与前端读 `conversation_messages.metadata` 的双写死路，统一走 events 表。
- **验收**：SSE 能推 turn 边界、模型文本增量、tool_call、submit；`extractToolTrace()` 不再永远返回空。

### Phase 2 — 真实运行态面板（前端 + 2 个 endpoint，2–3 天）
- [ ] **计划面板**：`GET /v1/goals/{id}/plan` 暴露 `ExecutionPlanItem`（已有 model/service）；`plan_updated` 事件驱动重渲染；按 agentId 分桶。
- [ ] **Agent 名册面板**：`agents.ts` 假名册 → 由 `agent_started/agent_activity/agent_finished` 事件驱动的真实 `TaskState` 注册表（照抄 `AgentProgressLine` 数据形状：agentType/name/color/toolUseCount/tokens/lastToolInfo/isResolved/isError）。
- [ ] **产出代码面板**：新增 workspace 文件树 + 选中文件 hunk diff（参考 `StructuredDiff.tsx`/`DiffFileList.tsx`）；被拒编辑也可见。
- [ ] **成本/缓存状态条**：顶部常驻显示 token（per-agent + per-goal 两层）、cache 命中率、当前进行中的计划项（参考 `cost-tracker.ts` + `Spinner.tsx:162`）。
- [ ] **"现在在干什么"最小模型**：`currentActivity + 最近 N 条`（参考 `bridge/sessionRunner.ts` 的 `SessionActivity` 环形缓冲，MAX_ACTIVITIES=10）。
- **验收**：5 条用户投诉（目标拆解/计划/产出代码/运行状态/参与的 Agent）全部可见且实时更新。

### Phase 3 — 缓存工程（内核，2–3 天）
- [ ] token 计数锚定真实 usage（参考 `tokens.ts:226`）：从最近带真实 `usage` 的回包回溯，只估增量——比 CJK 修正更根本。
- [ ] `context_assembler.py`：system 按"永不变/每会话变/每轮变"三段切，只在第一段末放 `cache_control` 断点；skills/tools/agents 动态清单移出 system，改对话内 delta 消息（参考 forkSubagent 的 10.2% 教训）。
- [ ] 两阶段断点检测：`recordPromptState`（12 维快照 + 三重 hash）/`checkResponseForCacheBreak`（掉幅>5% 且绝对>2000 token 才算 break）进 `ops/probe_cache_hit.py`；break 事件带 reason 进 ops。
- [ ] TTL 资格 latch 到会话级 state，防中途翻转 bust 缓存。
- **验收**：probe 能输出"哪个清单变了→为什么掉缓存"；命中率进入可观测仪表。

### Phase 4 — Skills 结构化条件激活（内核，1–2 天）
- [ ] `skills.py`：`applies_when` 英文语义匹配 → 结构化触发器（`paths` glob / milestone 阶段 / 产物类型 / 显式 tag）硬匹配（参考 `loadSkillsDir.ts:159` `conditionalSkills`）。
- [ ] 清单 1% 预算 + 每条 `whenToUse` 截断 250 字（参考 `SKILL_BUDGET_CONTEXT_PERCENT=0.01`）。
- [ ] delta 注入：只发本会话未发过的 skill；调用时才把 SKILL.md 正文包成 meta user message 注入（渐进披露，替掉 manifest 全量注入）。
- **验收**：中文验证目标命中率从≈0 提升到可度量；manifest 不再全量占上下文。

### Phase 5 — 子 Agent 主动 spawn + 流式/中断（内核，3–5 天）
- [ ] 把子 Agent 调度从"仅 milestone 串行"升级为模型可调用的 `AgentTool`：支持全新 agent 与 fork 自身两种；fork 复用父已渲染 system prompt 字节以共享 prompt cache（参考 `forkSubagent.ts:60`）。
- [ ] 子 Agent 结果回主上下文只回 `summary + artifact_path + usage`，prompt 显式禁止主 Agent 去读产物全文（"Don't peek / Don't race"）。
- [ ] 流式：模型文本边生成边推（Phase 1 的 `assistant_text`/`stream_event` 已铺路）。
- [ ] 中断/反控通道：对称 `control_request/control_response`（参考 `bridgeMessaging.ts:243`），非 HTTP 特例。
- [ ] 工具能力位补齐：`interruptBehavior` / `maxResultSizeChars`（参考 `Tool.ts:416/466`），为中断与大产物兜底。
- **验收**：模型可在运行中主动派子 Agent；可中途中断；长产物不再撑爆上下文。

---

## 4. 与既有计划的衔接

- 覆盖 `console-observability-gap-2026-08-02.md` 的 P0（Phase 0）+ P1（Phase 1 事件源）+ P2（Phase 2 面板）+ P3（Phase 5 流式/中断/canary）。
- 覆盖 `agent-core-vs-claudecode-audit-2026-08-02.md` 的 P0（Phase 1/3/5 部分）+ P1（Phase 3/4）+ P2（Phase 5）。
- 既有 W4 已闭环的 CJK token 估算、cached_tokens 落 probe、submit 回归、Skills 中文路由、资格态 INTERNAL_DOGFOOD **保留不动**；本计划在其上叠加事件协议与结构化面板。

---

## 5. 优先级与建议落地顺序

**先做 Phase 0 + Phase 1**：Phase 0 零风险立刻缓解"没法正常对话"，Phase 1 是后面所有可观测能力的唯一载体——没有它，Phase 2 的面板永远是无源之水。
Phase 2 直接闭环用户 5 条投诉。Phase 3–5 是内核能力补强，可在控制台可用后按资源推进。

> 关键纪律提醒（沿用 M5 文档）：Skills 扩面仍是"先证后扩"，Phase 4 的条件激活要先在 3–5 个中文目标上验证命中率再推广，避免重蹈 R0–R3 波次违反纪律覆辙。
