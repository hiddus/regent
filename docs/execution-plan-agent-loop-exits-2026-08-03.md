# 执行计划：Agent Loop 出口落地（A0）

**日期**：2026-08-03  
**状态**：IMPLEMENTED — P0–P3 已合入主干代码路径  
**依据**：[`absorption-plan-agent-matrix-2026-08-03.md`](absorption-plan-agent-matrix-2026-08-03.md) v3.4 §A0；[`decision-note-agent-loop-exit-2026-08-03.md`](decision-note-agent-loop-exit-2026-08-03.md)  
**参考**：OpenWork（plan 批准 / permission / abort / artifacts）+ Claude Code（AskUserQuestion / 无 tool 结束 / 硬顶 / doom_loop→要人）  
**角色**：产品经理视角（体验与验收）× 技术专家视角（状态机与改动面）联合制定

---

## 0. 联合结论（一句话）

> 每一轮 Agent lease **必须**落盘 `exit_kind ∈ {COMPLETE, STOP, ASK_HUMAN}` 并给出结果包或问题单；验证失败/预算到 **不得**再自动 `SESSION_RESUME` / lesson / ATTRIBUTE_3。Session 只在人答完或用户显式继续后开下一轮。

---

## 1. 产品经理：用户可感知出口

### 1.1 三态体验

| 出口 | 对话里 | 活动流 | 右侧/结果区 |
|------|--------|--------|-------------|
| **COMPLETE** | 「本轮完成」+ 摘要 + 未决项（可空） | Agent Session：本轮已完成 | 预览、产物、证据、变更要点 |
| **STOP** | 「已停止：{原因}」+ 草稿仍在 | Agent Session：已停止 | 草稿/工作区入口；无「正在生成」 |
| **ASK_HUMAN** | 清晰问题 + 选项/输入 + 建议 | Agent Session：等待你确认 | 问题单卡片；Worker 空闲 |

禁止用户看到：无说明的反复「正在续跑 / 请求模型生成」却从不问人、从不交结果。

### 1.2 COMPLETE 结果包（对用户必含）

1. 一句话摘要  
2. 预览 URL（若有）  
3. 产物入口（源码/包）  
4. 证据/验证摘要（通过了什么；未做的也要诚实写）  
5. 本轮变更要点（≤5 条）  
6. 未决项列表（可空）  
7. `exit_kind=COMPLETE` 时间戳  

### 1.3 ASK_HUMAN 问题单（对用户必含）

1. 问题正文（人能直接答）  
2. 为何卡住（缺信息 / 需批准 / 无进展）  
3. 选项（若适用）或自由输入  
4. 建议默认项（可空）  
5. 拒绝/停止后果说明  
6. 答复后：「将在同一 Session 继续」提示  

交互：批准 / 选择选项 / 补充文字 / 拒绝→STOP 或保持暂停。  
Skills 安装授权、危险 Permit、大改 plan 批准，一律走同一问题单协议（类型字段区分）。

### 1.4 STOP 场景清单

| 场景 | 用户文案要点 |
|------|----------------|
| 用户点暂停/停止 | 已按你的要求停止 |
| 预算/轮次硬顶 | 本轮额度用尽；草稿保留 |
| 策略/权限不可恢复 | 无法继续的原因 |
| 用户拒绝 ASK | 已记录拒绝；未自动重试 |
| 系统 abort lease | 运行已中止 |

### 1.5 与 soft / ACHIEVE 的产品边界

| 允许 | 禁止 |
|------|------|
| soft 仅影响「是否继续修」的**展示建议** | soft-rescue / 剔 gap 后宣称 COMPLETE |
| ACHIEVE 仅在真实 COMPLETE（或显式人工确认交付）后 | FAIL 洗成 ACHIEVE /「已完成」 |

文案：**未完成就是未完成**；可提供「仍可查看草稿」。

### 1.6 产品明确不做（本执行计划范围外）

- 远程 Skill 商店 UI 大而全（仅预留授权安装问题单类型）  
- 运营域 Hive 多角色（S5）  
- Context Retriever 生产化（S1，出口之后）  
- 重做整套控制台信息架构  

---

## 2. 技术专家：工程落点

### 2.1 现状如何破坏出口

