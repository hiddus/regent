# Regent 文档索引

## 当前有效基线（2026-07-30）

| 文档 | 状态 | 用途 |
|---|---|---|
| [`../Regent-PRD.md`](../Regent-PRD.md) | CURRENT | 产品定义与需求（合并自 Definition-v3 + PRD-v2） |
| [`../Regent-Technical-Spec.md`](../Regent-Technical-Spec.md) | CURRENT | 技术架构与实施规范（合并自 TechSpec-v2 + Architecture-v3） |
| [`definitions/REGENT-DEFINITION-1.0.txt`](definitions/REGENT-DEFINITION-1.0.txt) | FROZEN | **唯一规范定义源** + `.sha256`；CI 防漂移 |
| [`../Regent-Measurement-Decision-Framework.md`](../Regent-Measurement-Decision-Framework.md) | CURRENT | P2-4 Eval / 组织晋测合同 |
| [`../Regent-Plan.md`](../Regent-Plan.md) | ACTIVE | **唯一编码执行清单**与开发切片 |
| [`p1-remaining-coding-plan.md`](p1-remaining-coding-plan.md) | ACTIVE | P1 剩余编码计划（G0→Graduation） |
| [`p2-platform-plan.md`](p2-platform-plan.md) | CONDITIONAL | P2 路线；编码门禁关闭 |
| [`appendices/`](appendices/) | CURRENT | 状态机 / Durable Effects / 安全租户 |

永久定义只存在于 `docs/definitions/REGENT-DEFINITION-1.0.txt`，后续文档只能引用，不得改写。

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

1. PRD §0 永久定义 → §6 Graduation 矩阵
2. `Regent-Plan.md` 开发切片与实现进度
3. Technical-Spec + 三份附录（实现合同时查阅）
4. **禁止**在 Graduation + 文档 CURRENT 前读 P2-1 并开工 Scheduler
