# 联合调研：Claude Code × OpenWork — 相对「出口 + To-do 驱动」还必须参考什么

**日期**：2026-08-03  
**角色**：产品经理 × 技术经理（联合调研，非开工单）  
**范围**：在已落地的两根支柱之上，筛出**必须吸收**的能力；不写实现切片。  
**已落地基线**：
- A0 出口：`COMPLETE / STOP / ASK_HUMAN`（禁 `RETRY_FOREVER`）
- Work Plan：Step 0 清单、`plan_approve`、清单驱动执行、COMPLETE `open_items`

**主参考**：
- [Claude Code Agent Loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)
- [Claude Code Todo / Task tools](https://code.claude.com/docs/en/agent-sdk/todo-tracking)
- [OpenWork PRODUCT / Permission / Abort / Artifacts](https://github.com/different-ai/openwork)（及 Cowork/OpenWork 任务环实践）
- 仓库内既有对账：[`absorption-plan-agent-matrix`](absorption-plan-agent-matrix-2026-08-03.md)、[`agent-core-vs-claudecode-audit`](agent-core-vs-claudecode-audit-2026-08-02.md)

---

## 0. 一句话结论

出口与 To-do 解决的是「**何时停**」和「**按什么顺序干**」。  
两边成熟产品里，与这两根柱**同级、必须补齐**的，是：

1. **中途人机闸门**（权限 / 打断 / 插话），否则出口只在失败后才出现；  
2. **可证明的结果面**（artifacts + ResultMessage），否则 COMPLETE 用户看不见；  
3. **循环内诚实状态**（单条 in_progress、系统提醒、doom/无进展），否则 To-do 变成假清单；  
4. **硬顶与可杀**（turns / budget / abort / 子 Agent 可停），否则再好的出口也会被旁路烧穿。

其余（MCP 市场、移动端手势）**不必抄**。  
Hive / 甘特级时间线 / 流式·事件 / Ask-vs-Act：**长远要**，按混合分期落地——见 [`execution-plan-hybrid-control-experience-ops-2026-08-03.md`](execution-plan-hybrid-control-experience-ops-2026-08-03.md)。

---

## 1. 两根已落地支柱 ↔ 对标源

| Regent 已做 | Claude Code | OpenWork | 还缺什么（本调研焦点） |
|-------------|-------------|----------|------------------------|
| A0 出口三态 | 无 tool → 结束；`ResultMessage`+`stop_reason`；`AskUserQuestion`；`max_turns`/`max_budget_usd`；doom→要人 | plan 批准；permission.reply；`session.abort`；artifacts+summaries | **运行中**打断、结构化 Ask 工具、Result 对外一等事件、子 Agent 可杀 |
| Work Plan / todo | TodoWrite→TaskCreate/Update/List；同时仅 1×`in_progress`；系统提醒催清单 | 右侧 plan UI；todos 时间线；Plan 模式（写前必批） | **系统催办**、单条增量 Task API、清单与活动流解耦、计划可改后重批 |

---

## 2. 产品经理：用户体感上「还必须有」

### 2.1 Must（没有就仍像黑盒 / 仍会空转）

| # | 能力 | 对标 | 用户为什么必须有 | Regent 现状 |
|---|------|------|------------------|-------------|
| P1 | **随时 Stop / Abort** | OW `session.abort`；CC Esc / 主机 shutdown | 出口在「跑完」才有用；跑偏必须立刻停 | 有跨 Run 暂停；**租赁中硬打断弱** |
| P2 | **中途插话（steering）** | CC 循环中注入 user message；OW redirect plan | 改需求不应等失败 ASK | 基本只有失败后 `resume_after_human` |
| P3 | **危险动作 Permission 卡** | OW allow once / always / deny；CC `canUseTool` | 写删发外链不能只靠白名单 | Permit 偏外层；**工具级实时卡不全** |
| P4 | **结构化问人** | CC `AskUserQuestion`（选项+暂停） | 文本提问易被当成「说完了」继续烧 | ASK 信封有了；**模型侧专用工具未成一等** |
| P5 | **COMPLETE 结果面** | OW artifacts+summaries；CC `ResultMessage` | 「完成」必须可点开产物/摘要/未决 | 有元数据；**控制台一等结果卡仍弱** |
| P6 | **计划可改再批** | OW/Cowork：redirect plan 再 approve | 首批错了不能只能拒绝重开 | plan_approve 有；**改计划闭环未产品化** |
| P7 | **清单驱动可见进度** | OW todos 时间线；CC 单条 in_progress | 用户要看见「卡在第 k 步」 | 工作清单已有；**与对话/步骤事件绑定弱** |

### 2.2 Should（明显提升信任与可控，可排第二波）

| # | 能力 | 对标 | 说明 |
|---|------|------|------|
| P8 | Ask vs Act 模式 | Cowork / OW | 「逐步批准」vs「按计划连跑」；删除仍强制问 |
| P9 | Plan-only 模式 | OW Plan agent | 只出计划不写盘，适合大改预览 |
| P10 | 审计可导出 | OW audit log | prompts / plan / tools / permission / outputs |
| P11 | Session 摘要产物 | OW `session.summarize` | 长 Session 可沉淀一页摘要 artifact |
| P12 | 流式叙述 | CC StreamEvent | 长跑不黑盒（观测，非正确性） |

### 2.3 明确不做 / 缓做（产品红线）

- 默认 Hive PM→Dev→QA 当编码大脑  
- 远程任务市场、复杂依赖编辑器  
- 照搬 CC 全套 MCP 生态为第一优先级  
- 「总是允许」类永久旁路（与 A0 / permission 冲突）

### 2.4 产品验收句（调研用，非本期承诺）

1. 跑偏时 3 秒内能 Stop，且 Session 可恢复。  
2. 执行中一句话改方向，不必等验证失败。  
3. 敏感写/删/外发有 once/always/deny，deny 优雅 STOP。  
4. COMPLETE 页能打开产物、摘要、open_items。  
5. 清单上永远最多一条「进行中」，且与真实工具步一致。

---

## 3. 技术经理：内核上「还必须参考」的机制

### 3.1 Must（与出口 / To-do 同级的工程契约）

| # | 机制 | Claude Code | OpenWork | Regent 落点建议 | 优先级依据 |
|---|------|-------------|----------|-----------------|------------|
| T1 | **循环自然结束 = 无 tool call** | 官方 loop：无 tool → `ResultMessage` | abort/结果分离 | Runner 已部分具备；**外层禁止把「无 tool」当成再租信号** | 出口正确性 |
| T2 | **Result / stop_reason 一等** | `ResultMessage.subtype` success/limit | artifacts+summaries | 已有 `agent_loop_exit`；需 **SSE/API 对外稳定契约** | 可观测 COMPLETE |
| T3 | **中断控制通道** | control_request / Esc；`worker_shutting_down` | `session.abort` | lease 可取消 + toolkit 协作取消；写盘可恢复 | 防烧钱 |
| T4 | **Permission 在工具边界阻塞** | hooks / `canUseTool` 等答复 | `permission.reply` Promise | 危险 tool 挂起 → ASK/Permit → 再续同一轨迹 | 人机闸门 |
| T5 | **Ask 工具阻塞到答复** | `AskUserQuestion` | permission 同构 | 专用 tool 或等价；**不得用纯文本提问冒充停** | ASK 可信 |
| T6 | **To-do / Task 状态机诚实** | 仅 1× in_progress；TaskCreate/Update；系统提醒 | plan JSON artifact + 时间线 | 已有 normalize；需 **系统催办**（久无更新提醒）+ 增量 update | 防假清单 |
| T7 | **硬顶** | `max_turns` / `max_budget_usd` | Stop 一等 | 已有 turns/tokens/wall；缺 **美元/成本硬顶**与对用户可见 | STOP 可预期 |
| T8 | **无进展 / doom → ASK** | 实践 + OpenCode 教训 | — | 已部分有；须与 **清单 blocked_item_key** 绑定 | 禁空转 |
| T9 | **子 Agent 有界且可停** | Task/Agent + 深度帽；业界痛点 KillAgent | — | `delegate_plan_item` 已有；缺 **深度上限、孤儿可杀、预算继承** | 防子孙烧穿 |
| T10 | **Stop / Verification hook** | Stop hook 可 block「假完成」 | — | 独立 Verification；soft 不得伪 COMPLETE（已立规） | COMPLETE 诚实 |

### 3.2 Should（工程增强，可跟在 Must 后）

| # | 机制 | 说明 |
|---|------|------|
| T11 | 只读工具并发 | CC 对 read/glob 并发；缩短 wall |
| T12 | 判别联合事件流 | `turn_* / tool_* / plan_updated / result / compact_boundary`；弃中文反解 |
| T13 | Prompt cache 分段 + 动态清单 delta | skills/tools 不进永不变 system 前缀 |
| T14 | Skills 渐进披露 | catalog → 按需正文；用户授权安装 = ASK 一种 |
| T15 | Compact 边界事件 | `compact_boundary` 可观测，避免 silently 丢上下文 |

### 3.3 技术不做

- 第三套 Agent loop / 平行「矩阵 OS」真相源  
- 无 `item_key` 的裸 subagent 风暴  
- soft gates 映射为 `exit_kind=COMPLETE`

---

## 4. 合成优先级（联合拍板建议）

### Wave A — 与「出口 + To-do」闭环（Must，建议下一刀）

```text
A1  运行中 Abort / 取消 lease（P1/T3）
A2  工具级 Permission 阻塞 + once/always/deny（P3/T4）
A3  模型可调用的 AskUserQuestion 等价物（P4/T5）
A4  COMPLETE 结果包控制台一等卡（产物/摘要/open_items）（P5/T2）
A5  To-do 系统催办 + blocked_item 进 ASK（P7/T6/T8）
A6  子 Agent 深度帽 + 可停 + 预算继承（T9）
```

### Wave B — 协作体验（Should）

```text
B1  中途 steering（插话改方向）（P2）
B2  计划 redirect → 重批（P6）
B3  Ask/Act 模式开关（P8）
B4  结构化事件流 + 流式（P12/T12）
B5  成本硬顶 max_budget（T7 增强）
```

### Wave C — 能力面扩展（Later）

```text
C1  Plan-only 模式（P9）
C2  审计导出 / summarize（P10/P11）
C3  Skills 安装授权通道、只读并发、cache 分段（T11–T14）
```

---

## 5. 映射总表（便于评审）

| 能力簇 | CC | OW | Regent 已有 | 联合判定 |
|--------|----|----|-------------|----------|
| 出口三态 | Result / Ask / 硬顶 | abort / ASK 类 | A0 | **已立**；加固对外契约 |
| To-do / Plan | Task/Todo + 1 in_progress | plan UI + 时间线 | Work Plan W0–W4 | **已立**；补催办与改计划 |
| 运行中 Stop | Esc / shutdown | session.abort | 弱 | **Must** |
| Permission | canUseTool | permission.reply | 部分 Permit | **Must** |
| 结构化 Ask | AskUserQuestion | 权限卡同构 | 信封有、工具弱 | **Must** |
| 结果面 | ResultMessage | artifacts | 弱 | **Must** |
| Steering | 中途 user | redirect | 弱 | Should |
| 子 Agent 可杀 | 仍痛点 | — | 有领单 | **Must 有界** |
| 流式 / 事件协议 | Stream + SDKMessage | steps 行 | 过渡态 | Should |
| Skills / MCP | 强 | 工作区能力 | L0 | Later（服从 ASK） |

---

## 6. 风险与依赖

| 风险 | 说明 | 缓解 |
|------|------|------|
| Permission「总是允许」复活死循环 | 与 A0 冲突 | always 限 session + 审计；禁止跨 Goal 永久旁路 |
| Abort 丢草稿 | 用户不敢点停 | STOP 必须带 draft_uri / Session PAUSED |
| 假 To-do 糊弄门禁 | 清单有但步未做 | COMPLETE/验证独立；催办；blocked_item |
| 子 Agent 孤儿 | CC 已知惨案 | 深度=1 默认；注册表可杀；继承父预算 |
| 过度抄 UI | 分心做壳 | 先契约与闸门，UI 只服务闸门 |

---

## 7. 评审检查句

1. 除了「失败后 ASK」，用户能否在**运行中**停、改、批？  
2. COMPLETE 是否有**可点击的结果面**，而不只是 metadata？  
3. To-do 是否**驱动**下一步，还是仅展示？系统是否催诚实更新？  
4. 危险工具是否在**调用点**阻塞，而不是事后审计？  
5. 子 Agent 是否可能无法停止地烧 token？  
6. 是否又引入了平行 loop / 默认 Hive？

---

## 8. 建议下一步（调研收口，非开工）

1. 产品确认 Wave A 六条为下一里程碑范围。  
2. 技术出 Wave A 短 DecisionNote（不变量 + 与 A0/Work Plan 关系）。  
3. 明确 **不做** Wave C 进入编码默认路径。  

**联合立场**：出口与 To-do 是骨架；**Abort + Permission + 结构化 Ask + 结果面 + 子 Agent 有界** 是骨架上必须焊死的关节。缺这些，两边对标产品里「人还能控住 Agent」的部分仍未到位。
