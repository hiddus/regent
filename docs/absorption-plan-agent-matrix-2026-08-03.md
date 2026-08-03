# Absorption Plan: 增强 Primary Agent（拆件使用，非底座）

**日期**：2026-08-03（v3.4 — A0 出口对齐 OpenWork / Claude Code）  
**状态**：ACTIVE（与产品方就「出口优先」达成一致）  
**基线**：[`decision-note-project-agent-session-2026-08-02.md`](decision-note-project-agent-session-2026-08-02.md)  
**产品远期**：智慧景区 / 智慧城市运营 Hive；近中期先做实编码交付 Primary Agent  
**范围来源**：agent-matrix 拆件 + agentskills + 成熟 coding Agent；禁止 Matrix OS 当底座  
**出口设计参考**：[OpenWork](https://github.com/different-ai/openwork)（plan 批准 / permission / abort / artifacts）+ [Claude Code Agent Loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)（无 tool → 结束；AskUserQuestion；Stop hook；预算/turns 硬顶；doom_loop → 要人）  
**落地执行计划**：[`execution-plan-agent-loop-exits-2026-08-03.md`](execution-plan-agent-loop-exits-2026-08-03.md)（产品×技术联合）  
**工作清单落地（草案）**：[`execution-plan-session-work-plan-2026-08-03.md`](execution-plan-session-work-plan-2026-08-03.md)（产品×技术×交互）

**零号前提 + 三大吸收轴**：

| 轴 | 角色 |
|----|------|
| **A0 出口合同** | Loop 必须有完成 / 终止 / 问人；无出口则任何续跑都是死循环 |
| Context | 看见什么 |
| Skills | 按需手艺 + 发现→对比→授权安装 |
| Agent Loop 工程 | 合进 AgentRunner（仍服从 A0） |

---

## 0. 先认清：Agent Loop 本身是循环，死循环是缺出口

Agent Loop **本来就是循环**（Observe→Act→Verify→Continue）。这不是 bug。

**死循环**指的是：没有明确的 **完成、终止、或向用户提问**，于是只能换 lesson / 无出口 Session 续跑 / ATTRIBUTE_3 换标签——**燃料换了，出口没有**。

### A0 — Loop 出口合同（最高优先级；已与产品方对齐）

每个 Agent 回合族（一次 lease / 一轮用户授权的执行）必须落到且只能落到：

| 出口 | 含义 | 对用户 |
|------|------|--------|
| **COMPLETE** | 验收条件满足（或显式本里程碑完成） | **完整结果**：产物、预览、证据、未决项清单（可为空） |
| **STOP** | 明确停止（预算硬顶、用户 abort、策略禁止、不可恢复） | **为何停** + 草稿/工作区指针；**不再自动续烧** |
| **ASK_HUMAN** | 当前信息/权限下无法完成，或需批准 | **可答问题 / 批准请求**；进程退出等人；答完再 lease |

```text
                    ┌─ COMPLETE  → 完整结果报告 → 结束本轮
   Agent Loop  ─────┼─ STOP      → 终止说明 + 草稿 → 结束本轮（不自动再开）
                    └─ ASK_HUMAN → 确认问题 → 暂停，等人（suspend，不占 Worker）
```

**禁止的第四态**：`RETRY_FOREVER`。

### A0.1 设计参考：OpenWork × Claude Code → Regent 映射

不照搬产品壳，只吸收 **出口与人机边界** 的成熟切法。

#### Claude Code（主参考：循环何时停、何时问人）

| Claude Code 做法 | 含义 | Regent 落点 |
|------------------|------|-------------|
| `while (tool_use)`：本轮 **无 tool call** → 结束本轮，等人下一条 | 自然 **COMPLETE 或陈述式 ASK**（文本提问也算停） | Runner：无 tool / 显式 `submit` → 结束 lease；禁止外层自动再租 |
| **`AskUserQuestion` 工具** | 结构化问人；经 `canUseTool` **暂停到有答复** | ASK_HUMAN：专用提问/确认信封（选项、缺什么、建议）；Outbox 停，不占进程 |
| **Permission / `canUseTool`** | 危险工具先批准再继续 | 已有 Permit；缺口时升为 ASK，不静默重试 |
| **`max_turns` / `max_budget_usd`** | 硬顶 → 停 | STOP：预算/轮次硬顶；附草稿与原因 |
| **`Stop` hook** | 声称结束时可 **block** 并要求补完（可验证条件） | COMPLETE 前独立核对（Verification）；未过 → ASK 或带回修一轮 **有上限**，禁止无限 block |
| **Result + `stop_reason`** | 明确为何停（end_turn / max_tokens / refusal…） | 每轮必须持久化 `exit_kind` + `stop_reason` + 结果包 |
| OpenWork/OpenCode 教训：**doom_loop 检测 → 强制要人** | 无进展空转识别 | 同 gap / 无 diff 重复 N 次 → ASK_HUMAN，禁止再自动 SESSION_RESUME |

#### OpenWork（主参考：任务流上的人机闸门与结果面）

| OpenWork 做法 | 含义 | Regent 落点 |
|---------------|------|-------------|
| Goal → **结构化 plan → 用户批准** → 再跑 | 开跑前闸门 | 大改/新里程碑：plan 或方向 **ASK 批准** 后再 lease（小续跑可免） |
| **Permission 事件** → UI allow/deny → `permission.reply` → 继续或优雅失败 | 中途闸门 | ASK_HUMAN / Permit 卡；deny → STOP 或改道，不重开彩票 |
| **Stop / `session.abort`** | 用户强制 STOP | 用户暂停/停止 → STOP；lease 取消；Session PAUSED |
| 跑完展示 **artifacts + summaries** | COMPLETE 有完整结果面 | COMPLETE 结果包：预览 URL、产物、证据、摘要、未决项 |
| pause/resume/cancel 为 **一等操作** | 不是失败后的补丁 | Session pause/resume 只在出口之后或用户显式操作 |

#### 合成后的 Regent 出口状态机（S0 要实现）

```text
[RUNNING]
    │ submit + 独立验证通过 ──────────────► COMPLETE（结果包）
    │ 用户 abort / 预算·轮次硬顶 ─────────► STOP（原因 + 草稿）
    │ AskUserQuestion / Permit 待批 /
    │   信息不足 / doom_loop 检出 ───────► ASK_HUMAN（问题单；Worker 释放）
    │
    └─ 禁止：验证 FAIL 或预算到 → 自动再 GENERATION_RUN / 再 lesson / 无问人续 Session
```

ASK 恢复（对齐 OpenWork permission.reply + Claude 答完再转）：

```text
人答复或批准 → 写入 Session checkpoint → 新 lease（同 workspace）
拒绝 / 超时策略 → STOP 或保持 PAUSED（策略可配，不得自动空转）
```

COMPLETE 结果包（对齐 OpenWork artifacts+summaries）：

- 产物与预览  
- 验证/证据摘要  
- 本轮变更要点  
- 未决项（可空）  
- `exit_kind=COMPLETE` + 时间与 token  

### 连续性机制 vs 出口（勿混）

| 机制 | 真正作用 | 不能代替 |
|------|----------|----------|
| ProjectAgentSession | 同一主体、同工作区、checkpoint | **不能**代替 COMPLETE/STOP/ASK |
| failure lesson / 新 Run | 至多带一点上下文 | **不能**代替出口 |
| ATTRIBUTE_3 换标签 | 应退役 | **不是**出口 |
| soft gates | 战术旁路 | **不是** COMPLETE |

### 当前缺口（诚实）

`AgentRunner` 有 `submit`、预算耗尽、verification，但外层常把 FAIL 转成再租/lesson/soft。  
S0 优先把出口做成一等公民，设计刻意对齐上表，而不是再发明第四套流水线。

---

## 0.1 既有纠正（仍有效，服从 A0）

| 主题 | 正确说法 |
|------|----------|
| ATTRIBUTE_3 | 拆掉当大脑；不是第三种出口 |
| Skills | 发现→对比报告→用户授权→安装（授权 = ASK_HUMAN 的一种） |
| Hive | 编码期不做默认替身；远期运营域要做；**每角色服从 A0** |

> 先有出口，再谈续跑；续跑只服务「人答完 / 条件变了之后的下一轮」。

---

## 1. 目标态

```text
近中期
  Primary Agent 循环执行，直到 COMPLETE | STOP | ASK_HUMAN
  ASK 之后人答 → 同 Session 再开有出口的下一轮

远期（景区/城市）
  多域 Hive；每域 Agent 同样有出口；Hive 编排不能取消出口
```

---

## 2. ATTRIBUTE_3 与「无出口续跑」

两者都不是解药：换标签 = 无出口空转；无出口的 Session 续跑 = **同一条死循环换皮**。

外层只允许在 **显式出口** 后动作——COMPLETE 交结果；STOP 停；ASK 等人；人回来后再 lease。

---

## 3. 已吸收盘点

| 已落地 | 判定 |
|--------|------|
| Skills L0 catalog / 渐进披露 | 不过度；无安装通道、无出口合同 |
| Session 续跑 | 连续性有了；**出口仍缺** → 续跑仍可能空转 |
| soft gates | 战术；易假冒 COMPLETE |

---

## 4. 长远应吸收（均服从 A0）

### A1 Context

Retriever → Composer → AgentRunner；提高 COMPLETE 率与 ASK 质量，不取消出口。

### A1b Skills

- L0 已有；L1 可解释+预算；L2 Manifest lockfile；L3 与 Context 协同  
- **L4 安装**：发现→对比报告→**用户授权**→安装→可回滚  
- 禁止无授权静默装；禁止 Skill 提权  

### A2 Manifest

Skills/Tools/MCP/Runtime/Sub-agent 本地锁定。

### A3 AgentRunner 工程

成熟 coding Agent 细节合进 Runner；**退出路径必须映射 A0 三态**；跑完交付完整结果包。

### A5 Hive 分期

现在：不做编码默认替身。远期：运营多域；每角色 A0。

---

## 5. 永久不吸收（收窄）

Matrix OS/Guardian/Treasury 第二真相；无授权自动装；把 soft 假 PASS 当 COMPLETE；无出口的 RETRY_FOREVER。

---

## 6. soft gates

不得把 FAIL 洗成 COMPLETE；中期与成功语义解耦。

---

## 7. 分阶段路线图

| 阶段 | 做什么 | 完成定义 |
|------|--------|----------|
| **S0** | **落地 A0（对齐 OpenWork/Claude Code）**：每轮 `exit_kind`∈{COMPLETE,STOP,ASK_HUMAN}；结果包/问题单/abort；doom_loop→ASK；禁无出口自动续烧 | 无 RETRY_FOREVER；抽检日志可见三态 |
| **S1** | Context spike + Skills L1 | 更高 COMPLETE 率；更清楚的 ASK |
| **S2** | Manifest + Skills L2 | lockfile |
| **S3** | Runner 加固；出口对齐 A0；完整结果包 | 三态映射清晰 |
| **S4** | 验证说真话；soft ≠ COMPLETE | FAIL 不被洗成成功 |
| **S4b** | Skills 发现→报告→授权安装 | 授权走 ASK |
| **S5** | 运营 Hive；每域 A0 | 协作不取消出口 |

**第一刀：S0 出口合同。** 不先堆 Context/Skills，也不先加 Session 续跑次数。

---

## 8. 评审检查句

1. 这一轮结束落在 COMPLETE、STOP 还是 ASK_HUMAN？都不是 → 仍是死循环。  
2. COMPLETE 是否带完整结果（产物+证据+未决项）？  
3. ASK 是否是人能答的具体问题？  
4. Session/lesson 是否只在「人已答 / 新授权」之后续跑？  
5. Skills 安装是否报告+授权？Hive 是否保留每角色出口？

---

## 9. 变更摘要

| 版本 | 说明 |
|------|------|
| v3.2 | 拆 ATTRIBUTE_3；Skills 授权安装；Hive 远期 |
| **v3.3** | Loop 缺出口不是缺燃料；COMPLETE/STOP/ASK；S0=A0 优先 |
| **v3.4** | A0 对齐 **OpenWork**（plan 批准 / permission / abort / artifacts）与 **Claude Code**（无 tool 结束 / AskUserQuestion / Stop 核对 / 预算硬顶 / doom_loop→要人）；产品方就出口优先达成一致 |
