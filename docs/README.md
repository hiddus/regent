# Regent 文档索引

## 当前有效基线（2026-07-31）

| 文档 | 状态 | 用途 |
|---|---|---|
| [`../Regent-PRD.md`](../Regent-PRD.md) | CURRENT | 产品定义与需求（含 §4.4 对话式完整交付） |
| [`../Regent-Technical-Spec.md`](../Regent-Technical-Spec.md) | CURRENT | 技术架构与实施规范（含 §13.8 沙箱/两级 Effect） |
| [`definitions/REGENT-DEFINITION-1.0.txt`](definitions/REGENT-DEFINITION-1.0.txt) | FROZEN | **唯一规范定义源** + `.sha256`；CI 防漂移 |
| [`../Regent-Measurement-Decision-Framework.md`](../Regent-Measurement-Decision-Framework.md) | CURRENT | P2-4 Eval / 组织晋测合同 |
| [`../Regent-Plan.md`](../Regent-Plan.md) | ACTIVE | **唯一编码执行清单**与开发切片（含 §14 CD-*） |
| [`conversational-delivery-plan-2026-07-31.md`](conversational-delivery-plan-2026-07-31.md) | ACTIVE（CD-0…5 完成） | 对话式完整交付统一开发计划（吸收双专家评审） |
| [`conversational-delivery-next-plan-2026-07-31.md`](conversational-delivery-next-plan-2026-07-31.md) | **ACTIVE 下一步（重订）** | CD-6…12：N-3 族真执行 → 硬债 → GQ-3/4 → 能力执行；6 周时间盒 |
| [`cd6-execution-plan-2026-07-31.md`](cd6-execution-plan-2026-07-31.md) | **ACTIVE（执行级）** | CD-6 工作包：6.1–6.7；N-3c/N-3d；T1–T6；从属 next-plan §2 |
| [`decision-note-auto-start-journey-2026-07-31.md`](decision-note-auto-start-journey-2026-07-31.md) | ACCEPTED | GoalSpec 快照启动 + 事后纠偏 |
| [`decision-note-prd-44-conversational-delivery-2026-07-31.md`](decision-note-prd-44-conversational-delivery-2026-07-31.md) | ACCEPTED | PRD §4.4 对话式交付编码批准 |
| [`decision-note-gq4-pending-2026-07-31.md`](decision-note-gq4-pending-2026-07-31.md) | PENDING | GQ-4 未晋级；禁止 .env 宣称 |
| [`doc-implementation-alignment-audit-2026-07-31.md`](doc-implementation-alignment-audit-2026-07-31.md) | REVIEW→已复检 | 文档—实现对齐审计；**以 §8 验收复检为准**（F-1…F-9 已闭环；新登记 N-1…N-7 修复引入问题，其中 N-3 阻断生产 agent 执行） |
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

1. PRD §0 永久定义 → §4.4 对话式交付 → §6 Graduation 矩阵
2. `Regent-Plan.md` §14 → **[`conversational-delivery-next-plan-2026-07-31.md`](conversational-delivery-next-plan-2026-07-31.md)**（CD-6…12 重订）
3. 开工 CD-6 时读 [`cd6-execution-plan-2026-07-31.md`](cd6-execution-plan-2026-07-31.md)
4. Technical-Spec §13.8 + 三份附录（实现合同时查阅）
5. **禁止**在 Graduation + 文档 CURRENT 前读 P2-1 并开工 Scheduler
6. **禁止**在 CD-6 全绿（含 N-3c/N-3d）+ CD-7 前开生产 GQ-3 canary；验收不得仅用 `echo ok`
