# 混合落地方案：控制面先行 × 体验面跟上 × 运营面远期

**日期**：2026-08-03  
**状态**：H0–H2 LANDED — 控制面 + 体验面 + 只读时间线已落地；H3 Hive 预埋已接线，**代码默认 `aar1_certified_hive=True`**（可按 Goal/env 退出；自适应拓扑仍 GQ-5 门禁）  
**DecisionNote**：[`decision-note-hybrid-h0-control-plane-2026-08-03.md`](decision-note-hybrid-h0-control-plane-2026-08-03.md)  
**立场修正**：调研文档将 Hive / 甘特 / 流式·事件 / Ask-vs-Act 标为「缓做」；**长远仍要**。本方案把它们纳入**同一产品叙事的分期混合路径**，而不是永久砍掉。  
**依据**：
- [`research-claude-openwork-must-absorb-2026-08-03.md`](research-claude-openwork-must-absorb-2026-08-03.md)
- A0 出口 + Session Work Plan（已落地）
- [`absorption-plan-agent-matrix-2026-08-03.md`](absorption-plan-agent-matrix-2026-08-03.md)（Hive 远期）

---

## 0. 三方共识（一句话）

> **近中期**焊死「人能控住的 Primary Agent」（出口 + 清单 + Abort/Permission/Ask/结果面）；  
> **同期预埋**事件协议与 Ask/Act 开关，让体验面不二次翻车；  
> **远期**在同一不变量上长出时间线（甘特级）与运营 Hive——**编排不能取消出口**。

```text
层 C  运营面   Hive 多 Session / 多角色     ← 景区·城市；每角色仍 A0
层 B  体验面   流式·事件 · Ask/Act · 时间线 ← 看得见、选松紧、看依赖
层 A  控制面   出口·清单·Abort·Permission·Ask·结果·子Agent有界  ← 先焊死
         ↑ 所有上层只能调用，不能旁路
```

**混合**含义：不是「先做完 A 再想 B」，而是 **A 必达 + B 同步预埋契约 + C 明确闸门后启用**。

---

## 1. 产品经理：为什么长远要、为何现在混合

### 1.1 长远仍要的四件事（产品理由）

| 能力 | 长远用户价值 | 若永远不做的代价 |
|------|--------------|------------------|
| **Ask-vs-Act** | 同一产品服务「盯着跑」与「批完去喝咖啡」两种人 | 只有一种松紧 → 要么烦死要么失控 |
| **流式 / 事件协议** | 长跑可跟、可回放、可客服排障 | 控制台永远像黑盒；面板只能轮询猜 |
| **时间线 / 甘特级视图** | 多步·多依赖·多派工时「谁堵谁」一眼清 | 清单变长后用户迷路；运营域无法排程 |
| **Hive** | 景区/城市：多域并行、角色职责、交接 | 单 Primary 撑不住多主体运营 |

### 1.2 混合策略（不是妥协，是顺序）

| 原则 | 内容 |
|------|------|
| **控制先于体验** | 没有 Abort/Permission，Ask-Act 会退化成「总是允许」 |
| **契约先于皮肤** | 事件协议 schema 可先于漂亮流式 UI；甘特 UI 先于「项目管理套件」 |
| **编排晚于单脑** | Hive 只在「单 Session 可控」达标后开；且每角色继承 A0 |
| **甘特 = 视图，不是第二真相** | 时间线只投影 `ExecutionPlanItem`；禁止另建一套任务库 |

### 1.3 产品分期验收（可对外讲）

| 阶段 | 名称 | 用户可感验收 | 含哪些「长远件」 |
|------|------|--------------|------------------|
| **H0** | 可控单脑 | 停得下、批得住、问得清、完成看得见 | 无 Hive；无甘特；事件**契约**起步 |
| **H1** | 可选松紧 + 看得见 | Ask/Act 开关；步骤流式/准实时；计划可改再批 | Ask-Act；事件流；流式（可先 tool 级） |
| **H2** | 依赖可读 | 清单可看前后置；简单泳道（按 owner） | **甘特级时间线 v0**（只读） |
| **H3** | 运营 Hive | 多角色/多 Session 编排；交接可见 | Hive；时间线可编依赖（仍非组合管理） |

### 1.4 产品明确边界（各期）

| 阶段 | 做 | 不做 |
|------|----|------|
| H0–H1 | Primary + 领单子 Agent | 默认 PM→Dev→QA；跨 Goal「永久总是允许」 |
| H2 | 只读依赖图 / 简易甘特投影 | MS Project；多项目资源平衡 |
| H3 | 运营域 Hive，角色有出口 | Hive 取消 A0；无 item 的裸角色空转 |

