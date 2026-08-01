# Decision Note — GQ-4 晋级（PENDING / GQ-3 窗 degraded）

> 状态：**PENDING** — GQ-4 未晋级；GQ-3 采数窗标为 **degraded**（交付漏斗不健康）  
> 日期：2026-07-31（前置与 next-plan 重订对齐）；**2026-08-01 增补 degraded**  
> 关联：[`conversational-delivery-next-plan-2026-07-31.md`](./conversational-delivery-next-plan-2026-07-31.md) · CD-8/9 · [`gq34-promotion-control-flow-2026-07-31.md`](./gq34-promotion-control-flow-2026-07-31.md) · [`cd6-execution-plan-2026-07-31.md`](./cd6-execution-plan-2026-07-31.md)

## 结论

**GQ-4 未晋级。** 默认生成策略仍为 artifact-backed 门禁下的受控路径；不得将运维侧 `.env` 中 `generation_strategy=agentic` 或类似本地开关表述为 GQ-4 已完成。

**2026-08-01：** 主矛盾是交付管道卡死（outbox 毒事件、`path outside plan`、`FAILED_TERMINAL`、PENDING 积压），不是「等 GQ-4 凑样本」。在 `funnel_degraded=true`（报告字段）期间：

- **禁止**将「等 agentic 样本」当作产品主叙事；
- **禁止** `apply_gq4_promotion --execute`；
- canary 可保持合同 percent（默认 20%）边修边采，但晋级门强制 BLOCKED。

## 前置条件

| 门禁 | 状态 | 说明 |
|---|---|---|
| CD-0.1 名义沙箱隔离 | ✅ 代码侧 | 生产强制 docker；**不足以**开窗 |
| **CD-6 全绿**（N-3 / N-3c / N-3d / N-3b / N-2 + T1–T6） | 🟢 S0 已验证 | 专用 agent-exec；uid；host_path_map；三联验收 |
| **CD-7** 技 P1-1…4 + N-4/N-6 | 🟢 代码侧收口 | 见 next-plan 7.1–7.5 |
| Verification 闸门等控制流 | ✅ 代码侧 | 不替代 CD-6/7 |
| GQ-3 真实流量窗（CD-8） | **合同已签 / 开窗中 / degraded** | [`decision-note-gq3-window-2026-07-31.md`](./decision-note-gq3-window-2026-07-31.md)；20% canary；漏斗健康前不评判晋级 |
| GQ-4 DecisionRecord | **未写** | 仅 GQ-3 达标且 funnel 健康后可起草 ACCEPTED |

## 禁止事项

1. **禁止**在未完成 **CD-6 全绿 + CD-7** 前提高 `canary_percent` 或于生产打开 `canary_gate`。  
2. **禁止**用 `.env=agentic` 宣称 GQ-4。  
3. **禁止**跳过 GQ-3 直接翻默认。  
4. **禁止**仅用 `echo ok` 宣称沙箱已闭环。  
5. **禁止**在双臂 `pass_rate≈0` / `funnel_degraded` 时讨论 GQ-4 ACCEPTED 或手工贴 agentic 充样本。

## 漏斗恢复门槛（撤 degraded）

同时满足后再重开晋级评判（仍须原合同 n≥30/臂 + `PROMOTE_AGENTIC_CANDIDATE`）：

1. stale ACTIVE（>2h 无更新）比率 < 20%；  
2. `GenerationRunRequested` PENDING 积压接近清零；  
3. 日均「到达 GenerationPlan」Goal 健康；  
4. 至少一臂 `pass_rate > 0` 且该臂 n≥10。

报告字段：`funnel_degraded` / `funnel_health`（见 `ops/gq3_production_report.py`）。

## 下一步

1. 完成交付管道解卡（DeliveryStateChanged handler、path allowlist、FAILED_TERMINAL 幂等、清毒 outbox）。  
2. 采数至合同样本（或 21 天窗到期）→ `python ops/gq3_production_report.py`。  
3. 仅当报告 `decision=PROMOTE_AGENTIC_CANDIDATE` **且** `funnel_degraded=false` 且 `apply_gq4_promotion` 通过：ACCEPTED DecisionRecord + 翻 `REGENT_GENERATION_STRATEGY=agentic`。  
4. 否则维持 PENDING；可关窗 / 调 percent（改合同）或继续 CD-10。

## 记录

| 字段 | 值 |
|---|---|
| Decision | PENDING |
| Scope | GQ-4 默认 agentic 晋级 |
| Blocker | 交付漏斗 degraded + GQ-3 报告未达标；见 `ops/gq3_production_report.py` |
| Author | rechaos |
| Reviewed | — |
