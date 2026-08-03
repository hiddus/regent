# 控制台可观测性缺口诊断与修复方案（2026-08-02）

> 触发：用户反馈「控制台交互界面体验非常有问题，很难实时持续对话，每个阶段的产出根本看不到，
> 目标拆解看不到，计划看不到，产出代码看不到，运行状态看不到，参与的 Agent 执行事物看不到」。
>
> 本文分两部分：**A. 实现复查（W4 后的现状）**、**B. 控制台缺口根因与修复方案**。

> **状态更新（2026-08-02）**：P0 止血与部分 REST 出口已落地（`0ce5963`）。  
> `ProgressEvent` / `activity_log` / `tool_events` / 进程内 agents **正式标为 TRANSITIONAL**，不是终态事件架构。  
> 团队共识：[`decision-note-delivery-machine-invariants-2026-08-02.md`](decision-note-delivery-machine-invariants-2026-08-02.md)。

---

# A. 实现复查（相对 `agent-core-vs-claudecode-audit-2026-08-02.md` 的增量）

## A.1 已修（W4 波次，见 `agent-core-next-wave-plan-2026-08-02.md`）

| 项 | 上次审计结论 | 现状 | 证据 |
|---|---|---|---|
| CJK token 估算 | `len//4`，中文低估 3–4 倍 | **已修** | `compact.py:49-60` `estimate_text_tokens()` 改为 `cjk*1.0 + other*0.25`，注释标 `W4-P0`；另加 `prompt_tokens` EMA 校正 |
| cache 命中率出口 | 只落本地 ledger，无 probe | **已修** | `ops/probe_cache_hit.py` 扫 `.regent_run_ledger.json` 出中位数/均值 |
| subagent submit 回归 | `ArtifactIncompleteError` | **已修** | P0-3 DONE |
| Skills 中文路由 | 全英文 `applies_when`，中文目标零命中 | **已修** | P1-2/3 DONE，消融报告 20/20 非空 |
| `context_window_tokens` 写死 128k | 未传参 | **已修** | P1-4，S0 配置 128000 |
| live golden 车道 | 无 live model 验证 | **已修** | `live_model_v2_green=true`，报告 `docs/agentic-live-golden-report-2026-08-02.json` |
| 资格态 | `DISABLED` | **升至 `INTERNAL_DOGFOOD`** | S0 部署验证 |

结论：上次审计点名的 4 个 P0 里，**3 个已闭环**。这一波质量是实的，不是文档自嗨。

## A.2 仍未动

| 项 | 状态 | 影响 |
|---|---|---|
| 流式输出 / MCP / 并行 gather | `P2 = DEFERRED` | 控制台无法逐字显示，模型输出必须等整轮结束 |
| 生产流量 | `canary_percent=0`、`canary_gate=false`、默认 `artifact-backed` | **Agent 内核在生产仍是 0 流量**，DOGFOOD 只是资格态 |
| Web 一等公民工具 | 未做 | 50 个验证目标中前 28 个依赖网络获取，仍靠 `curl` 白名单绕行 |

## A.3 门禁欠账：6 项测试失败

`tests/unit` + `tests/architecture`（`.venv/Scripts/python.exe -m pytest ... --basetemp=$TEMP/rgt_recheck`）：

| # | 测试 | 失败原因 | 性质 |
|---|---|---|---|
| 1 | `test_goal_execution_contract.py::test_console_starts_goal_and_polls_persistent_progress` | `assert "3000" in hooks_src` 失败 | **信号性失败**：前端轮询间隔已从 3000ms 改为 `active ? 1000 : 10000`，测试未跟。见 B.4 |
| 2 | `test_evidence_chain_integrity.py::test_p1_event_catalog_covers_lifecycle` | `assert 17 == 16` | 陈旧计数断言（上次审计已报，未修） |
| 3 | `test_goal_execution_contract.py::test_worker_registers_all_p1_main_chain_events` | 同上一族 | 陈旧计数断言 |
| 4 | `test_p2_committed_packages.py::test_bootstrap_profiles_include_certified_pair` | certified 集合多出 `fastapi-web-v1`、`flask-web-v1` | 扩包未更新断言 |
| 5-6 | `test_execution_orchestrator.py::test_recover_or_wait_after_deploy_gap_*` | `TypeError: 'coroutine' object does not support the asynchronous context manager protocol`（`execution_orchestrator.py:4205`） | **测试 mock 欠账**：新增 `_record_delivery_state`（CD-1.2/CD-5）进入 `_apply_delivery_verdict` 后，老测试的 `MagicMock()` sessions 不支持 async CM |

