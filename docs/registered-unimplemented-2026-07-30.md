# 已登记未实现项（核查修复 Phase 3 — 2026-07-30 更新）

| 项 | 来源 | 状态 |
|---|---|---|
| P2-3 Impact Graph（衰减 / 批量撤销 / 循环证据检测 / 重验证下游） | Spec §16 | **已实现**：`impact_graph_service.py` + `memory_impact_edges`（0037）+ MemoryService 接线 |
| P2-5 AgentEnvelope `correlation_id` + HMAC 签名 | Spec §17 | **已接线**：活跃路径 `agent_envelope` / `agent_mesh` 在配置 `REGENT_AAR1_ENVELOPE_HMAC_KEY` 时走 `envelope_v1` HMAC；无密钥时保留 digest 兼容 |
| G0 ExternalOperation 完整闭环 | Spec §9 / §25 | **半落地**：服务层 + 调度 EO + Worker 周期 `ReconciliationWorker.tick` 已挂；完整生产 provider query→resolve 全路径仍待后续合入 |
| ReleaseCandidate 人工闸门 | Spec §25 | **已强制**：P1 预览链创建 `RELEASE_APPROVAL` HumanTask，默认 `require_release_human_approval=true`，不再 auto-approve |
| R7 浏览器级 Gate（无 Playwright 时） | Spec §25 已知限制 #2 | 无 Playwright 时 dry-run；有 Playwright 时跑真实旅程（见 `test_browser_journey.py`） |
| P0#5 仓库内可复核的唯一 Product DecisionRecord | PRD §9.5 / Spec §25 #4 | **已纳入仓库**：`docs/experiments/p0-v1-artifacts/`（含签署 DecisionRecord 与 SHA 核对）+ 本地 `ExperimentService` 评分路径测试 |

SelfImprovementRun（P2-8）代码已落地但属**候选**：API 返回 `candidate_ungated=true` / `decision_record_status=ROLLOUT_NOT_ALLOWED`，无产品 DecisionRecord 前不得宣称验收。

Hive：默认单 Agent；`REGENT_AAR1_CERTIFIED_HIVE` 仅为固定模板 opt-in，见 `.env.example`。
