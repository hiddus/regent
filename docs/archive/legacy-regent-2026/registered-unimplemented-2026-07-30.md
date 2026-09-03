# 已登记未实现项（核查修复 Phase 3 — 2026-07-30 更新）

| 项 | 来源 | 状态 |
|---|---|---|
| P2-3 Impact Graph（衰减 / 批量撤销 / 循环证据检测 / 重验证下游） | Spec §16 | **已实现（部分生效）**：`impact_graph_service.py` + `memory_impact_edges`（0037）+ MemoryService 接线（循环/级联撤销/`can_support_gate`）。**已知限制（TS §25 #7）**：`confidence_decay` 仅单测可达，未进生产检索/打分 |
| P2-5 AgentEnvelope `correlation_id` + HMAC 签名 | Spec §17 | **已接线**：活跃路径 `agent_envelope` / `agent_mesh` 在配置 `REGENT_AAR1_ENVELOPE_HMAC_KEY` 时走 `envelope_v1` HMAC；无密钥时保留 digest 兼容 |
| G0 ExternalOperation 完整闭环 | Spec §9 / §25 | **半落地（已推进）**：服务层 + 调度 EO + Worker `tick`（stale→RECONCILING）+ `resolve_reconciling_via_query`（Deployment durable probe / 超期 MANUAL_REVIEW）已挂；跨 provider 真实网络 query→resolve 全路径仍待后续合入 |
| ReleaseCandidate 人工闸门 | Spec §25 | **已强制**：P1 预览链创建 `RELEASE_APPROVAL` HumanTask，默认 `require_release_human_approval=true`，不再 auto-approve |
| R7 浏览器级 Gate（无 Playwright 时） | Spec §25 已知限制 #2 | 无 Playwright 时 dry-run；有 Playwright 时跑真实旅程（见 `test_browser_journey.py`） |
| P0#5 仓库内可复核的唯一 Product DecisionRecord | PRD §9.5 / Spec §25 #4 | **已纳入仓库**：`docs/experiments/p0-v1-artifacts/`（含签署 DecisionRecord 与 SHA 核对）+ 本地 `ExperimentService` 评分路径测试 |

SelfImprovementRun（P2-8）代码已落地但属**候选**：API 返回 `candidate_ungated=true` / `decision_record_status=ROLLOUT_NOT_ALLOWED`，无产品 DecisionRecord 前不得宣称验收。

Hive：Settings **代码默认** `aar1_certified_hive=True`（`.env.example` 默认 on），启用认证固定模板 `pm-dev-independent-qa-v1`；可按 Goal metadata `force_single_agent` / `hive_enabled=false` 或 `REGENT_AAR1_CERTIFIED_HIVE=false` 退出。自适应自由拓扑的**生产权限继承**仍 `ROLLOUT_NOT_ALLOWED`（沙箱探索开放）。

## 隐私治理（PRD §7 — 对齐审计收尾轮）

| 项 | 来源 | 状态 |
|---|---|---|
| 隐私：告知同意 / PII 分级最小化 / 保留期可配置与超期匿名化 | PRD §7.1 / §7.2 / §7.3 | **已实现（2026-07-30）**：`privacy_consents`（0038）+ notice/grant/withdraw API；Goal 创建登记同意；Observation/对话采集闸门；`PiiClass`/`classify_and_minimize`；`privacy_retention_days` + Worker/`anonymize_expired`。§7.4 导出/删除保持。 |
| 导出与删除 | PRD §7.4 | **已实现**（既有） |
