# PRODUCT 门槛再校准：废除强制 7 天窗

> Owner 指示（2026-07-22）：Regent 要的是**不断进化**，不是等七天现实验证。  
> 定义依据：`REGENT-DEFINITION-1.0` 属性 6（外部结果闭环）+ 属性 7（明确终态）——持续调整，而非日历待机。

## 变更

| 项 | 旧 | 新 |
|---|---|---|
| G6 样本/窗口 | ≥7 天观察窗 | **无日历下限**；以可查询闭环轮次为准 |
| 同日多次验证 | 不能替代 7 天 | **可以**累计为独立路径/Observation |
| PRODUCT 签署时机 | 等 `closes_at_earliest` | 闭环证据齐即可签 |

## 仍禁止

- 内部 smoke / 假点击 CONTINUE 充当闭环  
- 口头不满未入系统  
- 未写 `P2StartDecisionRecord` 却开工 Scheduler  
- 文档仍 CONDITIONAL 却开工 P2（与日历无关的门禁仍在）

## 本包证据（已满足新 G6/G7）

见 `g6_g7_prewindow_evidence.json`：2× 拒绝→REVISE，≥3 Observation。