---

## 2. 技术经理：混合架构与切片

### 2.1 不变量（三层共用）

| ID | 内容 |
|----|------|
| H-A | 任意 Agent lease 结束必须 `exit_kind ∈ {COMPLETE,STOP,ASK_HUMAN}` |
| H-B | 写操作服从 Work Plan（trivial 豁免除外）；Hive 角色也只能领有 `item_key` 的项 |
| H-C | Permission `always` **默认 session 作用域**；禁止静默跨 Goal 永久旁路 |
| H-D | 事件流是观测与 UI 的**唯一推荐真相**（过渡期可双写，H1 末收口） |
| H-E | 时间线 / Hive 编排 **不得**直接改 Verification 结论或伪造 COMPLETE |

### 2.2 预埋点（H0 就做，避免 H1/H2 翻车）

| 预埋 | H0 最小动作 | 解锁 |
|------|-------------|------|
| **RegentEvent 判别联合** | 定义 schema + Runner 已有事件对齐写入；SSE 可先推子集 | H1 流式 / 面板 |
| **execution_mode** | Goal/Session metadata：`ask` \| `act`（默认 `ask` 或按设置） | H1 Ask-Act |
| **plan dependencies** | 已有字段；API/控制台只读展示 | H2 时间线 |
| **owner_agent_id + role** | 已有 owner；预留 `role` 枚举（primary/subagent/hive_*） | H3 Hive |
| **abort_token** | lease 级取消位；toolkit 协作检查 | H0 Abort |

### 2.3 工程切片（混合排期）

```text
H0  控制面焊死（约 1.5–2.5 周）
    H0.1 Abort / 取消 lease + STOP 带草稿
    H0.2 工具级 Permission（once/always/deny）阻塞
    H0.3 AskUserQuestion 等价工具 → ASK_HUMAN
    H0.4 COMPLETE 结果包 API + 控制台结果卡
    H0.5 子 Agent 深度帽=1、可停、预算继承
    H0.6 RegentEvent schema + 关键路径落盘（预埋，UI 可简陋）

H1  体验面（约 2–3 周，可与 H0.6 重叠）
    H1.1 Ask-vs-Act 开关（产品默认：新 Goal=ask；信任用户可 act）
         Act 仍强制：删除 / 外发 / 未批 plan 的大写
    H1.2 SSE 事件流收口（弃中文反解）；tool/plan/exit 实时
    H1.3 流式：先 assistant 文本与 tool 进度；token 级可选
    H1.4 中途 steering + 计划 redirect 再批
    H1.5 To-do 系统催办 + ASK 带 blocked_item_key

H2  时间线（约 1.5–2 周）
    H2.1 只读依赖图（ExecutionPlan dependencies）
    H2.2 「甘特级」简易时间条：按 item 状态 + owner 泳道（无工期估）
    H2.3 可选：用户拖依赖仅改 plan（需 plan_approve 或 act 模式）

H3  运营 Hive（约 3+ 周，产品闸门：H1 验收过）
    H3.1 角色合同已有 MemberContract → 仅运营 Goal 类型启用
    H3.2 每角色独立 lease + A0；编排器只派 item_key
    H3.3 交接事件进 RegentEvent；时间线显示跨角色
    H3.4 编码默认路径仍 Primary；Hive 需显式 goal_kind / org 开关
```

### 2.4 Ask-vs-Act 技术语义（避免「Act = 总是允许」）

| 模式 | 行为 |
|------|------|
| **ask** | plan_approve 默认开；写/删/外发 → Permission 卡；Ask 工具照常 |
| **act** | 同 Session 内对已批 plan 的**清单内写**可连跑；`always` 仅本 session |
| **两模式共性** | Abort 永远可用；删除/密钥/外发默认仍 ask；doom_loop → ASK；预算硬顶 → STOP |

### 2.5 「甘特」技术降维

不要上项目管理引擎。投影即可：

```text
ExecutionPlanItem
  → { item_key, content, status, owner, dependencies[], updated_at }
  → TimelineView（泳道=owner，条=状态色，边=dependencies）
```

工期、关键路径、资源冲突：**H3 以后再说**，且单独 DecisionNote。

### 2.6 Hive 接入条件（技术闸门）

同时满足才开编码外的默认 Hive：

1. H0 Abort/Permission/Ask/结果面 生产可用  
2. H1 事件流稳定；Ask-Act 无「永久旁路」事故  
3. 子 Agent 可停、深度帽有效  
4. `goal_kind` / org 策略显式 opt-in（非全局默认）

---

## 3. 交互专家：一面板上的混合体验

### 3.1 信息架构（控制台，一构图）

