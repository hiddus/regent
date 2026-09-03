# Agentic Qualification 可执行计划（2026-08-01）

> 状态：ACTIVE  
> 裁决：[`decision-note-agentic-qualification-ladder-2026-08-01.md`](./decision-note-agentic-qualification-ladder-2026-08-01.md)  
> 目标：取消失败 control 对候选验证机会的否决权，同时保留更严格的绝对质量 / 安全 / 成本门禁  
> 明确不做（本计划第一批）：Skills 扩充、跨 Goal prompt cache 优化、多 Agent / Hive 扩投资、GQ-4 默认翻转

## 0. 立即止损（Day 0）

| # | 动作 | 验收 |
|---|---|---|
| S0 | 生产 **停止扩流**；执行 `python -B ops/clamp_generation_strategy_freeze.py`（建议），将 canary 置 0 / gate false | **DONE 2026-08-01**：Settings env `percent=0` `gate=False` `QUAL=DISABLED`；见 `m6-canary-window-2026-08-01.json` `CLAMPED_PENDING_QUALIFICATION` |
| S1 | 报告层：GQ-3 / 生产对照结论改判 `INVALID_BASELINE` + 四条 reason | **DONE**：`gq3_production_report.classify_invalid_baseline` / enrich |
| S2 | DecisionRecord 落盘（本目录 note）；M6 watch plan 标 **HALTED_PENDING_QUALIFICATION** | **DONE** |
| S3 | `apply_gq4_promotion` 路径显式拒绝 `INVALID_BASELINE` / 旧窗 | **DONE**：`gq4_default_switch_gate` + 单测 |

## 1. 批次 Q0 — 模型与晋级语义（与止损并行）

### Q0-1 INVALID_BASELINE + FALLBACK_ONLY 状态模型

- `generation_strategy_experiment.py` / `gq3_production_report.py`：control 验证成功率为 0 且候选严重 starved → `INVALID_BASELINE`（非 `INSUFFICIENT_EVIDENCE`）。  
- Settings / 策略注释：`artifact-backed` = `FALLBACK_ONLY`，`eligible_as_champion=false`。  
- 资格枚举（配置或 DB）：`DISABLED | OFFLINE_QUALIFICATION | INTERNAL_DOGFOOD | CANARY_5 | CANARY_25 | CANARY_50 | DEFAULT`。

### Q0-2 分流与 funnel_degraded

```text
kill_switch → artifact-backed
qualification 未达 DOGFOOD/CANARY_*/DEFAULT → artifact-backed
else → stable bucket by qualified percent
```

- `funnel_degraded`：只阻断 **扩流** 与触发回滚评估；**不**阻断 Offline Qual / 进入 Dogfood / 资格达标后的首档 5%。  
- 删除「artifact-backed 漏斗恢复」作为 canary_gate 的产品前置叙述（代码门禁改为 qualification_state）。

### Q0-3 Provider 完整性（若尚未全绿则补测锁住）

已有：`finish_reason=length` → 失败；畸形 tool args → 失败；429/5xx 重试；401/403 不重试。  
**DONE 2026-08-02**：补齐验收测 — `parse 失败 → Runner 拒绝完成`；503 重试；401/403 不重试（`test_agent_core_m0_m1_contracts`）。

## 2. 批次 Q1 — Agentic 主链五阻断（Offline Qual 前置）

| ID | 主题 | 要点 | 状态 |
|---|---|---|---|
| P0-1 | 模型响应完整性 | 见 Q0-3；输出 token 上限；deadline / Retry-After | **DONE**（锁测） |
| P0-2 | Artifact manifest | 版本化包含/排除/超限；ts/tsx/…；截断 → `ARTIFACT_INCOMPLETE` | **既有** |
| P0-3 | Runtime Profile v1 | 生成/验证/Preview 共用；删无条件 `/health` | **DONE**（真启后端见 P0-5 / R1） |
| P0-4 | Runner 去递归 | 同 RunState gap turn；耗尽诊断 + 禁 RC | **DONE 2026-08-02** |
| P0-5 | 真实 Preview + accepted snapshot | Preview 按 Profile 启后端；accepted；REVISE | **R1 DONE**：进程+readiness；失败 recoverable |

## 3. 批次 Q2 — Offline Qualification Lane

### 3.1 产品冻结（第一批唯一类型）

> 带持久化 CRUD、一个外部证据输入、一个机器可执行 Journey 的轻后端 Web App。  
> 单一 Runtime Profile；暂不并行 React/Vue/静态多框架。

### 3.2 固定执行链

