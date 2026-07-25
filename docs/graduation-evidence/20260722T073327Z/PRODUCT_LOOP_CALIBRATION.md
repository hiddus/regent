# PRODUCT 门槛校准说明（2026-07-22）

Owner 决策：短期体验/App **不要求**达预期；P1 收敛 **演进闭环**，避免卡死。

| 项 | 旧读法 | 新读法（PRD §5.2） |
|---|---|---|
| G6 | ≥5 成功 Journey / 体验达标 | ≥1 次 不满→REVISE→新 Discovery/Preview；另至少 1 条独立路径 |
| G7 | 成功决策 | CONTINUE/**REVISE**/STOP + ≥3 合格 Observation（拒绝计入） |
| 员工「不是想要的」 | 质量失败卡阶段 | **合法闭环输入** |

`PRODUCT` 状态：**PASSED**（演进门槛；无强制日历窗）。长期变好靠持续 REVISE，不靠「再等七天」。

相关代码：
- `POST /v1/deployments/{id}/product-rejection`
- `product_rejection_count` 护栏指标
- 收紧外部证据启发式；CapabilityResolution 真解析