```text
┌─ 对话 ──────────────────────────────┬─ 右侧栏 ─────────────┐
│ 流式叙述 / Ask 卡片 / Permission 卡  │ 工作清单（主）        │
│ COMPLETE 结果卡（可点产物）          │  [Ask|Act] 模式切换   │
│                                      │  时间线（H2 折叠）    │
│                                      │  Agent 名册           │
│                                      │  活动流（事件驱动）   │
└──────────────────────────────────────┴──────────────────────┘
 底栏一等：[停止] 始终可见；Act 模式下仍红
```

### 3.2 各阶段交互重点

| 阶段 | 交互必达 | 交互忌讳 |
|------|----------|----------|
| H0 | Stop 一等；Permission/Ask 用同一卡片族；结果卡压过长活动流 | 清单再套大卡片墙；无 Stop 的「漂亮动画」 |
| H1 | 模式切换有确认文案（「Act：清单内连跑，删除仍询问」）；流式不抢滚动 | Act 藏在设置深处；假进度条无事件 |
| H2 | 时间线默认折叠；有依赖阻塞时自动展开并高亮堵点 | 一上来甘特占满首屏 |
| H3 | Hive 角色用名册区分；交接用事件条而非新导航页 | 运营 IA 污染编码默认首屏 |

### 3.3 Ask / Permission / Plan 卡片统一语言

同一视觉组件，三种 `ask_type`：

| ask_type | 主按钮 | 次按钮 |
|----------|--------|--------|
| `plan_approve` | 批准计划 | 修改方向 / 停止 |
| `permission` | 允许一次 / 本会话允许 | 拒绝 |
| `ask_user` / 缺口 | 选项 A/B… | 补充说明后继续 |

拒绝 → STOP 或保持 PAUSED（文案写清），**不**自动换租空转。

### 3.4 流式体验原则

- 用户贴底才跟随；上滚锁定（已有审计结论）  
- 心跳/重复 progress **原地替换**；状态类 append  
- token 级流式可关；**tool 边界事件不可关**（否则 Ask-Act 无感）

### 3.5 甘特级时间线（H2）交互降维

- 只读条 + 依赖线足够；编辑走「改清单 / 对话 redirect」  
- 泳道：Primary / 子 Agent /（H3）Hive 角色  
- 空态：无依赖时不展示时间线入口，避免空壳

---

## 4. 联合状态机（跨层）

```text
[Goal 创建]
  → execution_mode = ask|act（默认 ask）
  → Step0 清单 → (ask 或大改) plan_approve
  → [RUNNING]
        ├─ Permission / AskUser ──► 阻塞至答复
        ├─ Abort ───────────────► STOP（草稿）
        ├─ doom / 预算 ─────────► ASK 或 STOP
        ├─ 清单完成+验证过 ─────► COMPLETE（结果卡）
        └─（H3）Hive 派单 ──────► 子 lease（仍上列出口）
  → 事件流全程 RegentEvent（H0 起写，H1 起 UI 主读）
```

---

## 5. 开放问题（需拍板）

| # | 问题 | 联合建议默认 |
|---|------|----------------|
| Q1 | 新 Goal 默认 ask 还是 act？ | **ask**；power 用户可 act |
| Q2 | H0 是否强制上 RegentEvent 落盘？ | **是**（哪怕 UI 仍简陋） |
| Q3 | H2 甘特是否允许拖依赖？ | v0 **只读**；编辑走对话 |
| Q4 | H3 Hive 默认开给谁？ | 仅 `goal_kind∈{ops,scenic,city}` 或 org flag |
| Q5 | Act 下 plan_approve？ | 同 Session 续跑免；**重规划 / 新里程碑仍要** |

---

## 6. 评审检查句

1. 长远四件（Ask-Act / 事件流 / 时间线 / Hive）是否都有**阶段与闸门**，而不是口头「以后再说」？  
2. H0 预埋是否足够让 H1 不翻 schema？  
3. Act 是否仍保留 Abort + 删除询问 + A0？  
4. 甘特是否只是 Plan 投影？  
5. Hive 是否可能静默成为「无出口的自由拓扑」？（答案必须为否；固定 Hive 可默认，自适应拓扑须 GQ-5 / DecisionRecord）

---

## 7. 下一步

1. 确认 Q1–Q5。  
2. 出 H0 DecisionNote（Abort / Permission / Ask / Result / Event 预埋）。  
3. 开工 **H0.1 Abort + H0.6 Event schema**（可并行），再 Permission / Ask / 结果卡。

**三方立场**：长远件全部要；混合落地 = **控制面必达、体验面预埋并紧随、运营面带闸门启用**——用一层不变量串起来，而不是三套产品。
