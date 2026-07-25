# Regent 文档索引

## 当前有效基线（2026-07-22）

| 文档 | 状态 | 用途 |
|---|---|---|
| [`../Regent-PRD-v2.md`](../Regent-PRD-v2.md) | CONDITIONAL | 产品权威候选；永久定义见 §1.1 |
| [`definitions/REGENT-DEFINITION-1.0.txt`](definitions/REGENT-DEFINITION-1.0.txt) | FROZEN | **唯一规范定义源** + `.sha256`；CI 防漂移 |
| [`../Regent-Technical-Spec-v2.md`](../Regent-Technical-Spec-v2.md) | CONDITIONAL | 技术权威候选（只引用定义，不重写） |
| [`../Regent-Measurement-Decision-Framework.md`](../Regent-Measurement-Decision-Framework.md) | CONDITIONAL | P2-4 Eval / 组织晋测合同 |
| [`p1-remaining-coding-plan.md`](p1-remaining-coding-plan.md) | ACTIVE | **唯一编码执行清单**（G0→Graduation） |
| [`p2-platform-plan.md`](p2-platform-plan.md) | CONDITIONAL | P2 路线；编码门禁关闭 |
| [`appendices/`](appendices/) | CONDITIONAL | 状态机 / Durable Effects / 安全租户 |

永久定义只存在于 PRD v2 §1.1，后续文档只能引用，不得改写。

## 历史参考（不得作为新编码输入）

| 文档 | 说明 |
|---|---|
| `Regent-PRD.md` / 旧 Plan | 已被 v2 取代 |
| `p1-core-capability-requirements.md` | 历史 P1 需求 |
| `p1-core-final-technical-spec.md` | 历史技术规范；以 Tech Spec v2 为准 |
| `p1-ai-practitioner-validation-contract.md` | 验证合同参考 |
| `contracts/*.md` / `adr/*` | 仍可能局部有效；与 v2 冲突时以 v2 + 附录为准 |
| `archive/` | 仅追溯 |

## 阅读与开工顺序

1. PRD v2 §1.1 永久定义 → §5 Graduation 矩阵  
2. `p1-remaining-coding-plan.md`（下一入口：`p1-graduation-00`）  
3. Tech Spec v2 + 三份附录（实现合同时查阅）  
4. **禁止**在 Graduation + 文档 CURRENT 前读 P2-1 并开工 Scheduler  
