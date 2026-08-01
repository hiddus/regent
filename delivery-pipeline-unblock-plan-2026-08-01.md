# 交付管道解卡修复计划

> 产品运营 / 技术专家 / AI 科学家联席方案
> 数据截止：SO outbox，2026-08-01

---

## 一、共识结论

当前系统要求 **GQ-3** 报告，但指标实际上无法达标。主矛盾不是「等 GQ-4 / 凑 agentic 样本」，而是 **Goal/Run 创建后管道像卡死**，导致：

- Agentic canary 只在 `GenerationPlan` 冻结时分流；
- 管道一旦停住，策略函数根本不会被调用；
- GQ-4 晋级门槛在管道复活前只是**旁路指标**，不应继续当作主目标。

**本周目标：先让管道「活着动」，再谈质量晋级。**

---

## 二、当前状态快照

| 指标 | 数值 | 含义 |
|------|------|------|
| 无更新 | **32 天** | 用户侧无有效进展反馈 |
| `GenerationRun` PENDING | **21** | 任务队列积压 |
| FAILED 且 `cannot mark FAILED_TERMINAL` | **8** | 状态机悬停，无法进入终态 |
| `path outside frozen plan` | **≥ 8** | 模型输出与冻结计划路径不一致 |

---

## 三、三角色根因诊断

### 3.1 产品经理（PM）

| 维度 | 问题 |
|------|------|
| 用户感知 | 创建后几小时没动静 / 反馈无效 / 补充方向无后续；产品「看起来完整」，但实验进度条是假的。 |
| 错误优先级 | 把 GQ-4（每周 930）当北极星，但漏斗顶端堵塞时，只能测出无效流量与并行「僵尸 Goal」。 |
| 产品原则 | 必须先保证「创建后 5 分钟内有可感知进展」；失败必须有出路；然后再谈策略实验。 |

### 3.2 技术专家（Tech）

**梗阻链（按发生顺序）**

1. LLM 产出路径不在冻结 plan 里 → `ValueError`/强制刹车；
2. `GO` 状态机 `INVALID_STATE cannot mark FAILED_TERMINAL` → 生成侧像卡死前无法落地；
3. `DeliveryStateChanged` 发出但 handler 缺失 / outbox 幂等化抱团 → 事件无人消费；
4. 单 worker 串行长 LLM 调用 → `PENDING` 积压 → 全体系卡死。

**Agentic 真相**

- 决策点在 `CapabilitySatisfied` / `GenerationRunRequested` 处忽略 plan；
- handler 从未真正调用 LLM；
- 反例率高：「是」（创建）但「没机会进」（执行）。

### 3.3 AI 科学家（AI Sci）

| 维度 | 问题 |
|------|------|
| 实验科学性 | ITT 算 plan 返回，20% canary 在健康漏斗下才成立。当前两筛 `pass_rate ≈ 0`，继续采样无法区分「策略差异」与「管道故障」。 |
| 生成合同失配 | frozen plan 路径集合过窄 / 模型缩写 `rec_templates`、`tenpl_init_err`、`fail-closed` 正确但不让通过；「扩 plan 或约束 prompt」的学习闭环只在管道恢复后才有意义。 |
| 建议 | 雪崩 SLO 达标前：GQ-3 要标记为 **degraded**，暂停晋级评判；恢复后才重开样本。 |

---

## 四、失败证据（`GenerationRunRequested`）

Source：SO outbox，2026-08-01 诊断

| 错误模式 | 次数/样本 | 含义 | 处置 |
|----------|-----------|------|------|
| `cannot mark FAILED_TERMINAL` | 8 | 外部操作 / 状态机非法，生成事件非重试死信 | P0 修状态转移；回收后重投 |
| `path outside frozen plan` | ≥ 8 | 模型写了 plan 外路径（templates/static/tests...） | P0 扩允许集或生成前 reconcile plan |
| `PENDING` 积压 | 21 / 均龄 ~31m | worker 吞吐不足或被毒事件拖死 | P0 清毒事件 + 增流并行 Goal |
| `DeliveryStateChanged` 无 handler | 大量 FAILED | 发了观测事件无人消费 | P0 注册 no-op/ack handler 或绑回原发 |