| 环节 | 现状 | 问题 |
|------|------|------|
| `AgentRunner` | `submit` / 预算耗尽 / 未 submit→Incomplete | 有局部终止，**无统一 exit_kind 外传** |
| `generator` + soft | FAIL/Incomplete 可洗 PASS 或再拒 | 假 COMPLETE 风险 |
| `DeliveryGapRecovery` | 默认 `SESSION_RESUME` | **无问人自动续烧**（RETRY_FOREVER 主通道） |
| ATTRIBUTE_3 | Session 缺失时 ladder | 换标签空转 |
| soft-pause | 有暂停，但文案/语义≠ ASK 问题单 | 人不知道要答什么 |
| Guidance CONTINUE | 常触发再 start/resume | 易在无出口时再点火 |

### 2.2 推荐数据模型（少表、可审计）

**推荐**：不新建第三套 loop；在 **Goal.metadata + Session.checkpoint** 落出口，Outbox/审计打事件。

```text
goal.metadata_json["agent_loop_exit"] = {
  "exit_kind": "COMPLETE|STOP|ASK_HUMAN",
  "stop_reason": "verified_pass|budget|user_abort|doom_loop|need_input|...",
  "lease_id": "<generation_run_id>",
  "session_id": "...",
  "epoch": N,
  "at": ISO8601,
  "result_bundle": { ... } | null,   // COMPLETE
  "ask_envelope": { ... } | null,    // ASK_HUMAN
  "draft_uri": "..." | null          // STOP / ASK 均可
}
```

- Session `checkpoint_json` 同步最近一次 exit（resume 用）。  
- 审计：`AGENT_LOOP_EXIT` + 既有 conversation/live_action。  
- **暂不**强制新表；若 ASK 需强任务卡，复用 HumanTask + `ask_envelope` 引用。

### 2.3 状态机

```text
RUNNING (lease)
  ├─ verify_OK                    → COMPLETE → Session 可保持 ACTIVE 但不再自动租
  ├─ user_abort | hard_budget     → STOP     → Session PAUSED
  ├─ need_input | permit | doom   → ASK_HUMAN→ Session PAUSED；Worker 释放
  └─ verify_fail（有限 in-run repair 用尽）→ ASK_HUMAN（默认）或 STOP（策略）
         禁止 → 自动 SESSION_RESUME / ATTRIBUTE_3
```

恢复：

```text
ASK_HUMAN + 人答复
  → bump_epoch + checkpoint 写入答复
  → 显式新 lease（Guidance CONTINUE / 批准回调）
STOP + 用户「恢复」→ 同 ASK 恢复路径（须新方向或确认）
COMPLETE → 仅用户新目标/新里程碑再开，不因旧 gap 自动开
```

### 2.4 禁自动续烧 — 优先砍的调用链（P0）

1. `DeliveryGapRecoveryService.recover`：有 Session 时 **不要**直接 `_resume_same_agent_session`；改为写 `ASK_HUMAN`（或达硬顶写 STOP）。  
2. `resume_after_human`：仅在 **ask_envelope 已解答** 或用户显式 CONTINUE 带方向时允许 lease。  
3. Orchestrator：Generation 结束 FAIL ≠ 再投递无出口 `GenerationRunRequested`。  
4. soft-rescue：**不得**映射为 COMPLETE；最多 STOP/ASK + 草稿。

---

## 3. 联合切片（建议 2 周主路径）

### Phase 0 — 合同与观测（0.5–1 天）

| 产出 | 验收 |
|------|------|
| 短 ADR：A0 三态 + 禁 RETRY_FOREVER | 评审签字 |
| `exit_kind` schema 文档 | 前后端字段对齐 |
| live_action 三态文案草案 | 产品过目 |

### Phase 1 — P0 禁续烧 + 落盘出口（3–5 天）【第一刀】

| 工程 | 产品验收 |
|------|----------|
| recover 主路径：gap → ASK_HUMAN（问题单）或硬顶 STOP；去掉默认自动 SESSION_RESUME | 制造验证失败：应出现问题卡，**不应**自动再烧模型 |
| 每轮 lease 结束写 `agent_loop_exit` | 日志/元数据可见 exit_kind |
| BudgetExhausted → STOP + 草稿 | 对话说明额度用尽，无自动再开 |
| Guidance：无未答 ASK 时禁止「空白续跑」；有答复才续 Session | 答完问题后才继续；不答就停着 |

**测试**：单测 recover 不再发无问人 resume；集成：失败→ASK→答→同 session_id 再跑→COMPLETE 或再 ASK。

### Phase 2 — 结果包与问题单体验（2–3 天）

