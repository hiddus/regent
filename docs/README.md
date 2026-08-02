# Regent 文档索引

## 当前有效基线（2026-08-01）

| 文档 | 状态 | 用途 |
|---|---|---|
| [`../Regent-PRD.md`](../Regent-PRD.md) | CURRENT | 产品定义与需求（含 §4.3 人工边界、§10.5 M6 canary） |
| [`../Regent-Technical-Spec.md`](../Regent-Technical-Spec.md) | CURRENT | 技术架构（含 §13.7.3 M6 窗、§13.8.3 软暂停、§18.6 prompt-cache） |
| [`definitions/REGENT-DEFINITION-1.0.txt`](definitions/REGENT-DEFINITION-1.0.txt) | FROZEN | **唯一规范定义源** + `.sha256`；CI 防漂移 |
| [`direction-note-run-think-learn-2026-08-02.md`](direction-note-run-think-learn-2026-08-02.md) | **DIRECTION** | 边跑边干边想；模型主理、人辅助决断；无退出门；经验吸收优先于怕浪费 |
| [`execution-plan-run-think-learn-2026-08-02.md`](execution-plan-run-think-learn-2026-08-02.md) | **ACTIVE** | 产品×技术逻辑执行方案：L0–L5 人步切片（方案可见→选项→lessons→cache 闭环→稳态） |
| [`decision-note-delivery-machine-invariants-2026-08-02.md`](decision-note-delivery-machine-invariants-2026-08-02.md) | **DRAFT** | 交付不变量草案（待按方向注记修订）；ProgressEvent/activity 标 TRANSITIONAL |
| [`../Regent-Measurement-Decision-Framework.md`](../Regent-Measurement-Decision-Framework.md) | CURRENT | P2-4 Eval / 组织晋测合同 |
| [`../Regent-Plan.md`](../Regent-Plan.md) | ACTIVE | **唯一编码执行清单**与开发切片（含 §14 CD-*） |
| [`m6-canary-watch-plan-2026-08-01.md`](m6-canary-watch-plan-2026-08-01.md) | **ACTIVE 下一步** | M6 5% canary 观察窗：切片指标 → 闭环 → 窗末决策 |
| [`m6-canary-window-2026-08-01.json`](m6-canary-window-2026-08-01.json) | RECORD | 生产开窗配置与回滚约定 |
| [`token-cost-cache-fix-plan-2026-08-01.md`](token-cost-cache-fix-plan-2026-08-01.md) | IMPLEMENTED | Prompt-cache / token 成本修复（P0/P1 已合入） |
| [`agent-core-restoration-executable-plan-2026-08-01.md`](agent-core-restoration-executable-plan-2026-08-01.md) | ACTIVE | Agent 内核 M0–M6 可执行计划 |
| [`conversational-delivery-plan-2026-07-31.md`](conversational-delivery-plan-2026-07-31.md) | ACTIVE（CD-0…5 完成） | 对话式完整交付统一开发计划 |
| [`conversational-delivery-next-plan-2026-07-31.md`](conversational-delivery-next-plan-2026-07-31.md) | 参照 | CD-6…12；当前生产状态以 M6 窗为准 |
| [`cd6-execution-plan-2026-07-31.md`](cd6-execution-plan-2026-07-31.md) | 参照 | CD-6 工作包 |
| [`decision-note-auto-start-journey-2026-07-31.md`](decision-note-auto-start-journey-2026-07-31.md) | ACCEPTED | GoalSpec 快照启动 + 事后纠偏 |
| [`decision-note-prd-44-conversational-delivery-2026-07-31.md`](decision-note-prd-44-conversational-delivery-2026-07-31.md) | ACCEPTED | PRD §4.4 对话式交付编码批准 |
| [`decision-note-gq4-pending-2026-07-31.md`](decision-note-gq4-pending-2026-07-31.md) | PENDING | GQ-4 未晋级；禁止 .env 宣称默认 agentic |
| [`doc-implementation-alignment-audit-2026-07-31.md`](doc-implementation-alignment-audit-2026-07-31.md) | REVIEW→已复检 | 文档—实现对齐审计 |
| [`p1-remaining-coding-plan.md`](p1-remaining-coding-plan.md) | ACTIVE | P1 剩余编码计划（G0→Graduation） |
| [`p2-platform-plan.md`](p2-platform-plan.md) | CONDITIONAL | P2 路线；编码门禁关闭 |
| [`appendices/`](appendices/) | CURRENT | 状态机 / Durable Effects / 安全租户 |

永久定义只存在于 `docs/definitions/REGENT-DEFINITION-1.0.txt`，后续文档只能引用，不得改写。

## 2026-07-31 决策记录与评审（补齐登记）

