# Regent：项目目标 vs 代码实现差距分析

> 分析日期：2026-07-30
> 方法：四份目标文档（PRD / Plan / Technical-Spec / Measurement-Framework）+ 权威清单（decision-note、registered-unimplemented、regent-verification 附录）比对 **实际源码与测试文件**
> 范围：`core/`（166 模块）、`apps/`、`tests/`（80+ 测试）、`fixtures/`
> 结论可信度：已直接读取关键实现与测试，非纯文档比对

---

## 一、总体判断

| 维度 | 结论 |
|---|---|
| 功能链路覆盖 | ✅ 广覆盖：P0 三套状态机/Outbox/Lease/Audit、P1 R1–R8（发现→生成→构建→预览→观测→决策闭环）、P2-1/2/4 核心均已落地代码 |
| 治理度量缺口 | ✅ 已被大幅补齐（北极星/护栏、隐私导出删除、证据五分类、Permit 行为测试、Eval 真实评分均已有实现与测试） |
| **可验证交付** | ⚠️ 仍有**硬缺口**：P0 完成定义第 5 条（冻结 A/B/C 对照 + 唯一产品 DecisionRecord）未满足，是 P0 毕业的**阻塞项** |
| 文档可信度 | ⚠️ 存在"已声称完成、实为骨架/桩/被 mock 掩盖"的项，以及文档过度宣称，需修正 |

**一句话**：构建块齐备且大多行为级真实，但"用可验证证据证明目标达成"这一产品身份核心要求尚未闭环，且少数 P2 集成点存在真实缺陷。

---

## 二、已实现（已核对源码/测试存在）

- **P0 全链路**：`CSV_SUMMARY_BASELINE`、`EVT_PARSER_GAP` 均有治理管道集成测试（`test_csv_summary_governance_path.py`、`test_evt_parser_gap_governance.py`）；Goal/Work/Run 状态机、Outbox、Lease、Timer、Artifact、Evidence、Audit 均落地。
- **P1 应用闭环**：`AllowlistedHttpEvidenceConnector`/`GoalIntentEvidenceConnector` 真实抓取（受 egress 代理+白名单门控）；`capability_resolution/build/acquire` 真实；`StaticAppPublisher` + `app_preview_service` 真实发布预览；`iteration_loop_service` 真实 REVISE 闭环；`self_improvement_sandbox` 真实 AST/compileall 隔离。
- **治理度量（已修）**：`north_star_metrics.py` + `/v1/governance/north-star`、`privacy_service.py`（导出/删除）、`evidence_policy.py`（五分类+Gate 排除）、`eval_harness_service.py`（真实评分+统计 Gate）、`memory_service.py`（MemoryRecord+撤销）。
- **P2-4 Eval Harness**：`runner.py` 真实驱动模型、`fixtures/eval_task_set_v1.json` + `eval_single_agent_baseline_v1.json` 冻结任务集存在、`statistical_gate` 真实（pass@k + 95% CI）。
- **前端**：`apps/regent-console/` 为真实 React19+Vite+TS，三栏布局、SSE 实时、`status.agents`/`live_action` 参与 Agent 名册非骨架。
- **G0 ExternalOperation 服务**：`external_operation_service.py` 含 `prepare/begin_dispatch/mark_*/reconcile`，`reconciliation_worker.py` 周期扫描，已接线 release 路径，并有 G8 故障注入测试。

---

## 三、真实差距（按严重度）

### A. 阻塞 P0 毕业（最高优先级）

| 差距 | 目标依据 | 现状 | 证据 |
|---|---|---|---|
| **P0#5：冻结 A/B/C 首轮对照 + 唯一产品 DecisionRecord** | PRD §9.5 / §5.3 | ❌ 未满足 | 基础设施（任务集 fixture + harness + 统计 Gate）已齐，但**尚未执行并产出签署的唯一 Product DecisionRecord**；Tech-Spec §25 明确"不得仅凭模块/夹具信号宣称已满足" |

### B. 已登记未实现（权威 `registered-unimplemented-2026-07-30.md`）

| 差距 | 依据 | 现状 |
|---|---|---|
| **P2-3 Impact Graph**（衰减/批量撤销/循环检测/重验证下游） | Spec §16 | 完全缺失；`MemoryRecord` + `REVOKED` 状态有，但 Impact Graph 无类无表 |
| **P2-5 AgentEnvelope `correlation_id` + HMAC 签名** | Spec §17 | 未实现（`REGENT_AAR1_ENVELOPE_HMAC_KEY` 预留）；自适应组织仅"提案"、`ROLLOUT_NOT_ALLOWED` 从不激活 |
| **G0 ExternalOperation 完整闭环** | Spec §9 / §25 | 服务层有，但项目自身登记为"待合入/不开启"；且 P2-1 调度 EO 集成为骨架（见 C） |

### C. 真实缺陷（代码已就位但为骨架/桩/被 mock 掩盖）

