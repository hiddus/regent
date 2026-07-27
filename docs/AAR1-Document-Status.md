# AAR-1 权威文档状态与优先级

> 状态：CURRENT  
> 日期：2026-07-27  
> 目的：消除旧索引中的状态漂移；不修改冻结定义

## 当前状态

| 文档 | 状态 | 权限 |
|---|---|---|
| `definitions/REGENT-DEFINITION-1.0.txt` | FROZEN | 唯一规范定义源，不可原地修改 |
| `Regent-PRD-v2.md` | CURRENT | 通用产品基线 |
| `Regent-AAR1-PRD.md` | CURRENT | AAR-1 范围产品权威 |
| `Regent-Technical-Spec-v2.md` | CURRENT | 通用技术基线 |
| `Regent-AAR1-Technical-Spec.md` | CURRENT / CODING-READY | AAR-1 范围技术权威 |
| `Regent-Measurement-Decision-Framework.md` | CURRENT | 通用 P2-4 测量基线 |
| `Regent-AAR1-Measurement-Addendum.md` | CURRENT | AAR-1 统计和 Rollout Gate |
| `AAR1CodingReadinessDecisionRecord.json` | PASSED | Foundation BUILD_ALLOWED；Rollout 关闭 |
| `AAR1-Coding-Plan.md` | ACTIVE | AAR-1 唯一编码执行顺序 |
| `AAR1MilestoneDecisionRecord.json` | M1–M6 PASSED | Foundation Contract 完成；Rollout 仍关闭 |

## 优先级

```text
REGENT-DEFINITION-1.0
→ AAR-1 PRD（AAR-1 范围）/ PRD v2（通用范围）
→ AAR-1 Technical Spec / Technical Spec v2
→ AAR-1 Measurement Addendum / Measurement Framework
→ AAR1 Coding Readiness DecisionRecord
→ AAR1 Coding Plan
```

如旧 `docs/README.md`、旧计划或归档文档仍显示 `CONDITIONAL`、P2 编码关闭或不同入口，以本状态表和新的 DecisionRecord 为准。任何新冲突必须通过 ADR/DecisionRecord 解决，不能在代码中隐式选择。

## 门禁解释

- `BUILD_ALLOWED`：授权 Foundation M1–M6（含 M5 Contract 与 M6 内存路径清除）按 Gate 实施。
- `ROLLOUT_NOT_ALLOWED`：强单 Agent 仍是默认 Champion；自适应多 Agent / 自由拓扑未授权。
- `ROLLOUT_ALLOWED`：未来仅由满足两份测量合同的 P2-4 Eval DecisionRecord 激活。
- M6 说明：Coding Plan 仅列到 M5；用户授权的 M6 映射为 Technical Spec Contract 中的「移除旧内存 Task store / 旧状态适配」。