全部 6 项都不是产品代码回归，但**长期红灯会掩盖真实回归**，必须清零。

---

# B. 控制台可观测性缺口

## B.1 用户的 7 个「看不到」→ 根因矩阵

| 用户反馈 | 根因层 | 精确位置 |
|---|---|---|
| 很难实时持续对话 | 交互层 | `useWorkspace.ts:276` 每秒 3 个 REST + `App.tsx:21` 每秒强制 smooth 滚动 |
| 每个阶段的产出看不到 | 事件源 | `agent_runner.py:374` 全程只有 1 个事件发射点 |
| 目标拆解看不到 | 事件源 | 无 milestone / 拆解事件 |
| 计划看不到 | 出口缺失 | `ExecutionPlanItemModel` 有表有 service，`api/` 零 endpoint |
| 产出代码看不到 | 出口缺失 | 只有 `GET /app-delivery/{id}/download`（zip），无文件树/内容/diff |
| 运行状态看不到 | 有损往返 | 结构化 event → 中文字符串 → `startswith` 反解析 |
| 参与的 Agent 执行事务看不到 | 数据错配 | Agent 名册来自 `topology.roles` 静态推导，与真实 `SubagentRunner` 无关 |

## B.2 层 1 — 事件源：AgentLoop 只发一种事件

`agent_runner.py` 整个 614 行主循环里，`on_event` **只有一个调用点**：

```python
# agent_runner.py:374-383
if on_event is not None:
    await on_event({
        "type": "tool_call",
        "turn": turn,
        "tool": call.name,
        "args_preview": _preview(call.arguments),
        "result_preview": _preview(result_text),
    })
```

**缺失的事件类型**（Claude Code 全部具备）：

- `turn_start` / `turn_end`（轮次边界 + 本轮耗时）
- `assistant_text`（模型的思考/说明文本；当前无流式，整轮结束才有）
- `plan_updated`（`todo_write` 已写入 `ExecutionPlanService`，但不发事件）
- `budget_tick`（token 消耗 / 剩余预算 / cache 命中）
- `repair_phase_start`（进入 repair、gap 列表）
- `compaction`（micro_compact / autoCompact 触发）
- `submit`（提交产物清单）
- `subagent_start` / `subagent_end`

**修复**：在 `agent_runner.py` 增设统一 `_emit()`，在上述 8 个点发射结构化事件。这是所有后续可观测性的地基 —— 不做这一步，前端做什么都是空转。

## B.3 层 2 — 有损往返 + 写错位置

### B.3.1 结构化 → 字符串 → 再解析

```python
# generator.py:149-151  结构化事件被压成中文字符串
tool = str(event.get("tool") or "")
preview = str(event.get("args_preview") or "")
await on_progress(f"执行工具 {tool}：{preview}"[:200])

# execution_orchestrator.py:1606-1611  再用字符串前缀反推 tool 名
if summary.startswith("执行工具 "):
    rest = summary[len("执行工具 ") :]
    if "：" in rest:
        tool = rest.split("：", 1)[0].strip()
```

`turn`、`args_preview`、`result_preview` 在这一跳全部丢失。

**修复**：`on_progress: Callable[[str], ...]` 升级为 `on_progress: Callable[[ProgressEvent], ...]`，
`ProgressEvent` 为结构化 dataclass；字符串 summary 只作为 `event.summary` 字段之一。

### B.3.2 tool_events 读写位置错配（前端恒空）

```python
# 后端写入 goals.metadata_json
# live_action.py:78-83
def _append_tool_event(meta, tool_event, tool):
    events = meta.get("tool_events") or []
    events.append(dict(tool_event))
    meta["tool_events"] = events[-_MAX_TOOL_EVENTS:]   # 写进 goal.metadata_json
```

```typescript
// 前端从 conversation_messages.metadata 读取
// progressNodes.ts:263-267
function extractToolTrace(m: Message): string[] {
  const meta = m.metadata || {}          // ← Message.metadata，不是 goal.metadata
  const events = meta.tool_events        // ← 恒为 undefined
```

