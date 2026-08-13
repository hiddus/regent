# AAR-1 权威文档状态与优先级

> 状态：**SUPERSEDED（历史索引，2026-08-11 标注）**  
> 日期：2026-07-27  
> 目的：消除旧索引中的状态漂移；不修改冻结定义
>
> ⚠️ **本表记录的是 AAR-1 时期（定义 1.0）的文档权威关系，已不是现行基线**：
> 现行唯一规范定义源是 [`definitions/REGENT-DEFINITION-3.0.txt`](definitions/REGENT-DEFINITION-3.0.txt)（见 [`definitions/README.md`](definitions/README.md)）；
> 下表中三份 `Regent-AAR1-*` 文档已移入 [`archive/`](archive/)，按 [`README.md`](README.md) §历史参考不得作为新编码输入；
> 现行文档入口见 [`README.md`](README.md)。

## 当时状态（AAR-1 时期，仅作历史）

| 文档 | 状态 | 权限 |
|---|---|---|
| `definitions/REGENT-DEFINITION-1.0.txt` | FROZEN（当时） | 当时的唯一规范定义源；已被 2.0、再被 3.0 取代 |
| `Regent-PRD.md` | CURRENT | 通用产品基线 |
| `Regent-AAR1-PRD.md` | CURRENT | AAR-1 范围产品权威 |
| `Regent-Technical-Spec.md` | CURRENT | 通用技术基线 |
| `Regent-AAR1-Technical-Spec.md` | CURRENT / CODING-READY | AAR-1 范围技术权威 |
| `Regent-Measurement-Decision-Framework.md` | CURRENT | 通用 P2-4 测量基线 |
| `Regent-AAR1-Measurement-Addendum.md` | CURRENT | AAR-1 统计和 Rollout Gate |
| `AAR1CodingReadinessDecisionRecord.json` | PASSED | Foundation BUILD_ALLOWED；Rollout 关闭 |
| `AAR1-Coding-Plan.md` | ACTIVE | AAR-1 唯一编码执行顺序 |
| `AAR1MilestoneDecisionRecord.json` | M1–M6 PASSED | Foundation Contract 完成；Rollout 仍关闭 |

## 当时的优先级（已被 3.0 取代）

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
- `ROLLOUT_NOT_ALLOWED`：**仅指生产 rollout 门禁**——强单 Agent 仍是生产默认 Champion，自适应多 Agent / 自由拓扑不得自动继承不可逆生产权限。按定义 3.0 ATTRIBUTE_4/7，沙箱内的候选拓扑与组织试验默认开放，本门禁不构成对探索、角色创造或沙箱组织演化的禁止（当时的读法把它当探索禁令，已被 3.0 修订，见 `Regent-Plan.md` §1.1）。
- **认证固定蜂巢（opt-in）**：设置 `REGENT_AAR1_CERTIFIED_HIVE=true` 后，在能力 C/V/R 满足时优先选用已认证模板 `pm-dev-independent-qa-v1`（Durable AgentTask PM→Dev→独立 QA）。生产服务器已启用该 opt-in；本地示例默认仍关。这不是自适应自由拓扑，也不改变默认 Champion。
- `ROLLOUT_ALLOWED`：未来仅由满足两份测量合同的 P2-4 Eval DecisionRecord 激活。
- M6 说明：Coding Plan 仅列到 M5；用户授权的 M6 映射为 Technical Spec Contract 中的「移除旧内存 Task store / 旧状态适配」。

