# oh-my-cli 吸收分析与长远规划

**日期**：2026-08-03  
**状态**：LANDED（O0–O4 纵深轴已接线生产路径；见 §13 修订记录）  
**对标源**：[qwen-code-dev-bot/oh-my-cli](https://github.com/qwen-code-dev-bot/oh-my-cli)（Apache-2.0；Qwen Code 系本地 code-agent CLI）  
**角色**：产品经理 × 技术经理 × 交互设计 × Agent 内核  
**立场**：吸收**行为契约与用户能力**，不吸收仓库形态、不引入第三套 Agent loop、不把本机 CLI 运行时当 Regent 底座。

**既有底座（必须服从）**：

| 文档 | 关系 |
|------|------|
| [`absorption-plan-agent-matrix-2026-08-03.md`](absorption-plan-agent-matrix-2026-08-03.md) | A0 出口三态；禁 `RETRY_FOREVER` |
| [`research-claude-openwork-must-absorb-2026-08-03.md`](research-claude-openwork-must-absorb-2026-08-03.md) | CC × OpenWork Must 清单 |
| [`execution-plan-hybrid-control-experience-ops-2026-08-03.md`](execution-plan-hybrid-control-experience-ops-2026-08-03.md) | H0–H3 混合层（控制→体验→时间线→Hive） |
| [`AUTONOMY.md`](https://github.com/qwen-code-dev-bot/oh-my-cli/blob/main/AUTONOMY.md)（上游） | 对标源的自治治理合同（参考边界，非照搬 bot） |

**易混澄清**：本文件**不是** [Yeachan-Heo/oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode)（OMC）。OMC 是 Claude Code 上的多 Agent 工作流插件层；`qwen-code-dev-bot/oh-my-cli` 是**自包含**的本地 Agent CLI（会话、审批、沙箱、证据、工作流契约）。后文「oh-my-cli / OMC-Q」均指后者。

---

## 0. 四方共识（一句话）

> oh-my-cli 真正值得吸收的，不是 160+ 个 `src/*.ts` 模块清单，而是一套**长期产品语法**：  
> **信任姿态可解释、突变可归因可回退、主任务与旁路隔离、完成不可伪造、坏状态可隔离、契约版本化可探测。**  
> Regent 应把这套语法焊进「控制面 → 体验面 → 交付证据 → 运营域」的同一叙事，并按十年运营（景区/城市）的可恢复性来排期——而不是为了「对标一个 CLI 功能表」开一堆平行实现。

```text
oh-my-cli 教的是「本地工程师如何敢把机器交给 Agent」
Regent 要学的是「用户如何敢把 Goal 交给产品，并在失败后仍能续航」

本地 CLI                         Regent 产品
─────────────────                ────────────────────────────
folder-trust × sandbox    →      workspace + Permit + 租户边界
approval + impact preview →      工具 Permission + 控制台可解释闸门
turn pre-image undo       →      Generation/工具回合可逆 + 草稿指针
side-question 结构隔离    →      旁路澄清不污染 Work Plan / lease
auto-achieve guard        →      A0 COMPLETE 守卫（已立，需显式化）
progress-loop detector    →      doom / 无进展 → ASK（已部分有）
corrupt quarantine        →      会话/artifact/Outbox 坏态隔离
compaction sidecar        →      transcript 原文 + 可校验摘要边界
evidence / delivery-brief →      COMPLETE 结果包 + 交付审查证据
workflow / extension 合同 →      能力包 / MCP / 命名工作流（远期）
AUTONOMY 治理面           →      DecisionRecord + 人维护闸门（不抄自改 bot）
```

---

## 1. 对标源：实现解剖（专家共用事实）

### 1.1 它是什么 / 不是什么

| 是 | 不是 |
|----|------|
| Node 22 + TypeScript ESM 的**单进程** code-agent CLI | 多租户 Goal/Outbox 产品后端 |
| OpenAI 兼容 provider + 文件/Shell 工具 + 硬顶（轮次/预算） | Hive / 组织运营编排器 |
| 用户家目录会话（`~/.oh-my-cli/sessions` JSONL） | 服务端 Session 真相源 |
| 强安全默认：folder-trust、命令策略、审批模式、脱敏导出 | 「yolo 默认」或无限自愈 |
| 大量**只读探测面**（doctor / trust-posture / contracts） | 必须全部产品化的 UI 皮肤 |
| AUTONOMY：Bot 可执行 Issue，**不能**改治理文件 | 应照搬的仓库自演进机器人 |

### 1.2 实现上真正成体系的五条脊梁

读 `README` Architecture + 关键模块后，可抽出五条**跨功能脊梁**（比单点 flag 更重要）：

| 脊梁 | 代表实现 | 设计意图 |
|------|----------|----------|
| **R1 边界合成** | `folder-trust` × `sandbox` × `approval` × `command-policy` → `trust-posture` | 「这次 run 到底允许什么」可审计；`yolo` **不能**压过 trust |
| **R2 回合归因** | `turn-checkpoint`：工具突变前抓 pre-image；undo/redo fail-closed | 回退单元是**回合**，不是 git hard reset |
| **R3 结构隔离旁路** | `side-question`：runner 无 session/goal/workspace 句柄；无 tool schema | 隔离靠类型边界，不靠「请模型别动」 |
| **R4 诚实生命周期** | `auto-achieve-guard`、`progress-loop-detector`、task 八态 + receipt；坏 checkpoint quarantine | 完成/卡住/损坏都不能靠 UI 臆造 |
| **R5 契约与证据** | provider/MCP/tool/workflow **versioned contract**；export/evidence 脱敏+digest；delivery-brief | 扩展与交付用合同与证据，不用口头保证 |

### 1.3 模块密度的正确读法

`src/` 约 160 文件，命名面很广（mission、desktop、delivery-web、review-studio…）。**长远吸收时必须分层**：

1. **脊梁层**（上表 R1–R5）——应进入 Regent 长期能力地图。  
2. **CLI 宿主层**（REPL、TUI、Electron desktop、loopback delivery-web demo）——学交互原则，不移植宿主。  
3. **扩展探测层**（`--*-contract` / `--discover-extensions`）——学「就绪态与版本协商」模式；排期靠后。  
4. **自治运营层**（`.autonomy`、Bot lease、community intake）——学「治理面与执行面分离」；**禁止**把自改仓库 bot 当产品能力。

若只盯着「有没有 `--undo-turn`」而对齐 flag，会做成一堆与 Outbox/Goal 脱节的 CLI 仿品。

---

## 2. 产品经理：长远要什么用户能力

### 2.1 十年叙事（不是本季功能）

Regent 的远期是**可运营的交付与运营域**（编码 Primary → 景区/城市 Hive）。oh-my-cli 贡献的是中间层缺失的**信任语法**：

| 用户长期心智 | 若永远缺 | 对标源给的产品句 |
|--------------|----------|------------------|
| 「我知道它现在被允许干什么」 | Permission 像黑盒；不敢开 Act | Trust posture / impact preview |
| 「做错了能退一步，不必重开世界」 | 只能 Abort 整 Goal 或手搓 git | Turn-level undo |
| 「问一句澄清不会打乱正在跑的计划」 | 插话=steering=改计划，或只能干等 | Side question 旁路 |
| 「说完成就真完成」 | soft PASS / 假 COMPLETE | Auto-achieve guard |
| 「卡住了会停下来问我，而不是换皮重试」 | 死循环 / 阶梯空转 | Progress loop → ASK |
| 「坏了的状态不会污染别的」 | 一个坏文件拖垮会话族 | Quarantine |
| 「我能把这次跑的证据交给别人」 | 只有聊天记录 | Export / evidence / delivery-brief |
| 「扩展能力进产品有合同」 | 随意 MCP/插件炸权限面 | Versioned contracts |

这些能力在 **H0–H3 之后仍然成立**，且在 Hive 开启后**更刚需**（多角色更需要姿态、归因、旁路、假完成守卫）。

### 2.2 与已拍板混合层的关系

| 混合层 | oh-my-cli 主要补强什么 |
|--------|------------------------|
| **H0 控制面**（已 LANDED） | 假完成守卫显式化；Permission **impact 可解释**；无进展 → ASK 与 `blocked_item_key` 焊死 |
| **H1 体验面** | Side question；compaction **可观测边界**；session/审计导出 |
| **H2 时间线** | 少直接对标；间接：步骤/任务 receipt 让时间线「有证据节点」 |
| **H3 Hive** | Trust posture 按角色/Session；旁路问询不串扰其它角色 lease；回合归因跨 worktree |
| **H3 之后（运营十年）** | 证据包成为组织交接物；契约化能力池；坏态隔离成为多租户运维常态 |

### 2.3 产品分期（吸收轴 **O**，叠在 H 之上，不另起产品线）

| 阶段 | 名称 | 用户可感验收 | 主要吸收脊梁 |
|------|------|--------------|--------------|
| **O0** | 守卫显式化 | 失败/打断/预算耗尽/修订过期 → **不可能**自动 COMPLETE；同一步无进展达阈 → ASK/STOP | R4 |
| **O1** | 可解释闸门 | 批准前看得到将触碰的路径/命令；一页「本 Run 姿态」 | R1 |
| **O2** | 旁路与压缩诚实 | 可侧问且主清单不变；压缩有事件/sidecar 语义，原文可追溯 | R3 + 压缩边界 |
| **O3** | 回合可逆 | 最近一次突变回合可 dry-run / undo（工作区+对话指针），发散则 fail-closed | R2 |
| **O4** | 证据与合同 | COMPLETE/STOP 可导出脱敏证据包；扩展/工作流有版本就绪态 | R5 |

**O 不替代 H**：没有 Abort/Permission（H0）就谈 undo/side-question 会变成危险玩具；没有事件契约（H1）就谈证据包会变成静态 PDF 幻想。

### 2.4 产品明确不做（长期红线）

- 默认 `yolo` / 「总是允许」跨 Goal。  
- 把 oh-my-cli Desktop / delivery-web demo 当成 Regent Console 替代品。  
- 把 AUTONOMY Bot 自演进复制进产品仓。  
- 为对标而默认 Ultrawork/多 CLI pane（那是 OMC 族问题，且与单 Primary 红线冲突）。  
- 用「功能数量对等」当 Graduation 指标。

---

## 3. 技术经理：长远架构落点

### 3.1 吸收原则（工程）

| ID | 原则 |
|----|------|
| O-T1 | **语义进内核，宿主留 CLI**：Regent 落点是 Application/Agent/API/Console，不是再做一个 `oh-my-regent` npm。 |
| O-T2 | **不新增 Agent loop**：全部挂在 `AgentRunner` + `ProjectAgentSession` + Outbox；出口仍 ∈ {COMPLETE, STOP, ASK_HUMAN}。 |
| O-T3 | **隔离靠缺能力，不靠提示词**：side-question 类路径不得持有 workspace writer / Permit mint / Goal mutator。 |
| O-T4 | **回退单元 = 可归因突变集**：优先「工具回合 / GenerationRun 片段」的 content pre-image；禁止把 `git reset --hard` 当产品 undo。 |
| O-T5 | **坏态隔离优于静默修复**：corrupt transcript/checkpoint → quarantine + 用户可见；不覆盖、不假装健康。 |
| O-T6 | **契约版本化**：未来 MCP/能力扩展用 `declared/ready/isolated` 就绪态；与现有 capability certification 对齐，不另起市场。 |
| O-T7 | **证据可验证**：导出带 digest；COMPLETE 守卫读取同一套 outcome，不读 UI 状态。 |

### 3.2 Regent 落点地图（概念 → 模块）

| 脊梁 | Regent 落点（建议） | 现状粗判（2026-08-03） |
|------|---------------------|------------------------|
| R1 Trust posture | `agent_control` / Permit + workspace sandbox 元数据；Console「姿态」面板；API `GET .../trust-posture` | Permit/沙箱有；**合成视图弱** |
| R1 Impact preview | Permission 卡 payload：`paths[]` / `command_class` / `network` | 卡有；**预览结构化不足** |
| R2 Turn undo | Generation/工具层 `TurnImageCollector`；artifact 存 pre-image；API undo dry-run | **基本缺**（STOP 带草稿 ≠ undo） |
| R3 Side question | 只读 ContextAssembler 快照 + 无工具 provider 调用；不写 conversation 主链（或写 `SIDE_NOTE` 旁路类型） | steering 有；**结构隔离旁路缺** |
| R4 Auto-achieve | Verification + `agent_loop_exit` + delivery soft 规则；显式 `evaluate_complete_allowed(outcome)` | 规则散落；**缺统一守卫函数/测试名** |
| R4 Progress loop | watchdog + `blocked_item_key` + ProgressTracker 语义 | 部分有；**阈与同一步定义需产品化** |
| R4 Quarantine | transcript/session/outbox payload 损坏路径 | sidecar 有实践；**统一 quarantine 协议缺** |
| Compaction boundary | 已有 `ContextCompactor` + transcript artifact | **事件对外契约/用户可见边界可加强** |
| R5 Evidence export | COMPLETE 结果包 + delivery-review checks；签名/digest 可选增强 | 结果卡起步；**便携证据包弱** |
| R5 Contracts | capability bootstrap + member_contract；MCP 若引入走同一认证 | 能力池有；**通用 extension 合同层远期** |

### 3.3 与不变量对齐

| 既有不变量 | oh-my-cli 吸收时的约束 |
|------------|------------------------|
| H-A / A0 出口 | undo / side-question / export **不得**发明第四出口；失败只能 STOP/ASK |
| H-B Work Plan | side-question 不得推进 `item_key`；undo 不得偷偷标 item done |
| H-C always 作用域 | trust posture 必须显示 always 的作用域；yolo 类模式若存在须显式且从属 trust |
| H-D 事件真相 | compaction、undo、quarantine、side-question 必须发 `RegentEvent` |
| H-E 时间线/Hive 不伪 COMPLETE | auto-achieve guard 在 Hive 角色上同样强制 |

### 3.4 技术债式「假吸收」清单（禁止）

- 只加 CLI flag 文档，不进生产路径。  
- 用 prompt「请不要修改文件」冒充 side-question。  
- undo = `git checkout .`。  
- quarantine = `except: pass`。  
- 把 oh-my-cli 源文件 vendoring 进 monorepo「先跑起来」。

---

## 4. 交互设计：长远体验语法

### 4.1 交互原则（从对标源提炼）

| 原则 | 含义 | Regent Console / 对话落点 |
|------|------|---------------------------|
| **先看见边界，再授权** | 批准 UI 展示 impact，而不是只显示工具名 | Permission 卡：路径树/命令类别/网络域 |
| **主舞台 vs 侧台** | 主任务时间线不被侧问污染 | 侧栏或浮层「快问」；答案默认不进主 transcript |
| **回退可预览** | undo 先 dry-run 列表 | 「将恢复 N 个文件 / 回滚 M 条消息」确认 |
| **损坏可命名** | quarantine 有可见徽章与恢复指引 | Session/Goal 顶栏：`CORRUPT_QUARANTINED` |
| **姿态一页纸** | 高级用户/支持可复制红acted JSON | 「安全姿态」只读页；与 Ask/Act 状态并列 |
| **完成是仪式** | COMPLETE 打开结果+证据，不是一句「好了」 | 已有结果卡方向；补证据入口与 open_items |

### 4.2 信息架构（长期）

```text
控制台 Session
├── 主列：对话 + 活动流（RegentEvent）
├── 右栏：Work Plan / 时间线投影
├── 顶栏：Ask|Act · Abort · 姿态指示（trust 摘要）
├── 模态：Permission（impact）· AskUserQuestion · Undo 预览
└── 侧台：Side question（可选挂起，不抢主列焦点）
```

Hive 远期：每个角色 Session **各自**姿态与侧台；交叉提问走显式交接事件，禁止静默写对方 lease。

### 4.3 交互不做

- 用炫酷 delivery-web 平行图替代 Work Plan 真相。  
- 把 doctor 结果弹成每次启动的模态墙（应按需 / 设置页）。  
- 侧问默认写入用户可见主对话导致「计划被聊天带跑」。

---

## 5. Agent 专家：循环、记忆与子代理

### 5.1 对 Agent 内核的长期含义

| 主题 | oh-my-cli 启示 | Regent Agent 层要求 |
|------|----------------|---------------------|
| **完成判定在循环外** | `evaluateAutoAchieve(outcome)` 纯函数 | Runner 在宣称 COMPLETE 前必须过守卫；Verification soft ≠ COMPLETE |
| **无进展是一等状态** | 同 step 连续 attempt 计数 | 与 Work Plan `item_key` 绑定；达阈 → ASK（带 blocked_item）或 STOP |
| **压缩不删证据** | transcript 原文 + sidecar；收据告诉模型「勿重复突变」 | 已有 compact + transcript artifact；须保证 **突变收据** 在压缩后仍在上下文策略中 |
| **子代理继承边界** | trust 继承；worktree lease | 深度帽=1；预算/abort/trust 继承；undo 范围默认不跨子代理除非显式 |
| **旁路调用无工具面** | side-question 忽略 tool_call | 旁路路由不得注册 tools；provider 层丢弃 tool_calls |
| **成本硬顶** | `--budget` / 轮次帽 | 已有 turns/tokens；美元硬顶仍属 Should（CC 调研 T7） |

### 5.2 与「边跑边想」方向的关系

[`direction-note-run-think-learn-2026-08-02.md`](direction-note-run-think-learn-2026-08-02.md) 强调经验吸收与少退出门恐惧。oh-my-cli 的贡献是：**敢跑的前提是可退、可问、可停、不可假完成**。  
吸收顺序应是「先可逆与守卫，再提高自主度」——否则「少退出门」会退化成空转。

### 5.3 Agent 明确不做

- 子代理自动开 yolo。  
- 用更多角色 prompt 目录代替 R1–R5。  
- 把 progress-loop 检测做成「自动换策略无限试」（第三次相同失败应隔离——对齐 AUTONOMY §8 与 A0）。

---

## 6. 长远能力地图（超越眼前实现）

下面按**能力域**规划，刻意不按「上游有没有同名文件」排列。

```text
                    ┌─────────────────────────────────────┐
                    │  Domain Ops (Hive / 多 Session)     │  ← H3+
                    │  姿态/undo/旁路/证据 按角色继承      │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │  Evidence & Contracts               │  ← O4
                    │  导出 · digest · 能力就绪态 · 交接  │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │  Reversibility & Continuity         │  ← O2–O3
                    │  压缩边界 · 侧问 · 回合 undo        │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │  Explainable Authority              │  ← O1
                    │  trust posture · permission impact  │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │  Honest Completion                  │  ← O0（可立即显式化）
                    │  auto-achieve · progress-loop · A0  │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │  H0–H2 Control / Experience / Timeline│  ← 已规划/已落地
                    └─────────────────────────────────────┘
```

**读图要点**：O 轴是**纵深加固**，不是横盘新开一条「oh-my-cli 产品线」。十年后用户仍应感觉是同一个 Regent，只是「敢交给它的事」变多了。

---

## 7. 吸收计划（可执行分期）

### 7.1 O0 — 诚实完成（优先，成本低，契约纯）

**目标**：把对标源 R4 的「完成守卫 / 无进展」变成 Regent 可测不变量。

| 工作项 | 说明 | 验收 |
|--------|------|------|
| O0.1 `evaluate_complete_allowed` | 统一函数：provider_fail / interrupt / cancel / budget / stale_revision / soft_verify → 禁 COMPLETE | 单测表驱动 |
| O0.2 接线 Runner / orchestrator | COMPLETE 出口前强制调用 | 集成：模拟打断不得 COMPLETE |
| O0.3 Progress loop → ASK | 同 `item_key` 连续无进展 ≥N → ASK + `blocked_item_key` | 与现有 watchdog 合并，不双轨 |
| O0.4 文档 | TS/PRD 各一句：假完成守卫；对齐 A0 | 权威文档登记 |

**不做**：新 UI 大改；undo；side-question。

### 7.2 O1 — 可解释权威

| 工作项 | 说明 | 验收 |
|--------|------|------|
| O1.1 Permission impact schema | 卡上结构化 `paths` / `command_class` / `effect_class` | Console 渲染；API 稳定 |
| O1.2 Trust posture API | 合成 workspace trust、sandbox、approval/Ask-Act、capability 就绪摘要 | 只读；脱敏；红acted JSON |
| O1.3 顶栏姿态指示 | 三态灯或短标签：受限 / 标准 / 高信任 | 与 Ask\|Act 并列 |

**依赖**：H0 Permission 已存在。  
**不做**：复制 folder-trust 文件格式；Electron。

### 7.3 O2 — 旁路与连续

| 工作项 | 说明 | 验收 |
|--------|------|------|
| O2.1 Side question 服务 | 无工具、无 Permit、不写主链（或 `SIDE_NOTE`）；边界 note | 结构上无法拿到 writer |
| O2.2 Console 侧台 | 快问入口；展示只读上下文摘要 | 主 Work Plan 不变 |
| O2.3 Compaction 对外事件 | `compact_boundary` 进 RegentEvent；用户可理解「摘要已替换细节」 | 与现有 compact 对齐，不重写算法 |
| O2.4 Session 导出 v0 | 脱敏 Markdown + manifest digest | 对齐调研 P10/P11 |

### 7.4 O3 — 回合可逆（重工程，放中后）

| 工作项 | 说明 | 验收 |
|--------|------|------|
| O3.1 TurnImageCollector | 突变工具执行前记录 pre-image（workspace 相对路径） | 与沙箱路径规范一致 |
| O3.2 Checkpoint 持久化 | 按 GenerationRun / tool-turn 存 artifact；digest | 原子写 |
| O3.3 undo/redo API | dry-run + apply；发散/冲突 fail-closed | 不碰用户手改文件 |
| O3.4 Console Undo | 预览列表 + 确认 | 与 Abort 文案区分：「停」vs「退一步」 |

**风险**：与并行子代理、Hive worktree 交互复杂 → **默认仅 Primary、深度 0 回合**；Hive 延期到 O3.b。

### 7.5 O4 — 证据与合同（远期）

| 工作项 | 说明 | 验收 |
|--------|------|------|
| O4.1 Evidence bundle | COMPLETE/STOP 导出可验证包（review checks、open_items、关键 artifact hash） | verify 命令/API |
| O4.2 命名工作流预设 | 用户/组织级 `ralplan→exec→qa` 类阶段序列（产品预设，非自由拓扑） | 挂 Work Plan，不新 loop |
| O4.3 Extension 就绪态 | MCP/外部工具 `declared/ready/isolated` 与 capability 认证合流 | 未 ready 不可被 Runner 调用 |
| O4.4 doctor | 部署/租户/worker/capability/canary 自检 | 运维入口；非每次打扰用户 |

### 7.6 与混合计划的排期咬合（建议）

```text
现在（H0–H2 已落地）
    └─ O0  立刻可做（守卫显式化）——建议下一刀文档+小切片
H1 深化期
    └─ O1  与 Permission/Ask-Act 体验同船
H1 末～H2
    └─ O2  侧问 + compact 事件 + 导出
H3 闸门前
    └─ O3  Primary 回合 undo 稳定
H3 启用后
    └─ O3.b 角色/worktree 级归因
运营域扩大
    └─ O4  证据包与合同成为组织交接默认
```

---

## 8. 优先级总表（联合拍板用）

| ID | 能力 | 产品 | 技术 | 交互 | Agent | 建议波次 |
|----|------|------|------|------|-------|----------|
| G1 | 假完成守卫统一 | Must | Must | — | Must | **O0** |
| G2 | 无进展→ASK 焊死 | Must | Must | Should | Must | **O0** |
| G3 | Permission impact | Should | Should | **Must** | Should | **O1** |
| G4 | Trust posture 页 | Should | Should | **Must** | Should | **O1** |
| G5 | Side question | Should | Should | **Must** | Must（隔离） | **O2** |
| G6 | Compact 可观测 | Should | Should | Should | Should | **O2** |
| G7 | Session/证据导出 | Should | Should | Should | — | **O2/O4** |
| G8 | Turn undo | Should（强） | 重 | Should | Should | **O3** |
| G9 | Quarantine 协议 | Should | Must（运维） | Should | — | **O0 末～O2** |
| G10 | 工作流预设/扩展合同 | Later | Later | — | Later | **O4** |
| — | Desktop / delivery-web / autonomy bot | 不做 | 不做 | 不做 | 不做 | **Out** |

---

## 9. 风险

| 风险 | 说明 | 缓解 |
|------|------|------|
| 功能表驱动 | 160 文件逐一对齐导致架构膨胀 | 只认 R1–R5；其余降级为「灵感」 |
| Undo 与交付冲突 | 撤回已审查产物 | undo 仅限未发布 Generation；发布后改走新 revision |
| Side question 漏写主链 | 污染计划 | 类型隔离 + 审计测试：断言无 Goal/Plan mutation |
| 姿态页泄露路径 | 安全/隐私 | 全面 redaction；对齐 PRD §7 |
| 过早 Hive undo | 跨 worktree 归因错误 | O3 仅 Primary；O3.b 单独 DecisionNote |
| 与 CC/OW 吸收计划打架 | 多源重复切片 | 本文件 O 轴挂靠 H 轴；G1/G2 并入 A0 加固而非新项目 |

---

## 10. 开放决策（需产品拍板）

1. **Side question 答案是否写入可见旁路消息类型**，还是纯 ephemeral？  
2. **Undo 的默认粒度**：单工具调用 vs 整代 GenerationRun vs 用户可见「一步」？  
3. **Trust posture 是否对终端用户默认展示**，还是仅「高级/支持」？  
4. **O0 是否并入下一刀工程**（建议：是，作为 A0 加固），还是单开 DecisionNote？  
5. **证据包是否需要签名**（密钥托管成本）还是先 digest-only？

---

## 11. 建议的下一步

1. 产品确认 §10 开放题（至少 1、2、4）。  
2. 出短 **DecisionNote**：采纳 O 轴与 R1–R5，明确 Out 列表。  
3. 开工 **O0** 小切片（守卫函数 + 测试 + 接线），与现有 delivery soft / agent_loop_exit 收口。  
4. O1 与 Console Permission 体验迭代排进 H1 深化。  
5. O3 单独立项做设计评审（工作区 pre-image 存储与租户配额）。

---

## 12. 附录：对标源关键入口

| 主题 | 上游路径 |
|------|----------|
| 自治合同 | `AUTONOMY.md` |
| 回合 undo | `src/turn-checkpoint.ts` |
| 侧问隔离 | `src/side-question.ts` |
| 假完成守卫 | `src/auto-achieve-guard.ts` |
| 无进展检测 | `src/progress-loop-detector.ts` |
| 信任姿态 | `src/trust-posture.ts` / folder-trust / approval |
| 架构索引 | `README.md` § Architecture |

---

## 13. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-08-03 | 初稿：四专家联合；确立 R1–R5 脊梁与 O0–O4 纵深轴；明确非 OMC、非第三 loop |
| 2026-08-03 | **LANDED O0–O4**：`evaluate_complete_allowed` + progress-loop；trust posture / permission impact；side-question + session-export + `compact_boundary`；turn checkpoint undo/redo；evidence bundle + workflow presets + extension readiness + `/v1/doctor`；Console 姿态/侧问/撤回/证据入口；单测 `test_oh_my_cli_absorption.py` |

### 落地索引（代码）

| 波次 | 主要落点 |
|------|----------|
| O0 | `application/agent_loop_exit.py`（守卫/进度/隔离）；`execution_orchestrator._stamp_agent_loop_complete`；`agent_runner` progress emit |
| O1 | `application/trust_posture.py`；`agent_control.permission_ask_envelope` impact；`GET .../trust-posture`；TaskCard 影响面；ArtifactPanel 姿态 |
| O2 | `application/side_question.py` / `session_export.py`；`POST .../side-question`；`GET .../session-export`；`RegentEventType.compact_boundary` |
| O3 | `application/turn_checkpoint.py`；`WorkspaceToolkit.bind_turn_collector`；`POST .../undo-turn` `/redo-turn` |
| O4 | `evidence_bundle.py` / `workflow_presets.py` / `extension_readiness.py` / `doctor.py`；`GET .../evidence-bundle`；`/v1/doctor`；`/v1/workflow-presets` |
