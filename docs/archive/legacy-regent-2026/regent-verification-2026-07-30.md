# Regent 需求—实现核查报告

> **⚠️ 时点快照说明（请先读）**  
> 本文件正文为 **2026-07-30 修复前** 的静态核查快照；文中 ❌ / 「未实现」等结论**未随后续落地回写**，请勿当作当前状态。  
> **修复后权威状态**以以下文档为准：  
> - `docs/decision-note-verifiable-delivery-2026-07-30.md`  
> - `docs/registered-unimplemented-2026-07-30.md`  
> - `Regent-Technical-Spec.md` §25  
> 另见文末附录「修复后状态（2026-07-30+）」。
>
> **口径更新（2026-08-11，定义 3.0）**：正文中「单 Agent 默认 / 动态组织须实验验证净收益 / 非默认」等表述，按现行定义只约束**晋级生产默认与扩大不可逆现实权限**；沙箱内的候选拓扑与组织试验默认开放。净收益 Gate 不是探索前置。

> 核查日期：2026-07-30  
> 方法：静态代码 + 文档比对（未执行 pytest；环境未安装运行时依赖，详见第六节）  
> 依据文档：`Regent-PRD.md`、`Regent-Plan.md`、`Regent-Technical-Spec.md`、`Regent-Measurement-Decision-Framework.md`、当时的冻结定义 `docs/definitions/REGENT-DEFINITION-1.0.txt`（现行规范源已是 `REGENT-DEFINITION-3.0`）  
> 代码范围：`C:\regent\core`（166 模块）、`C:\regent\apps`、`C:\regent\tests`（81 测试文件）

---

## 一、核查结论速览

| 问题 | 结论 | 一句话 |
|---|---|---|
| 1. 项目目标是否被需求文档覆盖 | ✅ 已覆盖 | 永久定义 + PRD + 测量框架 + 技术规格构成完整需求基线，目标无重大遗漏 |
| 2. 代码实现是否已覆盖需求文档 | ⚠️ 部分覆盖 | 功能链路广覆盖，但产品治理类需求（北极星/护栏/隐私导出/证据分类）未实现 |
| 3. 需求文档有、代码未实现的内容 | ❌ 存在 | 北极星指标、护栏红线、§7.4 导出删除、证据五分类、Eval 真实评分、G0 完整闭环等 |
| 4. 实现代码有、需求文档未包含的内容 | ⚠️ 存在 | Tauri 桌面端、SelfImprovementRun（P2-8 候选提前落地）、HiveRuntime 被过度宣称 |
| 5. 项目目标是否已被满足 | ❌ 未满足 | 构建块齐备但**未被可验证地证明**；P0 完成定义第 5 条（A/B/C 实验+DecisionRecord）无法靠桩评分满足 |

---

## 二、逐项核查

### Q1 项目目标是否被需求文档覆盖

**结论：已覆盖。**

- 产品身份/使命由当时的冻结定义 `REGENT-DEFINITION-1.0.txt` 锚定（现行为 `REGENT-DEFINITION-3.0`），PRD §0 引用并声明「CI 在定义 ID/哈希漂移时阻止合并」，目标权威源清晰。
- 目标分层落在四份文档：`Regent-PRD.md`（P0/P1/P2 产品使命、ICP、非目标、北极星/护栏）、`Regent-Plan.md`（实现切片 S0–S8）、`Regent-Technical-Spec.md`（架构与状态机）、`Regent-Measurement-Decision-Framework.md`（P2-4 实验合同）。
- 未发现「代码中存在、但文档未描述」的产品级目标（Q4 中的溢出项属于实现层/候选层，非目标层）。
- **注意**：`USER.md` 中「树米科技/Showmac 企业宣传册」为无关历史残留，与 `C:\regent` 的 Regent 项目无关，不计入本项目目标。

### Q2 代码实现是否已覆盖需求文档

**结论：功能广覆盖，治理类需求缺口明显。** 按领域：