---

## 五、分阶段修复方案

### P0 — 48h：让 App「活着动」

目标：消除卡死，让 `GenerationRun` 能进能出，用户侧出现可感知进展。

- [ ] **事件消费修复**：注册 `DeliveryStateChanged` 的 ack/no-op handler（或停止 emit）；清空毒 outbox。
- [ ] **状态机修复**：修复 `cannot mark FAILED_TERMINAL` —— 生成失败路径必须能进入终态，不得悬停。
- [ ] **Plan 对齐**：`path outside plan` 允许常见脚手架路径进入 plan，或生成后自动扩 plan（显式白名单约束）。
- [ ] **运维回填**：回找 `DISPATCHING` / `PENDING` 的 `GenerationRun`；对 idle `ACTIVE` Goal 批量 requeue。
- [ ] **并发限流**：产品侧加 Goal 限流（例如同时 `GENERATING ≤ N`），避免全体饿死。

**P0 验收标准**：
- 新创建 Goal 在 5 分钟内有 `live/active` 心跳；
- `PENDING` 队列 24h 内下降 50% 以上；
- 无新增 `cannot mark FAILED_TERMINAL` 悬停。

---

### P1 — 一周内：可感知进展 + 失败有出路

目标：建立稳定的 SLO，失败路径可被识别、交人、重试。

- [ ] **SLO**：创建后 5min 必须出现 `live/active` 心跳；15min 无进展 → 自动交人 / 可重试卡。
- [ ] **控制面**：超时后 `reject` 的卡不可再见（允许态）；支持补充方向文本 + 批准 resume。
- [ ] **Canary 纪律**：策略采样只在 `live-active`（`goal_id`, `bucket`, `strategy`）上运行，便于眩流与归因。
- [ ] **生成合同**：plan 与 prompt 对齐（最小可行 HTML 集），减少冻结后的恢复成本。

**P1 验收标准**：
- 日均「到达 plan」Goal 数 > X，且 `state=ACTIVE` 占比 ≥ tt%；
- 失败路径 100% 有明确终态或人工交接入口；
- 不再用僵尸 Goal 充数。

---

### P2 — 管道健康后：再谈 GQ-3/4

目标：在健康漏斗上恢复科学的质量晋级评判。

- [ ] **Measurement**：GQ-3 标记为 `degraded`；晋级评判暂停至漏斗健康。
- [ ] **健康门槛**：日均「到达 plan」Goal 数 X 且 `state=ACTIVE` 比率 ≥ tt% 后，恢复采样统计。
- [ ] **重开门**：以真实成功样本重开 GQ-3/4 门槛；禁止用僵尸 Goal 充 n。

---

## 六、职责分工

| 角色 | 拥有（Owner） | 本周交付物 |
|------|---------------|------------|
| 产品经理 | 优先级 / SLO / 并发策略 | 「创建后可感知进展」验收标准；暂停 GQ-4 主叙事的决策记录 |
| 技术专家 | outbox / 状态机 / plan 合同 | P0 四码代码修复 + S0 解卡脚本；毒事件清单 |
| AI 科学家 | 实验有效性 / 生成合同 | `degraded` 判定法；plan + 模型输出对齐策略；恢复采样的统计门槛 |

---

## 七、立即不做

为避免继续消耗团队带宽，以下事项本周明确暂停：

1. **不手工给单个 Goal 贴 agentic。**
2. **不硬抬 canary「假装」样本。**
3. **不在 `pass_rate = 0` 时讨论默认 agentic。**

---

## 八、下一步跟踪

- **周会 Owner 观察**：重摆开工 PO（`handler` + `FAILED_TERMINAL` + `path-outside-plan` + 调试队）。
- **每日站会三问**：
  1. 昨夜 `PENDING` 队列变化？
  2. 新增悬停事件类型？
  3. 新创建 Goal 5min 内是否有心跳？

---

*计划制定：2026-08-01*
