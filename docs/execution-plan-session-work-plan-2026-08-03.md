# 落地方案：Session 工作清单 / 任务图（Work Plan）

**日期**：2026-08-03  
**状态**：IMPLEMENTED — W0–W4 已落地；见 [`decision-note-session-work-plan-2026-08-03.md`](decision-note-session-work-plan-2026-08-03.md)  
**依据**：A0 出口合同；OpenWork（Plan 预览/批准）；Claude Code（TodoWrite → TaskCreate/Update/List）  
**基线资产（勿重复造轮）**：
- Agent 工具 [`todo_write`](../core/src/regent/agent/tools.py)
- 持久化 [`ExecutionPlanService`](../core/src/regent/application/execution_plan.py) + `ExecutionPlanItemModel`
- 控制台 [`ArtifactPanel`](../apps/regent-console/src/components/ArtifactPanel.tsx)「执行计划」折叠区
- [`SubagentRunner`](../core/src/regent/agent/subagent.py)（默认 batch 关）
- A0 [`agent_loop_exit`](../core/src/regent/application/agent_loop_exit.py)

---

## 0. 三方共识（一句话）

> Primary Agent **必须先写出可勾选的工作清单，再逐步执行**；需要时把清单项派给子 Agent。  
> 清单挂在 **ProjectAgentSession**，服从 **A0 出口**；不是 Hive 多角色流水线。

```text
L1 开跑计划（OpenWork）     生成清单 →（可选）人批准 → 写入 Session/Goal Plan
L2 执行任务图（Claude Code）  pending → in_progress → completed；可依赖、可派工
L3 子 Agent 领单              SubagentRunner 绑定 plan item_key；回写状态
```

---

## 1. 产品经理：要解决什么、验收什么

### 1.1 用户问题

| 今天体感 | 目标体感 |
|----------|----------|
| Agent 直接改文件，不知在干什么 | 先看到「本轮要做的 N 步」 |
| 漏步、跳步、空转 | 逐步勾选；未完成不能假装 COMPLETE |
| 没有「派给谁」 | 复杂项可显示「子 Agent 执行中」 |
| 右侧计划常空 | 有清单时默认展开；无清单时明确「尚未规划」 |

### 1.2 产品规则

1. **多步目标强制 Step 0**：进入工具改文件前，须已有 ≥1 条 pending/in_progress 计划项（小修补可豁免，见技术节）。  
2. **大改 / 新里程碑**：清单生成后走 A0 **ASK_HUMAN（plan_approve）**，人批准再跑（OpenWork）。小续跑可跳过批准。  
3. **逐步完成**：同时最多 1 条 `in_progress`（Primary）；完成后才开下一项。  
4. **COMPLETE 门禁**：A0 COMPLETE 时，非 cancelled 项须全部 `completed`，或结果包 `open_items` 显式列出未完成并经人确认。  
5. **子 Agent**：只领清单项，不另起无清单的平行大脑。  
6. **不做**：远程任务市场；默认 PM→Dev→QA Hive；为清单新建第三套 Agent loop。

### 1.3 产品验收（非工程师可验）

| # | 场景 | 通过标准 |
|---|------|----------|
| P1 | 新目标开跑 | 对话/右侧出现工作清单（≥3 步常见）后再见大量写文件 |
| P2 | 执行中 | 当前项高亮 in_progress；完成项打勾 |
| P3 | 大改 | 先出现「请确认计划」；批准后才续跑 |
| P4 | 失败 ASK | 问题单可引用「卡在第 k 步」 |
| P5 | 派工（二期） | 某步显示子 Agent；完成后回写勾选 |

### 1.4 产品明确不做（本方案范围）

- 重做整站 IA  
- 运营域 Hive 任务编排（S5）  
- 完美甘特图 / 多项目组合管理  

---

## 2. 技术专家：怎么落、挂哪里

### 2.1 现状诊断

| 组件 | 能力 | 缺口 |
|------|------|------|
| `todo_write` | 本 lease 内存清单 | **不强制**；模型常跳过 |
| `ExecutionPlanService` | DB 持久、status/依赖/owner 字段已有 | 与 Session 弱绑定；工具面仍是整表覆盖式 todo |
| ArtifactPanel | 有「执行计划」UI | 常为空；无批准流；无派工态 |
| SubagentRunner | 按 milestone 跑 | 未接 plan `item_key`；batch 默认关 |

### 2.2 目标模型（升格，少造表）

**权威**：`ExecutionPlanItem`（已有表）+ Session checkpoint 摘要。

对齐 Claude Code Task 语义（映射到现有字段）：

| CC / 概念 | Regent 字段 |
|-----------|-------------|
| TaskCreate | `upsert` 新 `item_key` |
| status pending/in_progress/completed | 已有 `status` |
| dependencies | 已有 `dependencies` |
| owner / 子 Agent | `owner_agent_id` |
| TaskList | `list_by_goal` + API |
| Session 持久 | `metadata.session_id` + `project_agent_session_id` |

新增工具面（或扩展 `todo_write`）：

```text
plan_upsert   — 创建/更新多项（鼓励开跑时一次写出）
plan_update   — 单条改状态（in_progress/completed）
plan_list     — 读回当前清单（防模型失忆）
```

保留 `todo_write` 为兼容别名 → 内部转 `plan_upsert`。

### 2.3 强制 Step 0（Runner 门禁）

```text
IF lease 目标非 trivial
  AND 尚无 active plan items
  AND 工具 ∈ {write_file, edit_file, run_command, …写操作}
→ 拒绝工具结果，提示：先 plan_upsert 工作清单
```

`trivial` 启发式（可配）：单文件修复、用户消息含「只改某某」、或 goal metadata `plan_required=false`。