| 领域 | 需求（来源） | 代码状态 | 测试状态 |
|---|---|---|---|
| 三套状态机 Goal/Work/Run | Plan §2-5 | ✅ 已实现 | 多为 mock/结构级 |
| Outbox/Lease/Timer/Artifact/Evidence/Audit | Plan §1 | ✅ 已实现 | 混合（少量行为级） |
| Permit 生命周期 + 不变量 | Plan §7 / Spec §10 | ✅ 已实现 | 撤销/fencing/重复领取**缺专门测试** |
| ExternalOperation（G0 前置） | Spec §9 / §25 | ⚠️ 结构已实现，完整闭环待 G0 合入 | mock 级 |
| CSV_SUMMARY_BASELINE | PRD §6 | ✅ 实现+测试 | ⚠️ 仅测计算/哈希/幂等，**绕过治理管道** |
| EVT_PARSER_GAP | Plan §9 | ✅ 实现+测试 | ⚠️ 仅测函数级，能力缺口认证**未测** |
| P1 能力链（12 项） | Spec §4/§13 | ✅ 全部有真实代码 | 路径逃逸/sandbox 命令行为级；其余 mock |
| P2-1 调度 | Spec §15 | ✅ 已实现 | checkpoint 测试为 AsyncMock |
| P2-2 Runtime Profile | Spec §14 | ✅ 已实现 | — |
| P2-3 长期记忆 | Spec §16 | ⚠️ 部分（缺 Impact Graph/衰减/批量撤销/循环检测） | — |
| P2-4 Eval Harness | Spec §18 | ⚠️ 结构在，评分为桩（`hash%2`） | — |
| P2-5 自适应组织/AgentEnvelope | Spec §17 | ⚠️ 部分，非默认；缺 HMAC 签名 | — |
| 北极星 CostPerVerifiedSuccess（§8.1） | PRD §8 | ❌ 未实现 | — |
| 护栏红线（§8.2） | PRD §8 | ❌ 未实现 | — |
| 隐私导出/删除（§7.4） | PRD §7 | ❌ 未实现 | — |
| 证据五分类 + 内部流量不得入 Gate（§12） | Spec §12 | ❌ 仅 2 类，未落地五类 | — |
| DecisionRecord 阶段门强制绑定 | Spec §24/§26 | ⚠️ 存在模型，未强制各阶段门 | — |

### Q3 需求文档有、代码未实现的内容

1. **北极星指标与护栏（PRD §8）**：全代码无 `CostPerVerifiedSuccess`、28 天窗口、分母≥10、以及 8 条护栏红线（核心任务完成率<70% 等）。属产品级硬需求，零实现。
2. **隐私导出/删除（PRD §7.4）**：Goal Owner 级导出包与删除回执无代码（`export`/`delete` 全仓无匹配）。
3. **证据五分类（Spec §12）**：仅 `DECLARED_INTENT` / `UNTRUSTED_DATA` 两态，规范要求的 5 类及「operational-observation 不得满足产品 Gate」未以分类机制落地。
4. **Eval Harness 真实评分（Spec §18 / 测量框架）**：`run_and_score` 为确定性桩（`hash%2`），无法支撑 P0 完成定义第 5 条的 A/B/C 对照实验。
5. **P2-3 记忆完整能力（Spec §16）**：Impact Graph、冲突处理、衰减、批量撤销、循环证据检测、重验证下游标均未实现。
6. **P2-5 AgentEnvelope 完整性（Spec §17）**：缺 `correlation_id` 与 HMAC 签名；自适应组织未设为默认。
7. **ExternalOperation 完整闭环（G0，Spec §9 / §25）**：§25 自身承认「完整闭环需在 G0 合入」。
8. **DecisionRecord 各阶段门强制唯一签名（Spec §24/§26）**：Eval/Org 仅普通 `decision` 字段，未创建规范的唯一签名 DecisionRecord。

### Q4 实现代码有、需求文档未包含的内容

1. **Tauri 桌面端应用骨架**（Spec §25 列出）：PRD 仅描述 Core + Web Console，桌面端未在需求中定义 → 实现溢出。
2. **SelfImprovementRun（受监管自我改进，Spec §25 #0021）**：对应 P2-8「受监管自我改进」，PRD §9.3 明确为**候选**（需单独产品 DecisionRecord），但已作为完成项实现，缺少其门禁 DecisionRecord。
3. **HiveRuntime / 蜂巢并行执行架构（Spec §25）**：实为「opt-in 固定模板，自适应自由拓扑 ROLLOUT_NOT_ALLOWED」，与规范「单 Agent 默认、动态组织需实验验证」不冲突，属合规的固定模板候选；但 §25 以「并行执行架构已完成」表述，易误导为已验证的默认能力 → **过度宣称**（非需求外，但表述越界）。

