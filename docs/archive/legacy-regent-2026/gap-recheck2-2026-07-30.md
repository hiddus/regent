# 项目目标 vs 代码实现 — 第三轮校验（2026-07-30 代码更新后）

> **对齐审计收尾轮补充（同日）**：PRD §7.1–7.3 隐私已落地；ops 历史脚本已迁入 `archive/oneoff/`；G0 增加 `resolve_reconciling_via_query`。详见 `docs/alignment-audit-2026-07-30.md` 附录。

> 方法：直接读取当前源码、测试、权威登记清单（`docs/registered-unimplemented-2026-07-30.md`）与仓内产物，**未**实跑 pytest（依赖 PostgreSQL + 模型 Provider）。结论以静态证据支撑。
> 前两轮：`docs/gap-analysis-2026-07-30.md`、`docs/gap-recheck-2026-07-30.md`。

## 总体结论

**项目已与目标基本对齐。** 首轮报告标记的差距在第三轮基本全部闭环：P0#5 阻塞项已解决、P2-3 / P2-5 实现并接线、ReleaseCandidate 改为强制人工闸门。仅剩 **1 项真实半落地（G0 生产级闭环）** 与 **2 项设计上的"候选/opt-in"（非差距，按治理不得宣称验收）**。应用层已无 `NotImplementedError`/TODO/STUB。

## 逐项核验结果

### ✅ 已闭环（前轮差距，本轮确认）

| 项 | 前轮状态 | 本轮证据 |
|---|---|---|
| **P0#5 仓内可复核 DecisionRecord** | 阻塞（产物不在仓内） | `docs/experiments/p0-v1-artifacts/` 已入仓（README/experiment-report/raw-run-manifest/VERIFICATION），含 SHA-256 与签名；DecisionRecord `ec17a72f`，`STOP_GENERALIZATION`，270 runs；`tests/unit/application/test_p0_decision_record_artifacts.py` 本地可复现（`ExperimentService.freeze→record_run→finalize`，无 `hash%2` 桩）；`docs/experiments/p0-task-set-v1.json` 存在 |
| **P2-3 Impact Graph** | 缺失 | `impact_graph_service.py` 真实实现：循环检测 `_would_create_cycle`、级联撤销 `revoke_cascade`、批量撤销 `batch_revoke`、置信度衰减 `confidence_decay`(半衰期 30d)、`can_support_gate` 阻断重验证；`MemoryService` 已接线；`tests/unit/application/test_impact_graph.py` 行为级测试 |
| **P2-5 AgentEnvelope HMAC** | 未接线（活跃路径纯 sha256） | `agent_envelope.py` 现 import `envelope_v1`（`build_unsigned_fields/sign_envelope/verify_envelope`）；`create_envelope` 在 `REGENT_AAR1_ENVELOPE_HMAC_KEY` 配置时走 RFC8785 + HMAC-SHA256；`verify_trust/verify_hmac/derive_child_envelope` 均支持校验；`tests/unit/application/test_agent_envelope.py` 存在 |
| **ReleaseCandidate 人工闸门** | auto-approve（已知限制） | `config.require_release_human_approval: bool = True`；`execution_orchestrator` 创建 `RELEASE_APPROVAL` HumanTask 并 await；`release_service.py` 读取该开关；`auto-approved by P1` 已移除 |
| **P2-1 dispatch_with_eo** | 已修（壳→真 EO） | 本轮复读 `scheduler_service.py:692+` 仍真实创建 ExternalOperation 行（Permit→claim→prepare→begin_dispatch→幂等） |
| **P2-1 preempt_with_eo_check** | 已修（TypeError） | 本轮复读仍正确传 `queue_entry_id`/`reason`，无崩溃 |
| **R7 浏览器 Gate** | 已修（仅 dry-run） | `test_browser_journey.py` 含 `test_real_playwright_journey_when_available`，有 Playwright 时真实验收 |
| **结构级测试** | 已修 | `test_evidence_chain_integrity.py` 已为真实落库 + 证据链断言（符合 PRD §26 禁令） |
| **USER.md 树米/Showmac 残留** | 全局身份文件非项目范畴 | 用户确认非项目文件，忽略 ✅（不计入项目差距） |

### ⚠️ 半落地（已知范围，已登记）

| 项 | 状态 | 证据 |
|---|---|---|
| **G0 ExternalOperation 完整闭环** | 半落地（已推进） | 服务层 + 调度 + Worker `tick`（stale→RECONCILING）+ `resolve_reconciling_via_query`（Deployment durable probe / 超期 MANUAL_REVIEW）；**跨 provider 真实网络 query→resolve 仍待后续** |

### 🔒 设计上的候选/opt-in（非差距，按治理不得宣称验收）

- **SelfImprovementRun（P2-8）**：代码落地但 `decision_record_status=ROLLOUT_NOT_ALLOWED` / `candidate_ungated=true`，属候选。
- **Hive 自适应组织**：默认单 Agent；`REGENT_AAR1_CERTIFIED_HIVE` 仅为固定模板 opt-in，默认 `ROLLOUT_NOT_ALLOWED`。

## 对齐判断

- **P0**：完成定义 5 条现可仓内复核（含签署 DecisionRecord + SHA + 可复现评分测试），此前阻塞的 P0#5 已解除 → **P0 可宣称达成**（以实跑验收套件为最终裁定）。
- **P1（R1–R8）**：生成→构建→预览→观测→决策闭环、证据五分类、受监管 SelfImprovement 沙箱、ReleaseCandidate 人工闸门均到位。
- **P2**：P2-1/2/3/4/5 核心均已实现并接线；仅 G0 生产级闭环与 P2-8/Hive 候选项按治理保持未放行。

## 建议

1. **收尾 G0**：补全生产 provider `query→resolve` 全路径，或对"半落地"给出明确的规格范围结论（以免长期悬而未决）。
2. **最终裁定**：实跑 `pytest`（PG + 模型 Provider）以最终确认 P0#5 与全链路通过，作为 P0 毕业的硬证据。
3. **保持治理纪律**：`registered-unimplemented` 已转为"已登记已实现/已知限制"的权威清单，建议持续维护，使差距始终可复核（证据优先、失败关闭）。

## 新增差距扫描

应用层 `core/src/regent/application` 无 `NotImplementedError` / `TODO` / `FIXME` / `STUB` 残留，本轮未发现此前未登记的新缺口。
