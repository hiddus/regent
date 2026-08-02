# Decision Note — GQ-4 晋级（PENDING / 资格阶梯取代漏斗自锁）

> 状态：**PENDING** — GQ-4 未晋级  
> 日期：2026-07-31；**2026-08-01 重大修订**（见资格阶梯裁决）  
> 权威后续：[`decision-note-agentic-qualification-ladder-2026-08-01.md`](./decision-note-agentic-qualification-ladder-2026-08-01.md)

## 结论

**GQ-4 未晋级。** 默认生成策略不得翻为 agentic。

**2026-08-01：** 废止「必须先由 artifact-backed 恢复生产漏斗，agentic 才能获得验证流量」的自锁协议。  
artifact-backed 降为 `FALLBACK_ONLY`；agentic 须经 **Offline Qualification → Dogfood → Canary 阶梯** 证明绝对资格。  
既有 GQ-3 / 生产对照窗若 control 验证成功率≈0 且候选 starved，结论为 **`INVALID_BASELINE`**，不得用于 GQ-4。

## 禁止事项

1. **禁止**用 `.env=agentic` 宣称 GQ-4。  
2. **禁止**跳过资格阶梯直接翻默认。  
3. **禁止**要求 artifact-backed verified success > 0 才允许 agentic Offline Qual / Dogfood / 首档 canary。  
4. **禁止**用 `INVALID_BASELINE` 旧窗样本讨论 `PROMOTE_AGENTIC_CANDIDATE`。

## 下一步

见 [`agentic-qualification-executable-plan-2026-08-01.md`](./agentic-qualification-executable-plan-2026-08-01.md)：Day0 clamp + INVALID_BASELINE → Q1 主链 → Offline Qual → 再谈 CANARY_5。

## 记录

| 字段 | 值 |
|---|---|
| Decision | PENDING（GQ-4） |
| Scope | 默认 agentic 晋级 |
| Blocker | 资格阶梯未完成；非「等 artifact-backed 漏斗」 |
| Author | rechaos |