> 正负说明：P2-1 调度、P2-2 Runtime Profile 等「承诺」项已提前落地，属超前于 P0/P1 完成要求的**计划内**工作，不算需求外溢出，但意味着资源已投入尚未到验收时点的阶段。

### Q5 项目目标是否已被满足

**结论：未满足（构建块齐备，但不可验证地证明）。**

永久定义的核心目标是「可靠、受治理的目标执行内核」。对照 **PRD §9 / §5.3 的 P0 完成定义五条件**：

1. ✅ Core 在空 Apps 下通过 `CSV_SUMMARY_BASELINE` —— 计算/哈希/幂等测试通过，但**未证明** Goal/Work/Run 形成、状态机终态、Worker 恢复（测试绕过治理管道）。
2. ⚠️ 普通 Goal 补齐能力缺口并通过 `EVT_PARSER_GAP` —— `EvtParserGapService` 具备完整链路（候选 Tool / Goal 范围认证 / 隐藏测试隔离 / 幂等），但**无验收测试驱动**这些治理点。
3. ✅ 独立 App 创建且不改 Core 领域模型 —— `StaticAppPublisher` 已实现。
4. ⚠️ 可恢复、幂等、受控副作用、Evidence/Permit/Audit 可追溯 —— 代码实现存在，但恢复/幂等/对账/Permit 撤销等**主要靠 mock 证明**，未达规范「禁止伪 Observation/类名/字符串检查」的行为验收要求。
5. ❌ A/B/C 冻结任务集首轮对照实验 + 唯一产品 DecisionRecord —— **无法满足**：Eval Harness 评分为桩，且北极星/护栏/证据分类等支撑度量缺失，无法产出可信净收益与 DecisionRecord。

> 此外，PRD §8 北极星与护栏作为产品级目标无实现；技术规格 §25 自述「P0 全链路通过」「P1 R1–R8 完成」与代码事实（见第三节）存在出入，降低了文档可信度。

**判定**：项目目标**未达成可验证交付**。差距集中在「可验证性」与「产品治理度量」两层，而非基础功能缺失。

---

## 三、关键发现：技术规格 §25 自述与实际代码不符

§25「已知非阻塞限制」部分自述经代码核查**不成立**：

| §25 自述 | 代码事实 | 结论 |
|---|---|---|
| Evidence Connector 仍为空实现 | `infrastructure/evidence_sources.py` 中 `AllowlistedHttpEvidenceConnector`（真实，egress 代理+allowlist，fail-closed）+ `GoalIntentEvidenceConnector`（始终可用） | ❌ 不实，已实现 |
| Deployment Provider 为内存实现 | `deployment.py` 生产接线为 `StaticPreviewDeploymentProvider`（真实静态发布）；`InMemoryDeploymentProvider` 仅为 `for testing only` 桩 | ❌ 不实，已实现 |
| ReleaseCandidate 自动批准 | `execution_orchestrator.py:2158` 调 `create_candidate` 后立即 `approve("auto-approved by P1 execution chain")`，跳过人工任务 | ✅ 属实 |
| 完整浏览器级 R7 gate 待 Playwright | `browser_journey.py` 无 Playwright 时 dry-run（所有 step `passed=True`） | ✅ 属实 |

> 影响：前两点误报会降低对文档其余「已完成」声明的信任，建议修订 §25 以贴合代码现状。

---

## 四、测试与 CI 门禁的系统性问题

- **CI 门禁与 PRD 分类脱钩**：`.github/workflows/ci.yml` 仅 `ruff → mypy → pytest`，未分设「状态转换/幂等/迁移/权限拒绝/恢复」独立门禁；`pyproject.toml` 无对应 `markers`。
- **行为验收被 mock 替代**：恢复、幂等、UNKNOWN 对账、Permit 撤销/过期/fencing 等 PRD 强调剧本多用 `AsyncMock` 验证「服务向 mock 会话发了正确调用」，而非真实落库后断言外部结果。
- **存在规范禁止的「结构级」测试**：`test_evidence_chain_integrity.py` 编译 DDL 并断言外键列名字符串出现；迁移测试仅断言文件存在/链链接，未执行 `alembic upgrade/downgrade` —— 正是 PRD §26「禁止用类名/字段存在代替行为验证」所禁。
- **Docker 沙箱未端到端验证**：`test_sandbox.py` 注入假 runner 仅断言命令字符串（`--network none/--read-only/--user 65532/--cap-drop ALL`），不启动容器。

