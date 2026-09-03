# Regent 差距复检报告（2026-07-30 第二轮）

> 复检时间：2026-07-30 18:23（首轮分析 17:36 之后约 47 分钟）
> 修复闭环：2026-07-30（本轮按复检项落地代码/产物/部署）
> 方法：对首轮 `docs/gap-analysis-2026-07-30.md` 中每一项差距，重新直接读取当前源码/测试/权威清单核对
> 权威依据：`docs/registered-unimplemented-2026-07-30.md`（已更新）、`docs/p0-completion-report.md`、`docs/decision-note-verifiable-delivery-2026-07-30.md`

---

## 修复后状态（本轮闭环）

| # | 项 | 修复后状态 | 说明 |
|---|---|---|---|
| 1 | P0#5 DecisionRecord 仓内可复核 | ✅ **已修** | 生产产物同步至 `docs/experiments/p0-v1-artifacts/`（SHA 与 completion-report 一致）；`test_p0_decision_record_artifacts.py` 覆盖冻结任务集 + `ExperimentService` 真实评分路径 + 唯一 DecisionRecord |
| 2 | P2-1 `dispatch_with_eo` | ✅ 已确认修复（前轮） | 真实 EO prepare/begin_dispatch |
| 3 | P2-1 `preempt_with_eo_check` | ✅ 已确认修复（前轮） | 签名参数对齐 |
| 4 | R7 浏览器 gate | ✅ 已确认修复（前轮） | Playwright 可用时真旅程 |
| 5 | `test_evidence_chain_integrity` | ✅ 已确认修复（前轮） | 行为级持久化断言 |
| 6 | ReleaseCandidate 自动批准 | ✅ **已修** | 强制 `RELEASE_APPROVAL` HumanTask；`require_release_human_approval=true`；经 `ReleaseApprovalCompleted` 继续部署 |
| 7 | P2-3 Impact Graph | ✅ **已修** | `ImpactGraphService` + `memory_impact_edges` 迁移 0037：环检测/级联撤销/批量撤销/衰减/Gate 阻断 |
| 8 | P2-5 AgentEnvelope HMAC | ✅ **已修** | 活跃路径接线 `envelope_v1` HMAC；配置密钥时 fail-closed |
| 9 | G0 ExternalOperation 完整闭环 | ⚠️ **半落地（已推进）** | Worker `tick` + `resolve_reconciling_via_query`（durable probe）；跨 provider 网络 query 仍后续 |
| 10 | 文档口径张力 | ✅ **已修** | Spec §25 / `registered-unimplemented` / 本复检表统一为「产物可复核 + 已知半落地项」 |
| 11 | USER.md 残留 | ℹ️ 非本仓范围 | 跨项目身份文件 |

---

## 一、逐项复检结果（首轮 → 当前）

| # | 首轮报告的差距 | 当前状态 | 关键证据 |
|---|---|---|---|
| 1 | **P0#5：冻结 A/B/C + 唯一产品 DecisionRecord 未满足（阻塞项）** | ✅ **仓内可复核** | `docs/experiments/p0-v1-artifacts/` + `VERIFICATION.md`；DecisionRecord `ec17a72f…` / Manifest `0f64f746…` |
| 2 | **P2-1 `dispatch_with_eo` 为壳（不建真实 EO）** | ✅ **已修复** | `scheduler_service.py` 真实 EO 绑定 |
| 3 | **P2-1 `preempt_with_eo_check` 运行时 TypeError** | ✅ **已修复** | 参数签名对齐 |
| 4 | **R7 浏览器 gate 仅 dry-run** | ✅ **已修复（有真实路径）** | `test_browser_journey.py` |
| 5 | **结构级测试 `test_evidence_chain_integrity`** | ✅ **已修复** | 行为级落库断言 |
| 6 | **ReleaseCandidate 自动批准跳过人工** | ✅ **已修复** | 不再 `auto-approved by P1 execution chain` |
| 7 | **P2-3 Impact Graph 缺失** | ✅ **已修复** | `impact_graph_service.py` + 测试 |
| 8 | **P2-5 AgentEnvelope HMAC 未实现** | ✅ **已接线** | `agent_envelope.py` / `agent_mesh.py` → `envelope_v1` |
| 9 | **G0 ExternalOperation 完整闭环** | ⚠️ **半落地** | 服务+调度+Worker 对账 tick；完整 provider 对账待后续 |
| 10 | **文档过度宣称** | ✅ **已收敛** | §25 与登记清单已更新 |
| 11 | **USER.md 含无关树米/Showmac 残留** | ℹ️ 非 Regent 项目问题 | 跨项目历史残留 |

---

## 二、与目标对齐的总体判断

本轮将复检中仍开放的阻塞/高优先项（P0#5、Impact Graph、HMAC、ReleaseCandidate 人工闸门、文档口径）落地；G0 完整生产对账保留为半落地（Worker 扫描已挂，缺全量 provider resolve 编排）。

---

## 三、建议下一步

1. G0：为 preview/MCP provider 补齐 query adapter → `resolve_reconcile` 端到端故障窗演练。
2. 生产启用 `REGENT_AAR1_ENVELOPE_HMAC_KEY` 后做一次 Agent Mesh 回归。
3. 运营侧确认 ReleaseApproval 人工任务 SLA。

---

## 附录：未闭环原因

| 项 | 原因 | 本轮已做最大切片 |
|---|---|---|
| #9 G0 完整生产闭环 | 需真实 provider I/O 对账编排与故障窗演练（月级工程切片） | Worker 周期 `ReconciliationWorker.tick`；调度 EO 真实绑定沿用前轮 |
| #11 USER.md | 非本仓库范围 | 无改动 |

_说明：修复后已跑相关 pytest；部署经 `ops/deploy_console.py` + `ops/sync_local_to_server.py` 验证公网 console/health。_