| 差距 | 依据 | 证据 | 严重度 |
|---|---|---|---|
| **P2-1 `dispatch_with_eo` 为壳** | Spec §15（G0 前置） | `scheduler_service.py:689` 只把 `eo_binding.bound=True` 写入决策 JSON，**从不调用 `ExternalOperationService` 创建真实 EO 行** | 高 |
| **P2-1 `preempt_with_eo_check` 运行时必崩** | 同上 | `scheduler_service.py:763` 调 `self.preempt(org_key=, target_goal_id=, actor=)`，但 `preempt` 强制要求 `queue_entry_id`+`reason`（:531-532）→ 运行时 `TypeError`；测试用 mock 掩盖 | 高 |
| **R7 浏览器级 Gate 仅 dry-run** | PRD §6 R7 | `test_browser_journey.py` 只断言 dry-run；Playwright 未装时跳过真实验收，CI 不覆盖 | 中 |
| **ReleaseCandidate 自动批准** | 治理门 | `execution_orchestrator.py` 创建候选后立即 `approve("auto-approved by P1 execution chain")`，跳过人工任务 | 中 |
| **测试可验证性系统性问题** | PRD §26（禁止结构级/类名/伪 Observation） | ① 恢复/幂等/对账/Permit 撤销多处用 `AsyncMock` 验证"调用正确"而非真实落库断言；② `test_evidence_chain_integrity.py` 编译 DDL 断言**外键列名字符串出现**（正是规范禁止的结构级测试）；③ Docker sandbox 注入假 runner 仅断言命令字符串，不启动容器；④ CI 仅 `ruff→mypy→pytest`，未分设状态转换/幂等/权限/恢复独立门禁 | 中 |

### D. 文档可信度 / 一致性

- **过度宣称**：Tech-Spec §25 称"P0 全链路通过 / P1 R1–R8 完成"，但 P0#5（A/B/C 对照）未满足——"完成"实际指结构/链路落地，与决策注记自身"不以模块存在当交付"的口径冲突；Hive/SelfImprovement 在 §25 被写成"已完成/可体验"，实则为 opt-in 候选/未放行（见 B）。
- **验证报告正文为修复前快照**：`regent-verification-2026-07-30.md` 正文仍标 ❌，已用醒目"时点快照"提示 + 附录"修复后状态"修正，引用时需以附录/decision-note 为准。
- **无关残留**：`USER.md` 含"树米科技/Showmac 企业宣传册"——与 Regent 项目无关的历史残留（属助手身份文件，非项目目标）。

---

## 四、P1 Graduation 风险（需关注）

- **G6（演进闭环可演示）**：`iteration_loop_service` 真实，但无证据显示已用**真实外部用户**（非内部流量）跑通 ≥1 次完整闭环 + 1 次独立路径。
- **G7（真实 Observation 决策）**：无证据显示已基于 ≥3 条合格外部 Observation 产出唯一决策。
- 这两项是 P1 毕业门禁，目前"能力在、未验证"，与 P0#5 同源风险。

---

## 五、建议优先级

1. **【阻塞】跑通 P0#5**：用 `eval_task_set_v1.json` 执行真实 A/B/C 对照（含 ≥30 任务、隐藏测试预签名、统计 Gate），产出**唯一签署的 Product DecisionRecord**；否则 P0 不得毕业。
2. **【高】修 P2-1 调度 EO 集成**：`dispatch_with_eo` 改为调用 `ExternalOperationService` 创建真实 EO；修正 `preempt_with_eo_check` 参数；补**真实 DB 集成测试**（替换 mock session）。
3. **【中】按登记排期补齐**：P2-3 Impact Graph、P2-5 HMAC（决策注记明确"不开启"，需 Eval 正收益后再排）。
4. **【中】修治理缺陷**：R7 接入真实 Playwright gate；ReleaseCandidate 加人工闸门或显式登记为已知限制。
5. **【中】清理测试/CI**：删结构级测试、补 Permit/恢复/对账真实行为测试、CI 分设阶段门禁、Docker sandbox 端到端验证。
6. **【低】修文档一致性**：§25 收敛"完成"口径、修正 Hive/SelfImprovement 表述、清理 `USER.md` 无关残留。

---

_说明：本报告基于当前代码与权威文档直接比对。P0#5 / G6 / G7 的"是否已验证"需实跑验收套件（依赖 PostgreSQL + 模型 provider）方能最终判定；本报告已尽最大可能用静态证据支撑结论。_

---

## 附录：修复后状态（2026-07-30 当晚）

> 对照上文「真实差距」表；**不改写**正文历史结论。权威登记见 `docs/registered-unimplemented-2026-07-30.md`、Spec §25。

| 差距项 | 修复后 | 指向 / 说明 |
|---|---|---|
| P2-1 `dispatch_with_eo` 为壳 | **已修** | `scheduler_service.dispatch_with_eo` → Permit claim → `ExternalOperationService.prepare` + `begin_dispatch`；provider `scheduler-dispatch-v1`；真实 DB 测 `tests/integration/test_scheduler_e2e.py` |
| P2-1 `preempt_with_eo_check` 运行时必崩 | **已修** | 查找目标 Goal 的 `SCHEDULED` 队列项后调用 `preempt(queue_entry_id=, reason=)`；DISPATCHING EO 仍拒绝 |
| ReleaseCandidate 自动批准 | **已登记为已知限制** | Spec §25 #1 + `registered-unimplemented`；人工批准 API 仍可用 |
| R7 浏览器 Gate 仅 dry-run | **半落地** | 无 Playwright → dry-run（已知限制）；有 Playwright → `test_real_playwright_journey_when_available` 真跑 |
| 证据链结构级测试 | **已过时（此前已修）** | `test_evidence_chain_integrity.py` 为行为级落库断言 |
| P2-3 / P2-5 / G0 完整闭环 | **刻意后置** | 见 registered-unimplemented |
| P0#5 A/B/C + 唯一 Product DecisionRecord | **仍阻塞（产品毕业）** | 基础设施齐；仓库无签署 DecisionRecord 产物；需生产/评测环境实跑，本轮未伪修 |
| G6 / G7 外部用户验证 | **未做** | 需真实外部 Observation；非本轮代码可闭环项 |
| `USER.md` 无关残留 | **不适用** | 仓库内无 `USER.md` |
| 文档 / 控制台部署脚本 | **已同步** | PRD §4.3、Spec §25 控制台表述、`ops/deploy_console.py` 同步 `/console/` 至 regent-api |