**结论**：`toolTrace` 永远返回 `[]`。后端辛苦维护的 20 条工具活动流，前端一条都读不到。

同时前端 `LiveAction` 接口（`liveActivity.ts:5-11`）**根本没有 `tool` / `tool_events` 字段**，
`parseLiveAction()` 也不解析它们 —— 即使位置改对了，也会被丢弃。

**修复**：三选一，推荐第 3 种。
1. 前端改读 `status.goal.metadata.tool_events`（最小改动，但仍是覆盖式快照）
2. 后端把 tool_event 也写一份进 message metadata（消息膨胀，不推荐）
3. **新增 `GET /v1/goals/{goal_id}/activity`**，返回结构化活动流（含 turn / tool / args / result / 时间戳），SSE 增量推送 —— 与 B.2 的事件源改造配套

## B.4 层 3 — 交互体验：轮询把 UI 打烂

这是「很难实时持续对话」的**直接元凶**，且与 A.3 的失败测试 #1 互为印证。

### 请求风暴

```typescript
// useWorkspace.ts:276
const interval = setInterval(tick, active ? 1000 : 10000)
```

`tick()` 每次执行 3 个 REST 请求（`loadStatus` + `loadMessages` + `api.listProjects`）。叠加：

- SSE 后端本身在 0.25–1.0s 轮询 DB（`events.py:_ADAPTIVE_POLL_MIN/MAX`）
- SSE `status_change` handler 里再 `void syncProjectView()`（又是 status + messages）
- SSE `new_message` handler 里再 `loadStatus`

**ACTIVE 状态下每秒 ≥3 个 REST + SSE DB 轮询**，且互相触发。

### 滚动劫持（最致命）

```typescript
// useWorkspace.ts:72  全量替换，每秒产生新数组引用
setMessages(msgs)

// App.tsx:21-23  依赖 ws.messages 引用
useEffect(() => {
  messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
}, [ws.messages])
```

即使消息内容一字未变，每秒的新数组引用都会触发一次 smooth 滚动。
**用户想往上翻看历史，1 秒内就被强制拽回底部。**

### 提示被覆盖

```typescript
// useWorkspace.ts:243  tick 每秒执行
if (live) { showHint(`Core：${live.summary}`); return }
```

`handleSend` 里设的「Core 正在处理你的指令...」1 秒后即被冲掉。

### 渲染复杂度 O(n²)

`MessageList` 把整个 `messages` 数组传给每个 `MessageItem`，
`goalAlreadyMoving()` / `approveAlreadyDone()` 对每条消息都做一次全表 `some()` 扫描。
消息数 n 增长时渲染成本按 n² 上升。

### 修复清单

| 编号 | 动作 | 位置 |
|---|---|---|
| C-1 | 消息按 `ordinal` 增量 merge，内容不变时复用原数组引用 | `useWorkspace.ts:loadMessages` |
| C-2 | 自动滚动加「用户已上滑则不跟随」判定；依赖改为 `messages.length` + 末条 id | `App.tsx:21` |
| C-3 | 删除 1s `setInterval`，SSE 为主通道，降级轮询设为 10s 且只在 SSE 断开时启用 | `useWorkspace.ts:276` |
| C-4 | `showHint` 区分「用户操作提示」与「Core 状态」，两个独立槽位，互不覆盖 | `useWorkspace.ts` + `Composer` |
| C-5 | `MessageItem` 依赖的判定结果在 `MessageList` 里一次性预计算成 Map | `MessageList.tsx` |
| C-6 | 修复测试 #1：断言改为「存在降级轮询且 SSE 为主」的语义断言，而非源码里含 `"3000"` 字面量 | `test_goal_execution_contract.py:127` |

## B.5 层 4 — 出口缺失

### B.5.1 计划（目标拆解 / todo）零出口

- `ExecutionPlanItemModel`（`infrastructure/models.py`）+ `ExecutionPlanService`（`application/execution_plan.py:70`）**已存在且被 AgentRunner 写入**
- `grep -rn "execution_plan|plan_items|todo" core/src/regent/api/*.py` → **零命中**
- 前端 `api.ts` 13 个接口中无任何计划相关