---

## 五、本核查的局限

1. **未执行测试**：环境未安装 pytest 及 PostgreSQL/Alembic 等运行时依赖；本报告为静态代码与文档比对，未实跑验收套件。Agent 静态判断 `CSV_SUMMARY_BASELINE` / `EVT_PARSER_GAP` 在其断言范围内「应能通过」，但未经执行确认。
2. **覆盖率非穷举**：3141 文件量级下采用分面抽样核查，聚焦 PRD 明示的验收点与状态机；未逐文件审计全部 166 模块。
3. **动态组织净收益**等需真实实验的结论，依赖 Eval 结果，本报告未运行实验。

---

## 六、建议（按优先级）

1. **修订 `Regent-Technical-Spec.md` §25**：将 Evidence Connector、Deployment Provider 的误报更正，避免误导验收与投资人/评审判断。
2. **补齐 P0 验收测试对治理管道的覆盖**：让 `CSV_SUMMARY_BASELINE` / `EVT_PARSER_GAP` 的 pytest 真正驱动 `CsvSummaryBaselineService` / `EvtParserGapService`，断言 Goal/Work/Run 形成、状态机终态、Worker 恢复、能力缺口认证。
3. **实现北极星与护栏（PRD §8）**：这是产品级目标，当前零实现，应纳入近期迭代。
4. **Eval Harness 接入真实执行与评分**：否则 P0 完成定义第 5 条永远无法满足，P2-5 自适应组织也无从验证。
5. **清理规范禁止的结构级测试**，并补充 Permit 撤销/fencing/重复领取、Observation 排除、证据五分类的专门行为测试。
6. **对 Q4 溢出项做门禁对齐**：SelfImprovementRun 应补其 P2-8 DecisionRecord 门；Tauri 桌面端若在路线内应补入需求文档，否则标记为探索性。

---

_核查由静态代码+文档比对完成；如需我进一步实跑两套 P0 验收测试或深入某模块，请告知。_

---

## 附录：修复后状态（2026-07-30+）

> 本附录对照上文快照中的关键缺口，记录 DecisionNote 路线落地后的状态；**不改写**上文历史正文。权威细节见 DecisionNote、`docs/registered-unimplemented-2026-07-30.md`、Spec §25。

| 核查缺口（快照时） | 修复后 | 指向 |
|---|---|---|
| CSV / EVT 治理管道未测 | **已修** | `tests/integration/test_csv_summary_governance_path.py`、`tests/integration/test_evt_parser_gap_governance.py` |
| Eval 评分桩（`hash%2`） | **已修**（交付信号/Goal 证据评分）；A/B/C 冻结对照仍待产品 DecisionRecord | `core/src/regent/application/eval_harness_service.py`、`fixtures/eval_single_agent_baseline_v1.json`、`tests/unit/application/test_eval_north_star.py` |
| 北极星 / 护栏（PRD §8） | **已修**（只读报告） | `core/src/regent/application/north_star_metrics.py`、`core/src/regent/api/governance.py`（`/v1/governance/north-star`） |
| 隐私导出/删除（PRD §7.4） | **已修** | `core/src/regent/application/privacy_service.py`、`tests/unit/application/test_privacy_export_delete.py` |
| 证据五分类 + Gate 排除规则 | **已修** | `core/src/regent/application/evidence_policy.py`、`tests/unit/application/test_evidence_trust_classification.py` |
| Permit / 恢复幂等行为测不足 | **已修**（专项行为测） | `tests/unit/application/test_permit_behavior.py`、`tests/unit/application/test_recovery_idempotency.py` |
| SelfImprovement / Hive 过度宣称 | **半落地**（候选/opt-in；门禁未验收） | Spec §25；API `candidate_ungated` / Hive `REGENT_AAR1_CERTIFIED_HIVE` |
| P2-3 Impact Graph、P2-5 HMAC、G0 完整闭环 | **刻意后置** | `docs/registered-unimplemented-2026-07-30.md` |