| 文档 | 类型 | 说明 |
|---|---|---|
| [`decision-note-console-dialog-2026-07-31.md`](decision-note-console-dialog-2026-07-31.md) | DecisionNote | 控制台对话框：规则透明 + 超时默认 |
| [`decision-note-multiagent-supplement-2026-07-31.md`](decision-note-multiagent-supplement-2026-07-31.md) | DecisionNote | Multi-Agent 补充 MA-0…MA-6 |
| [`decision-note-dead-weight-trim-2026-07-31.md`](decision-note-dead-weight-trim-2026-07-31.md) | DecisionNote | 已剪生成路径两处死重 |
| [`console-dialog-prd-2026-07-31.md`](console-dialog-prd-2026-07-31.md) | 输入 PRD | 已被 PRD §4.3 / §4.4 吸收 |
| [`console-dialog-plan-2026-07-31.md`](console-dialog-plan-2026-07-31.md) | 输入计划 | 已被 `conversational-delivery-plan` 吸收 |
| [`conversational-delivery-architecture-review-2026-07-31.md`](conversational-delivery-architecture-review-2026-07-31.md) | REVIEW | 双专家架构核对；**以 §9 修正为准**，不单独作编码基线 |
| [`delivery-state-machine-2026-07-31.md`](delivery-state-machine-2026-07-31.md) | 设计说明 | 交付状态机（对应 `application/delivery_state.py`） |
| [`gq34-promotion-control-flow-2026-07-31.md`](gq34-promotion-control-flow-2026-07-31.md) | 设计说明 | GQ-3 / GQ-4 转正控制流 |
| [`gq0-baseline-report-2026-07-31.md`](gq0-baseline-report-2026-07-31.md) | 报告 | GQ-0 现状基线 |
| [`review-gq-implementation-2026-07-31.md`](review-gq-implementation-2026-07-31.md) | REVIEW | GQ-0…GQ-2 实现复核 |
| [`review-multiagent-sync-2026-07-31.md`](review-multiagent-sync-2026-07-31.md) | REVIEW | Multi-Agent 同步评审 |
| [`review-multiagent-completeness-2026-07-31.md`](review-multiagent-completeness-2026-07-31.md) | REVIEW | Multi-Agent 补丁完整性检验 |
| [`diagnosis-output-quality-2026-07-31.md`](diagnosis-output-quality-2026-07-31.md) | 诊断 | 输出质量诊断 v2 |

> 子目录索引：[`contracts/`](contracts/)、[`adr/`](adr/)、[`appendices/`](appendices/)、[`definitions/`](definitions/)、[`experiments/`](experiments/)、[`graduation-evidence/`](graduation-evidence/)、[`archive/`](archive/)。

## 历史参考（不得作为新编码输入）

| 文档 | 说明 |
|---|---|
| `archive/Regent-PRD-v2.md` | 合并前 PRD v2（已并入 `Regent-PRD.md`） |
| `archive/Regent-Technical-Spec-v2.md` | 合并前技术规范 v2 |
| `archive/Regent-Architecture-v3.md` | 合并前架构 v3 |
| `archive/Regent-Definition-v3.md` | 合并前定义 v3 |
| `archive/Regent-AAR1-*.md` | AAR-1 Foundation 历史文档 |
| `p1-core-capability-requirements.md` | 历史 P1 需求 |
| `p1-core-final-technical-spec.md` | 历史技术规范；以 Technical-Spec 为准 |
| `contracts/*.md` / `adr/*` | 仍可能局部有效；与 Technical-Spec 冲突时以后者为准 |

## 阅读与开工顺序

1. PRD §0 永久定义 → §4.3 人工边界 / §4.4 对话式交付 → §10.5 M6 / §6 Graduation 矩阵
1b. **运作方向** → **执行方案**：[`direction-note-run-think-learn-2026-08-02.md`](direction-note-run-think-learn-2026-08-02.md) → [`execution-plan-run-think-learn-2026-08-02.md`](execution-plan-run-think-learn-2026-08-02.md)
2. **当前 ACTIVE**：[`m6-canary-watch-plan-2026-08-01.md`](m6-canary-watch-plan-2026-08-01.md)（观察窗）；参考 [`agent-core-restoration-executable-plan-2026-08-01.md`](agent-core-restoration-executable-plan-2026-08-01.md)
3. Technical-Spec §13.7.3 / §13.8.3 / §18.6 + 三份附录（实现合同时查阅）
4. CD-6…12 / `Regent-Plan.md` §14 仍为工程参照，但生产 canary 状态以 M6 窗记录为准
5. **禁止**在 Graduation + 文档 CURRENT 前读 P2-1 并开工 Scheduler
6. **禁止**将小比例 M6 canary 宣称成 GQ-4；扩 10% / 默认 agentic 须过观察护栏 + DecisionRecord
