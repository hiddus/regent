# 已登记未实现项（核查修复 Phase 3）

以下能力在需求中已识别，本轮**仅登记、不实现**：

| 项 | 来源 | 状态 |
|---|---|---|
| P2-3 Impact Graph（衰减 / 批量撤销 / 循环证据检测 / 重验证下游） | Spec §16 | 未实现 |
| P2-5 AgentEnvelope `correlation_id` + HMAC 签名 | Spec §17 | 未实现（`REGENT_AAR1_ENVELOPE_HMAC_KEY` 预留） |
| G0 ExternalOperation 完整闭环 | Spec §9 / §25 | 结构已有；完整闭环待 G0 合入 |

SelfImprovementRun（P2-8）代码已落地但属**候选**：API 返回 `candidate_ungated=true` / `decision_record_status=ROLLOUT_NOT_ALLOWED`，无产品 DecisionRecord 前不得宣称验收。

Hive：默认单 Agent；`REGENT_AAR1_CERTIFIED_HIVE` 仅为固定模板 opt-in，见 `.env.example`。
