# Regent P2 平台计划

> 状态：CURRENT（P2 编码门禁已开）  
> 日期：2026-07-23（Owner 升 CURRENT + P2Start）  
> 产品基线：`Regent-PRD.md`（CURRENT）  
> 技术基线：`Regent-Technical-Spec.md`（CURRENT）  
> 测量：`Regent-Measurement-Decision-Framework.md`（CURRENT）  
> 附录：`docs/appendices/*`  
> P2Start：`docs/P2StartDecisionRecord.json`

## 0. 总判（二次复审）

| 审批项 | 结论 |
|---|---|
| 文档升 `CURRENT` | **已批准** |
| 定义保护 CI（现行 `REGENT-DEFINITION-3.0`；本文成稿时为已被取代的 1.0） | **已落地，须保持绿** |
| P2 仅承诺 1/2/4；3/5/6 条件；7–9 候选 | 已写入 PRD §8 |
| `p2-scheduler-01+` | **已批准** |
| P1/G0 ExternalOperation | **可以继续（最小实现已启动）** |

```text
A. 定义保护 → B. 文档收口 → C. G0 ExternalOperation
→ D. 故障注入 G8 → E–G. Graduation → H. CURRENT → I. P2Start → J. Scheduler
```

```text
P2-0  P1 Graduation（含 G0 ExternalOperation）
  ↓
P2-1  多 Goal 调度
  ↓
P2-2  多 Runtime Profile
  ↓
P2-3  长期记忆
  ↓
P2-4  最小 Eval Harness
  ↓
P2-5  自适应执行组织
  ↓
P2-6  Champion/Challenger
  ↓
P2-7  生产发布
  ↓
P2-8  自我改进
  ↓
P2-9  能力生态
```

---

## P2-0：P1 Graduation + G0 Durable Effects

验收：PRD §5 `SYSTEM_GRADUATED` + `PRODUCT_EVIDENCE_GRADUATED`。

### 必须先做（阻塞 G8 / 阻塞 Scheduler）

1. 最小 **ExternalOperation + Permit 原子 CONSUMED + operation_key + dispatch_generation + 对账**（附录 2）。  
2. 崩溃与网络故障注入门禁（G8）：0 重复副作用；UNKNOWN≤15min 进入对账路径。  
3. Provider capability matrix 登记。

### 其余 Graduation 任务

质量门禁；假交互清除；Journey；真实 Evidence；**演进闭环**（拒绝→REVISE，无强制 7 天窗）；凭据；Git/Release；DoD Pack。

建议提交：

- `p1-graduation-00`：**G0 ExternalOperation**（新增，优先）  
- `p1-graduation-01`：质量门禁 + 假交互 + 凭据  
- `p1-graduation-02`：Journey + Evidence + 产品证据窗  
- `p1-graduation-03`：故障注入报告 + GraduationDecisionRecord  

---

## P2-1：多 Goal 调度

前置：G0 + G8 + GraduationDecisionRecord + P2StartDecisionRecord + 文档 CURRENT。  
交付：Queue、多资源原子预留、BudgetLedger+price_book、Aging/公平性、checkpoint、可重放 SchedulingDecision（附录 1 §13）。

---

## P2-2 … P2-9

与 `Regent-PRD.md` §9 一致。P2-5 依赖 P2-4 Eval（Measurement Framework §8）。P2-3 强制 Memory-delayed PI；P2-5 强制 Agent-to-Agent PI。

---

## 编码启动结论

```text
DONE：P2 承诺包最小实现（Scheduler + Runtime + Eval + Memory API）
门禁：P2-5/6 需 Eval DecisionRecord；P2-7–9 仍为候选
```