**修复**：新增 `GET /v1/goals/{goal_id}/plan-items`，返回 `[{item_key, title, status, updated_at}]`；
前端在 StageBar 下方渲染为可折叠的任务清单，`plan_updated` 事件到达时增量更新。

### B.5.2 代码产出零出口

- 唯一出口：`GET /app-delivery/{project_id}/download`（打包 zip）
- **无文件树、无单文件内容、无 diff**
- `ArtifactPanel` 只有：Agent 名册 + 预览 iframe + 下载按钮 + 审阅（`JSON.stringify` 裸 `<pre>`）

**修复**：
- `GET /v1/app-projects/{id}/workspace/tree` — 文件树（路径 + 大小 + 修改时间）
- `GET /v1/app-projects/{id}/workspace/file?path=` — 单文件内容（带大小上限 + 二进制拒绝）
- `GET /v1/app-projects/{id}/workspace/diff?from=&to=` — 相邻 accepted snapshot 之间的 diff
- `ArtifactPanel` 增加「源码」页签：左树右码，`write_file`/`edit_file` 事件到达时高亮变更文件

已有基础：`accepted_workspace.py` 有 snapshot + `verify_promotion_hashes`，diff 有可靠锚点。

### B.5.3 Agent 名册是假的

```typescript
// agents.ts:75-91  从 topology.roles 静态推导
const roles = Array.isArray(topo?.roles) ? topo.roles : []
for (...) {
  agents.push({ ..., activity: ..., detail: null, is_main: false })  // detail 恒 null
}
```

后端 `_goal_agents_for_console`（`app_guidance_service.py:627`）同样只读 Hive AgentDeployment / AgentSpec，
**与真实运行的 `SubagentRunner`（`agent/subagent.py`）完全无关**。

面板上「产品 / 开发 / 质检」是按拓扑角色画出来的卡片，不是正在跑的 Agent。

**修复**：`SubagentRunner` 在 start/end 时写入运行态（subagent_id、milestone、状态、当前工具、耗时），
`GET /v1/goals/{id}/agents` 返回真实运行态，与静态角色名册合并展示（真实态优先）。

---

# C. 执行优先级

## P0（体验止血，纯前端 + 1 个测试，风险最低）

1. **C-1 / C-2 / C-3 / C-4 / C-5** — 消息增量 merge、滚动不劫持、删除 1s 轮询、提示分槽、渲染去 O(n²)
2. **C-6 + A.3 的 6 项测试** — 门禁清零

做完这一步，「很难实时持续对话」直接消失，且不依赖任何后端改动。

## P1（让内部真实活动可见）

3. **B.2 统一事件源** — `agent_runner.py` 补 8 类事件（地基，必须先做）
4. **B.3.1 结构化 ProgressEvent** — 消除字符串往返
5. **B.3.2 活动流出口** — `GET /v1/goals/{id}/activity` + SSE 增量推送，前端渲染工具活动时间线

## P2（让产出可见）

6. **B.5.1 计划出口** — `GET /v1/goals/{id}/plan-items` + 任务清单 UI
7. **B.5.2 代码出口** — workspace tree / file / diff + ArtifactPanel 源码页签
8. **B.5.3 真实 Agent 运行态** — SubagentRunner 上报 + 合并展示

## P3（对齐 Claude Code 的交互质感）

9. 模型流式输出（当前 `provider.py` 无 stream，属 W4 已 DEFERRED 的 P2）
10. 中断 / 打断当前轮次
11. 生产开 canary（当前 `canary_percent=0`，需 `sample_gates≥20`）

---

# D. 验收标准

| 项 | 验收 |
|---|---|
| P0 | 一次生成过程中，用户可稳定向上翻阅历史消息不被拽回；ACTIVE 状态下每秒 REST 请求数 ≤1；`tests/unit` + `tests/architecture` 全绿 |
| P1 | 控制台能看到「第 N 轮 · 调用 write_file · 耗时 1.2s · 累计 12k tokens · cache 命中 47%」级别的活动流 |
| P2 | 控制台能在生成过程中打开任意已产出文件查看内容，并看到计划项从 pending → done 的实时变化 |
| P3 | 模型输出逐字可见；用户可中断当前轮次 |