| 工程 | 产品验收 |
|------|----------|
| COMPLETE `result_bundle` 填满并进对话/活动流 | 用户能看到摘要+预览+未决项 |
| ASK `ask_envelope` + HumanTask/卡片 | 选项可点；拒绝→STOP |
| 用户 PAUSE → STOP 语义对齐 | 活动流「已停止」 |

### Phase 3 — doom_loop + soft 解耦（2–3 天）

| 工程 | 产品验收 |
|------|----------|
| 启发式：同 gap_kind 连续 ≥N 或工作区无实质 diff ≥N → ASK（禁止再自动修） | 空转几次后必问人 |
| soft：默认不影响 exit_kind；`soft_pass` 不得当 COMPLETE | 控制台仍显示未通过项（若有） |
| ATTRIBUTE_3 默认关闭/仅显式 flag | 主路径日志无 CONFIGURE→COMPOSE 叙事 |

### Phase 4 — 加固与对齐 Claude/OpenWork 细节（可选，1–2 天）

- Runner 暴露结构化 `stop_reason`（对齐 CC result）  
- 大改前 plan 批准（OpenWork 开跑闸门）— 可配置  
- abort 取消 in-flight lease  

**预估合计**：约 **8–12 人天**（1 名后端为主 + 前端/文案穿插）。

---

## 4. doom_loop 启发式（工程建议）

```text
IF same delivery_gap_kind streak >= 2 AND auto resumes >= 1  → ASK_HUMAN
IF workspace content hash unchanged across 2 leases           → ASK_HUMAN
IF SESSION_RESUME count without COMPLETE in window >= 3        → STOP or ASK
```

阈值进 Session checkpoint，供问题单「为何卡住」引用。

---

## 5. 依赖顺序

```text
ADR + schema
  → P0 禁自动 SESSION_RESUME / 写 exit_kind
  → ASK 问题单 UI/对话
  → COMPLETE 结果包
  → doom_loop + soft 解耦
  →（之后才）Context / Skills L1 / 授权安装
```

**不**在出口落地前加深 soft 或加厚 ATTRIBUTE_3。

---

## 6. 开放问题（需拍板）

| # | 问题 | 产品倾向 | 技术倾向 |
|---|------|----------|----------|
| Q1 | 验证 FAIL 默认 ASK 还是有限 1 次同 lease in-run repair 后再 ASK？ | 少自动、早问人 | 保留 Runner **内** repair；**出** lease 必 ASK |
| Q2 | soft 生产默认是否改为 `full` 或 `soft` 但不改 exit？ | 诚实展示 | 先不解耦默认值，先禁止假 COMPLETE |
| Q3 | ASK 是否必须 HumanTask 卡，还是对话选项即可？ | 先对话+活动流 | 复用 HumanTask 便于审计 |
| Q4 | COMPLETE 是否自动 ACHIEVE Goal？ | 里程碑 COMPLETE ≠ 总 Goal ACHIEVE | 分离：exit COMPLETE vs Goal 状态机 |

**联合建议默认**：Q1 出 lease 必 ASK；Q2 soft 暂留但禁假 COMPLETE；Q3 HumanTask+对话双写；Q4 分离。

---

## 7. 评审检查句（开工/收工）

1. 失败后是否出现 **问题单**，而不是静默再烧？  
2. 每轮 metadata 是否有且仅有一种 `exit_kind`？  
3. COMPLETE 是否带完整结果包？  
4. STOP 后是否零自动 GenerationRun？  
5. 人未答前 CONTINUE 是否被拒绝或仅提示「请先回答」？  

---

## 8. 下一步

~~1. 产品确认 …~~ **已按默认拍板开工并落地。**

### 落地摘要（2026-08-03）

| 项 | 落点 |
|----|------|
| Schema / 文案 | `application/agent_loop_exit.py`；`live_action` 三态事件 |
| ADR | `decision-note-agent-loop-exit-2026-08-03.md` |
| 禁自动续烧 | `delivery_gap_recovery.recover` → ASK/STOP（`agent_loop_exit_enforced`） |
| 人答后续跑 | `resume_after_human` → 同 Session `SESSION_RESUME` |
| soft 假 COMPLETE | verification 不再洗 PASS；generator soft → DeliveryRejection→ASK |
| COMPLETE 落盘 | orchestrator `_stamp_agent_loop_complete`（≠ ACHIEVE） |
| 开关 | `REGENT_AGENT_LOOP_EXIT_ENFORCED`（默认 true） |

**第一刀代码：砍掉 gap 后的默认自动 `SESSION_RESUME`，改为 `ASK_HUMAN` 落盘。** ✓
