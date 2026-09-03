# 逻辑执行方案：边跑边干边想（产品 × 技术）

**日期**：2026-08-02  
**状态**：ACTIVE（执行清单；按人步切片，禁止跳步宣称完成）  
**依据**：[`direction-note-run-think-learn-2026-08-02.md`](direction-note-run-think-learn-2026-08-02.md)  
**兼容**：C1 快照启动（[`decision-note-auto-start-journey-2026-07-31.md`](decision-note-auto-start-journey-2026-07-31.md)）  
**不做**：Goal 前外挂工作室、澄清毕业门、CC 整包吸收、未爬通人步的捷径

---

## 0. 产品 × 技术共识（本方案前提）

| 维度 | 结论 |
|---|---|
| 产品 | 模型主理拆解与方案；人只在推演不清时对有限选项辅助决断。边跑边想，无「退出条件」。 |
| 技术 | 同一 Goal + 现有 Outbox 主链；用 Spec 版本 / guidance / HumanTask（真分叉）表达，不新造第二事实源。 |
| 共同红线 | 怕的不是花预算，是花了不沉淀经验；任何切片验收必须含「下次是否更聪明」。 |

---

## 1. 端到端逻辑（运行时）

```text
用户一句话（可模糊）
        │
        ▼
┌───────────────────┐
│ 模型拆解（主理）   │  ProductUnderstanding / GoalSpec
│ 假设·步骤·未知项   │  显式写入 Spec + 对话可见「方案卡」
└─────────┬─────────┘
          │
     能否自洽推演？
      /            \
    能                不能
     │                 │
     ▼                 ▼
 给出方案            有限选项卡（2–4 个分叉）
 按人步推进          人辅助决断 → 写回 Spec → 再推演
     │                 │
     └────────┬────────┘
              ▼
     C1 快照可早开（不等人填 PRD）
              │
              ▼
   人步交付链（已有，须跑顺）
   Discovery → Req → Cap → Generate
   → Verify → Preview → Observe → Gate
              │
         失败 / 偏差
              ▼
   报错可见 + failure_lessons 落盘
   再争预算 / 资源 / 重开或续跑
   （下次强制注入 lessons）
```

**产品要点**：用户始终看到「模型认为要怎么做」或「需要你拍板的分叉」，而不是黑盒空转。  
**技术要点**：方案与选项是 GoalSpec / conversation EVENT 的一等投影；执行仍走 Orchestrator，不走会话私刑。

---

## 2. 人步切片（严格顺序）

> 每步：**产品验收** + **技术验收** + **学习验收（若适用）**。上一步未绿，禁止开下一步「捷径」。

### 步 L0 — 叙事与门禁对齐（文档 / 评审）

| | 内容 |
|---|---|
| 产品 | 对外/对内统一：模型主理、人辅助、边跑边学；废除「先谈清再开工」话术。 |
| 技术 | PR 模板增加：是否跳过人步、经验如何吸收；拒绝无 lessons 的空转重试扩面。 |
| 现状 | 方向注记已写；本执行方案 ACTIVE。 |
| 验收 | 评审能按本文件挡「外挂访谈 / 毕业门 / CC 插队」。 |

### 步 L1 — 方案可见（模型拆解露出来）

| | 内容 |
|---|---|
| 产品 | draft / 理解完成后，控制台展示**方案卡**：目标理解、关键假设、拟议步骤、未知项（非 JSON 裸 dump）。用户可随时纠偏（已有 CORRECT/MODIFY）。 |
| 技术 | 复用 `ProductUnderstanding` + GoalSpec（explicit / inferences / unknowns）；新增对话消息类型或结构化 metadata（如 `GOAL_PLAN_PROPOSED`），Console 渲染方案卡。不改 C1 早开。 |
| 钩子 | `app_project_service.create_draft`、`GOAL_UNDERSTANDING_READY`、ConfirmationCard 文案改造。 |
| 验收 | 模糊一句开工后，用户不点开 metadata 也能看懂「系统打算怎么做」。 |

### 步 L2 — 推演不清 → 有限选项（人辅助决断）

| | 内容 |
|---|---|
| 产品 | 模型判定「无法自洽」（关键未知阻塞路径 / 互斥假设）时，弹出 **2–4 个选项**，选一个继续；不倾倒长问卷。 |
| 技术 | 在理解或 guidance 路径增加 `needs_user_fork` 信号；选项写入 Spec inferences；用户选择走 guidance（新 command 或复用 APPROVE+option_id）。**真分叉**可用轻量 HumanTask；禁止做成「总是允许」交付缺口卡。 |
| 与 C1 | 允许已 Start；分叉确认后 progressive-snapshot 写回再续跑（已有 progressive 路径）。 |
| 验收 | 人为构造「互斥产品方向」用例：系统停在选项，而不是瞎生成一整站。 |

### 步 L3 — 错误即经验（强制吸收）