### 2.4 与 A0 / Session

| 事件 | 行为 |
|------|------|
| 生成首版计划 | 可写 `ASK_HUMAN` ask_type=`plan_approve` |
| 人批准 | `mark_ask_answered` → 同 Session lease |
| 执行中更新 | upsert plan；live_action `plan_updated` |
| doom_loop / 验证缺口 ASK | ask_envelope 带 `blocked_item_key` |
| COMPLETE | 校验清单完成度 → 写入 result_bundle |
| STOP | 清单冻结；状态保留 |

Session checkpoint：

```json
{ "work_plan": { "item_keys": ["..."], "updated_at": "..." } }
```

### 2.5 子 Agent 派工（二期）

```text
Primary: plan_update(item, owner=subagent-X, status=in_progress)
  → SubagentRunner.run_milestone(brief 含 item_key + acceptance)
  → 回写 completed/failed + evidence_refs
```

禁止：无 item_key 的裸 subagent 风暴；禁止默认 Hive 三角色。

### 2.6 工程切片

| 阶段 | 内容 | 人天（估） |
|------|------|------------|
| **W0** | ADR：Work Plan 不变量 + 与 A0/Session 关系 | 0.5 |
| **W1** | `plan_*` 工具 + Step 0 门禁 + 持久化绑 Session | 2–3 |
| **W2** | plan_approve ASK；COMPLETE 清单门禁 | 1–2 |
| **W3** | 控制台计划面板强化（见交互）+ API 列表 | 2–3 |
| **W4** | 子 Agent 领单回写（可选） | 2–3 |

**第一刀：W1 Step 0 + 持久清单可见。**

### 2.7 风险

| 风险 | 缓解 |
|------|------|
| 强制计划拖慢小修 | trivial 豁免 |
| 模型写假清单糊弄门禁 | COMPLETE/验证仍独立；空完成可 doom |
| 与旧 todo_write 双轨 | 别名收敛到 ExecutionPlan |
| 计划批准打扰 | 仅大改/首跑；续跑默认免 |

---

## 3. 交互工程师：怎么呈现、怎么点

### 3.1 信息架构（控制台）

**右侧「工作清单」升为一等折叠（默认：有未完成项时展开）**，不再埋在深层「执行计划」偶发空列表。

```text
┌─ 工作清单 ─────────────────────────┐
│ ○ 1. 搭路由与首页          pending   │
│ ● 2. 接入持久化        in_progress │  ← 当前
│ ✓ 3. 补测试                 done     │
│ ◇ 4. UI 打磨     → 子Agent          │  ← 二期
│ [批准计划] [调整后继续]              │  ← plan_approve 时
└────────────────────────────────────┘
```

对话区：计划批准用 A0 问题单卡片（选项：批准 / 修改方向 / 停止）。  
活动流：保留 `计划已更新（N 项）`；当前步变化可短摘要。

### 3.2 状态视觉

| status | 视觉 |
|--------|------|
| pending | 空心圆 + 次要色 |
| in_progress | 实心/脉冲 + 强调色（非紫光污染；跟现有 console token） |
| completed | 勾选 |
| failed / cancelled | 警告/划线 |

禁止：清单区再套一层大卡片墙；禁止和活动流抢第一屏（清单优先于长活动流）。

### 3.3 空态 / 加载

- 无计划：`尚未生成工作清单 — Agent 规划中…`  
- Step 0 拦截后对话提示：`请先列出本轮步骤`（用户可见助手说明）  
- COMPLETE 有 open_items：清单底部「未决」分组  

### 3.4 交互不做

- 用户手拖复杂依赖编辑器（一期只读+批准；调整用对话）  
- 独立「项目管理」页  

---

## 4. 联合状态机（执行视角）

```text
[RUNNING]
  ├─ 无计划 + 写操作 ──────────────► 拒工具 / 要求 plan_upsert
  ├─ 首版计划 + 需批准 ────────────► ASK_HUMAN (plan_approve)
  ├─ 人批准 / 免批准 ──────────────► 按项执行（todo/plan_update）
  ├─ 项完成 → 下一项
  ├─ 验证缺口 / doom ─────────────► ASK_HUMAN（可带 blocked_item）
  ├─ 清单完成 + 验证通过 ──────────► COMPLETE (+ result_bundle)
  └─ 预算/用户停 ──────────────────► STOP（清单冻结）
```

---

## 5. 开放问题（需拍板）

| # | 问题 | 联合建议默认 |
|---|------|----------------|
| Q1 | 何时强制 plan_approve？ | 首跑 + 用户显式「重新规划」；同 Session 小续跑免 |
| Q2 | trivial 豁免标准？ | 单文件/单函数级；或用户说「只改 X」 |
| Q3 | COMPLETE 是否允许带 open_items？ | 允许，但须在结果包列出；默认不 ACHIEVE |
| Q4 | W4 子 Agent 是否进第一期？ | **否**；W1–W3 先做实 Primary 清单 |
| Q5 | 工具名用 plan_* 还是保留 todo_write？ | 对外 `todo_write` 兼容；对内 ExecutionPlan；文档推 Task 语义 |

---

## 6. 评审检查句

1. 多步任务是否先见清单再大量改文件？  
2. 清单是否落在 DB/Session，刷新还在？  
3. 失败 ASK 是否指到具体步骤？  
4. COMPLETE 是否还对未完成项撒谎？  
5. 是否引入了平行 loop 或默认 Hive？  

---

## 7. 下一步

1. 确认 Q1–Q5 默认建议  
2. 写短 ADR `decision-note-session-work-plan`  
3. 开工 **W1：Step 0 门禁 + plan 持久 + 控制台默认展开**  

**第一刀代码：写操作前强制工作清单，并保证右侧能看见。**