```text
冻结 GoalSpec
→ Agentic Runner
→ 隔离 Sandbox
→ 项目测试
→ 启动与 Smoke
→ 动态 Preview
→ 验收 Journey
→ accepted_workspace_snapshot
→ REVISE
→ V2 增量验证
```

### 3.3 入口

- `ops/run_agentic_offline_qualification.py`（新）：跑冻结 golden + 写出 `docs/agentic-offline-qual-report-YYYY-MM-DD.json`。  
- 出口：契约测试全绿、录制回放无静默成功、golden 完成 Preview+V2、无安全事件 → 允许 `INTERNAL_DOGFOOD`。

### 3.4 绝对门槛（清单）

- 截断 / 畸形 tool call ≠ 完成  
- Artifact manifest 完整率 100%  
- 预算、token、成本、transcript 对账 100%  
- 预算耗尽产物不得晋级  
- Sandbox 无越权 / 泄露 / 非授权网络  
- Preview 与 verification 同 workspace + Profile hash  
- V2 基于 V1 accepted snapshot  
- 基础设施诱发误失败 = 0  
- 无假 `ACHIEVED`

## 4. 批次 Q3 — Dogfood → Canary 阶梯

| 转移 | 最低条件 |
|---|---|
| Qual → Dogfood | Q2 出口 |
| Dogfood → CANARY_5 | ≥20 内部独立任务；基建误失败 0；无假 ACHIEVED；成本/时延 ≤ 预注册上限 |
| CANARY_5 → 25 | ≥40 独立 Goal；preview/first_runnable CI 达门槛；非计划救援率在护栏内 |
| 25 → 50 → DEFAULT | 同构扩样；**禁止 5% 直接全量** |

相对比较（阶段 B）仅当 **两臂皆有非零可比较 verified 结果** 时进行；artifact-backed 持续为 0 时只保留历史对照，**不要求** agentic 显著优于 0。

## 5. 回滚

**立即 0% agentic：** 泄露/越权、假 ACHIEVED、未验证发布、Artifact/预算/transcript 无法对账。  

**滚动 20 Goal：** Preview 或 first-runnable 低于合格基线 >10pp；`unplanned_rescue_rate > 0.40`；P95 或单位验证成本 > 1.5× 基线。  

回滚单位：镜像 + 模型配置 + prompt + tool schema + Runtime Profile + Skill bundle。在途 Run 用创建时冻结版本完成或取消。

命令：`ops/clamp_generation_strategy_freeze.py`（及后续 `ops/set_agentic_qualification.py`）。

## 6. 第一批交付物（只做这些）

> 进度账本与修复波次：[`agentic-repair-wave-2026-08-02.md`](./agentic-repair-wave-2026-08-02.md)（ACTIVE）

| # | 交付物 | 状态 |
|---|---|---|
| 1 | `INVALID_BASELINE` + DecisionRecord | **DONE** |
| 2 | `FALLBACK_ONLY` + qualification 七态 | **DONE**（自动晋级机 → Q3） |
| 3 | Provider 截断/畸形 fail-closed 锁测 | **DONE** |
| 4 | Artifact manifest | **DONE** |
| 5 | Runtime Profile v1 | **DONE** |
| 6 | Runner 统一预算/transcript/去递归 | **DONE**（含 candidate branch 接线） |
| 7 | 真实 Preview 最小链 | **R1 DONE**（`PreviewProcessSupervisor`） |
| 8 | `accepted_workspace_snapshot` | **R1 DONE**（含失败 recoverable） |
| 9 | Offline Qualification 执行入口 | **R2 DONE***（fixture→OFFLINE；DOGFOOD 须 live+V2；ops 相邻门） |
| 10 | 5% Canary 开关 + 回滚 | **R0 DONE**：`set_agentic_qualification.py` + clamp |

## 7. 与现有文档关系

| 文档 | 新关系 |
|---|---|
| M6 canary watch plan | **HALTED_PENDING_QUALIFICATION**；扩流禁止 |
| token-cost-cache plan | 仍有效；Qual 泳道沿用 cache 布局 |
| GQ-4 pending note | GQ-4 仍关；删除「漏斗恢复后才能给候选流量」产品依赖 |
| PRD/Tech Spec | 须增补 Qualification Ladder / FALLBACK_ONLY（下一批文档同步） |

## 8. 建议实施顺序（本周）

```text
Day0  clamp + INVALID_BASELINE 报告改判 + 文档互链
Day1–2  qualification 状态 + 分流改写 + 单测
Day3–5  P0-2/P0-3/P0-5 主链（P0-1/P0-4 补验收）
Day6–7  Offline Qual 入口 + 首份 golden 报告
```

未过 Offline Qual **不得**再开生产 canary。