| | 内容 |
|---|---|
| 产品 | 失败时用户看见「错在哪 + 下次会避开什么」；重开/续跑不是同一傻瓜重来。 |
| 技术 | 规范 `failure_lessons[]` 写入点（gen fail / verify fail / deploy gap / soft-pause）；结构：`{code, summary, avoid, at}`。Orchestrator 注入已有——补齐**稳定生产写入**与去重。重开/争取预算时 lessons 为空则打点告警。 |
| 钩子 | `execution_orchestrator` acceptance 注入、`context_assembler` Prior failure lessons、delivery_gap_recovery。 |
| 验收 | 同 gap 连续两次：第二次 prompt/acceptance 中必含第一次 lesson；有单测。 |

### 步 L4 — 学习闭环样例：cache（证明「能学会」）

| | 内容 |
|---|---|
| 产品 | 成本问题必须闭环：差 → 动作或明确实验结论，而不是只晒数字。 |
| 技术 | `probe_cache_hit` + ledger `cached_tokens` 已有采集 → 增加：阈值触发「诊断原因」（前缀不稳 / 清单进 system 等）→ **一条自动或半自动修复**（先做最稳的一条，如断言 volatile 仍在 conversation 后）或写入 `ops` 可执行建议并回归验证命中率变化。 |
| 验收 | 构造前缀污染用例：探针报差 → 修复后同剧本命中率上升或给出「不可修+原因」记录；禁止只报警。 |

### 步 L5 — 人步交付链稳态（已有能力爬顺）

| | 内容 |
|---|---|
| 产品 | Preview → 观察 → 决策仍是成功故事；软暂停与真审批分叉不变。 |
| 技术 | 不新开大特性；针对现网卡点（INVALID_STATE、gap 空转、软暂停体验）做加固，并确保 L3 lessons 挂得上。 |
| 验收 | 约定 3–5 个中文模糊目标：能出方案卡；该分叉时分叉；失败有 lessons；至少 1 条跑到 Preview 或可解释的软暂停。 |

### 步 L6 — 才允许讨论的「新路径」（当前冻结）

仅当 L1–L5 有证据后，单独立项（各需对照门禁）：

- 持久事件流（替换 TRANSITIONAL activity）  
- Skills 硬匹配 / 渐进披露  
- 只读工具并发  
- 流式投影  
- 计划驱动子代理  

**仍禁止**：会话作真相、自由 AgentTool fork、未证 cache 炫技插队。

---

## 3. 与现网的映射（少造轮子）

| 能力 | 现网 | 本方案动作 |
|---|---|---|
| 早开执行 | C1 auto-snapshot | **保留** |
| 理解草案 | ProductUnderstanding + unknowns | L1 做成可见方案卡 |
| 纠偏 | CORRECT / MODIFY / progressive-snapshot | L2 选项决断后写回 |
| 真审批 | HumanTask / RELEASE 等 | 分叉可用；缺口仍禁止「总是允许」 |
| 经验 | failure_lessons 注入（写不全） | L3 补全写入与门禁 |
| 观测 | ProgressEvent / activity TRANSITIONAL | 继续当脚手架；L6 再谈终态事件 |
| cache | ledger + probe | L4 做成学习闭环样例 |

---

## 4. 建议排期（逻辑周，非日历承诺）

| 顺序 | 切片 | 依赖 |
|---|---|---|
| 1 | L0 评审话术 | — |
| 2 | L1 方案卡 | L0 |
| 3 | L2 选项分叉 | L1（先有方案形态） |
| 4 | L3 lessons 强制吸收 | 可与 L2 部分并行，但验收不晚于 L5 |
| 5 | L4 cache 闭环 | L3 的「经验结构」可复用 |
| 6 | L5 稳态狗粮 | L1–L4 至少各有最小绿 |
| 7 | L6 提案窗口 | L5 证据包 |

---

## 5. 产品 / 技术分工

| 角色 | 负责 |
|---|---|
| 产品 | 方案卡 / 选项卡文案与交互；「人辅助决断」边界；狗粮目标清单与主观可信验收 |
| 技术 | Spec/消息契约；guidance 分叉；lessons 写入点；cache 闭环；单测与 S0 探针 |
| 共同 | 每步定义「下次是否更聪明」的可观察信号；拒绝跳步 |

---

## 6. 明确不做（本方案周期内）

1. Goal 前独立访谈产品  
2. unknowns=0 才允许 Generate 的硬毕业门  
3. 回退「人类确认才能 Start」  
4. 以 Claude Code 交互面替换交付主链  
5. 一次性重做事件协议 + Skills + 子代理大爆炸  

---

## 7. 一页验收（方案层）

- [x] 模糊目标开工后可见**模型方案**（L1）— `GOAL_PLAN_PROPOSED` + Console 方案卡  
- [x] 互斥方向会停在**选项**而非瞎生成（L2）— `needs_user_fork` / `SELECT_OPTION` / start 门禁  
- [x] 失败重来时 lessons **必现**（L3）— gen 失败 `append_failure_lesson` + acceptance 注入  
- [ ] cache 差能驱动**修复或结论**（L4）  
- [ ] 人步链在狗粮上可解释地跑（L5）  
- [ ] 无跳步合并进 main 的「捷径」PR  

**一句话**：先让用户看见模型怎么想、该拍板时能拍板、错了下次更聪明；交付机器骨架不动，经验回路补齐。
