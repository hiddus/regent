# 已登记未实现项（核查修复 Phase 3）

以下能力在需求中已识别，本轮**仅登记、不实现**：

| 项 | 来源 | 状态 |
|---|---|---|
| P2-3 Impact Graph（衰减 / 批量撤销 / 循环证据检测 / 重验证下游） | Spec §16 | 未实现 |
| P2-5 AgentEnvelope `correlation_id` + HMAC 签名 | Spec §17 | 未实现（`REGENT_AAR1_ENVELOPE_HMAC_KEY` 预留） |
| G0 ExternalOperation 完整闭环 | Spec §9 / §25 | 服务层 + 调度 EO 绑定已有；完整 provider 对账/生产闭环待 G0 合入 |
| ReleaseCandidate 人工闸门 | Spec §25 已知限制 #1 | P1 执行链仍 `auto-approved`；人工批准 API 存在，默认不强制 |
| R7 浏览器级 Gate（无 Playwright 时） | Spec §25 已知限制 #2 | 无 Playwright 时 dry-run；有 Playwright 时跑真实旅程（见 `test_browser_journey.py`） |
| P0#5 仓库内可复核的唯一 Product DecisionRecord | PRD §9.5 / Spec §25 #4 | 生产曾有实验报告（见 `docs/p0-completion-report.md`）；仓库夹具+Harness 齐备，但本仓不含签署的唯一 Product DecisionRecord 产物，不得仅凭模块宣称毕业 |

SelfImprovementRun（P2-8）代码已落地但属**候选**：API 返回 `candidate_ungated=true` / `decision_record_status=ROLLOUT_NOT_ALLOWED`，无产品 DecisionRecord 前不得宣称验收。

Hive：默认单 Agent；`REGENT_AAR1_CERTIFIED_HIVE` 仅为固定模板 opt-in，见 `.env.example`。
