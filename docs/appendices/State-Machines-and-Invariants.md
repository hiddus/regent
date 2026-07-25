# 附录 1：State Machines and Invariants

> 状态：CURRENT  
> 配套：`Regent-Technical-Spec-v2.md` §5  
> 日期：2026-07-22（二次复审：补全实现级表）

## 1. 通用规则

每个控制对象冻结：`state, command, guard, resulting state, emitted event, terminal?, retry, timeout/cancel/unknown`。

持久化：`version` + Check Constraint + 条件更新；终态不原位重开；非法转换写 `failure_code`。

---

## 2. Goal

状态：`DRAFT | READY | ACTIVE | PAUSED | WAITING_HUMAN | BLOCKED | ACHIEVED | EXHAUSTED | FAILED | CANCELLED`

| command | from | guard | to | event | terminal? | retry |
|---|---|---|---|---|---|---|
| QUALIFY | DRAFT | Spec 可生成 | READY | GoalStateChanged | no | yes |
| FREEZE_SPEC | READY | Spec 内容合法 | READY | GoalSpecFrozen | no | no（修订新 Spec） |
| START | READY | Spec FROZEN | ACTIVE | GoalExecutionRequested | no | idempotent key |
| PAUSE | ACTIVE | Operator | PAUSED | GoalStateChanged | no | — |
| RESUME | PAUSED | Operator | ACTIVE | GoalStateChanged | no | — |
| REQUEST_HUMAN | ACTIVE | HumanTask 创建 | WAITING_HUMAN | GoalStateChanged | no | — |
| HUMAN_RESOLVED | WAITING_HUMAN | Task 完成 | ACTIVE/BLOCKED | GoalStateChanged | no | — |
| MARK_BLOCKED | ACTIVE | 预算/能力不足 | BLOCKED | GoalStateChanged | no | after unblock |
| CANCEL | 非终态 | Operator | CANCELLED | GoalStateChanged | yes | no |
| COMPLETE | ACTIVE | Decision 规则 | ACHIEVED/EXHAUSTED/FAILED | GoalStateChanged | yes | no |

超时：WAITING_HUMAN 超时 → BLOCKED 或按策略 CANCELLED。  
UNKNOWN：挂在 ExternalOperation，Goal 保持 ACTIVE 或 WAITING_HUMAN。

---

## 3. Work

状态：`PLANNED | READY | RUNNING | EVALUATING | ACCEPTED | REJECTED | WAITING_HUMAN | BLOCKED | UNKNOWN | CANCELLED`

| command | from | guard | to | event | terminal? |
|---|---|---|---|---|---|
| PLAN | — | Goal ACTIVE | PLANNED | WorkStateChanged | no |
| READY | PLANNED | 依赖满足 | READY | WorkStateChanged | no |
| START_RUN | READY | 可创建 Run | RUNNING | RunRequested | no |
| EVALUATE | RUNNING | Run EXECUTED | EVALUATING | WorkStateChanged | no |
| ACCEPT | EVALUATING | 独立验收通过 | ACCEPTED | WorkStateChanged | yes |
| REJECT | EVALUATING | 验收失败 | REJECTED | WorkStateChanged | yes |
| BLOCK | * | 能力/预算 | BLOCKED | WorkStateChanged | no |
| CANCEL | 非终态 | Operator | CANCELLED | WorkStateChanged | yes |

EXECUTED Run ≠ ACCEPTED Work。重试：新 Run，不重开终态 Work。

---

## 4. Run

状态：`REQUESTED | RUNNING | EXECUTED | FAILED | TIMED_OUT | CANCELLED`

| command | from | guard | to | event | terminal? | retry |
|---|---|---|---|---|---|---|
| REQUEST | — | Work READY | REQUESTED | RunStateChanged | no | idempotent |
| START | REQUESTED | Lease | RUNNING | RunStateChanged | no | — |
| SUCCEED | RUNNING | 产出校验 | EXECUTED | RunStateChanged | yes* | 新 Run |
| FAIL | RUNNING | 明确失败 | FAILED | RunStateChanged | yes | 新 Run |
| TIMEOUT | RUNNING | 超时策略 | TIMED_OUT | RunStateChanged | yes | 新 Run |
| CANCEL | REQUESTED/RUNNING | 未 CONSUMED 外部派发 | CANCELLED | RunStateChanged | yes | — |

\* EXECUTED 对 Run 终态，但对 Work 仍需 EVALUATING。  
若已 ExternalOperation CONSUMED：取消不得假装无副作用，走对账/补偿。

---

## 5. DiscoveryRound

状态：`REQUESTED | RESEARCHING | READY | DECIDED | BLOCKED | FAILED | EXHAUSTED`

| command | from | guard | to | event | terminal? |
|---|---|---|---|---|---|
| REQUEST | — | Goal ACTIVE；幂等键 | REQUESTED | DiscoveryRoundRequested | no |
| BEGIN_RESEARCH | REQUESTED | — | RESEARCHING | DiscoveryRoundChanged | no |
| COMPLETE_RESEARCH | RESEARCHING | Evidence 写入 | READY | DiscoveryRoundChanged | no |
| DECIDE | READY | 决策策略 | DECIDED | DiscoveryCompleted | yes |
| BLOCK | * | 缺口不可恢复 | BLOCKED | DiscoveryRoundChanged | yes |
| FAIL | * | 系统错误 | FAILED | DiscoveryRoundChanged | yes |
| EXHAUST | * | 预算用尽 | EXHAUSTED | DiscoveryRoundChanged | yes |

RESEARCH_MORE：DECIDED 载荷；触发能力恢复或人工，常启动**新** Round（新幂等键），不重开旧 Round。

---

## 6. AppBuild

状态：`REQUESTED | RUNNING | PASSED | FAILED | CANCELLED`

| command | from | guard | to | event | terminal? |
|---|---|---|---|---|---|
| REQUEST | — | WorkspaceSnapshot 存在 | REQUESTED | AppBuildRequested | no |
| START | REQUESTED | Sandbox/Permit | RUNNING | AppBuildChanged | no |
| PASS | RUNNING | VerificationReport 完整 | PASSED | AppBuildPassed | yes |
| FAIL | RUNNING | 报告失败 | FAILED | AppBuildChanged | yes |
| CANCEL | REQUESTED/RUNNING | 无不可逆外部或已对账 | CANCELLED | AppBuildChanged | yes |

重试：新 Build 行。仅 PASSED 可建 ReleaseCandidate。

---

## 7. Deployment

状态：`REQUESTED | PROGRESSING | SUCCEEDED | FAILED | UNKNOWN | ROLLING_BACK | ROLLED_BACK`

| command | from | guard | to | event | notes |
|---|---|---|---|---|---|
| REQUEST | — | PASSED Build；Permit | REQUESTED | PreviewDeploymentRequested | + ExternalOperation PREPARED |
| DISPATCH | REQUESTED | 同事务 CONSUMED | PROGRESSING | — | EO→DISPATCHING |
| SUCCEED | PROGRESSING | Provider OK | SUCCEEDED | PreviewDeploymentSucceeded | |
| FAIL | PROGRESSING | 终态失败 | FAILED | — | |
| LOSE_RESPONSE | PROGRESSING | 超时 | UNKNOWN | — | EO UNKNOWN |
| RECONCILE | UNKNOWN | query | SUCCEEDED/FAILED/MANUAL | — | |
| ROLLBACK | SUCCEEDED | 新 Permit+新 EO | ROLLING_BACK→ROLLED_BACK | — | 新操作 |

---

## 8. GateEvaluation / IterationDecision

Gate 状态结果：`PASSED | FAILED | INSUFFICIENT_EVIDENCE`（评价结果，非长生命周期机）。

| command | guard | result | event |
|---|---|---|---|
| EVALUATE | 绑定指标+Observation 集 | 三选一 | GateEvaluationRequested/Recorded |
| DECIDE | 同一 Gate 仅一次 | CONTINUE/REVISE/STOP | IterationDecisionRecorded |

样本不足 → 必须 `INSUFFICIENT_EVIDENCE`，禁止 PASSED。内部 smoke 不得驱动 PASSED。

---

## 9. Permit

```text
REQUESTED → GRANTED → CLAIMED → CONSUMED
REQUESTED → DENIED
GRANTED → EXPIRED | REVOKED
CLAIMED → EXPIRED | REVOKED
CLAIMED → CONSUMED  （仅与 EO PREPARED→DISPATCHING 同事务）
```

CONSUMED = 唯一派发权已持久化（见附录 2）。1:1 ExternalOperation。

---

## 10. ExternalOperation

见附录 2。摘要转换：

| from | command | to | guard |
|---|---|---|---|
| PREPARED | BEGIN_DISPATCH | DISPATCHING | 同事务 Permit CONSUMED |
| DISPATCHING | CONFIRM | SUCCEEDED | Provider OK |
| DISPATCHING | TERMINAL_FAIL | FAILED_TERMINAL | Provider 明确失败 |
| DISPATCHING | LOSE | UNKNOWN | 超时/丢响应 |
| UNKNOWN | RECONCILE | RECONCILING | 具备 query |
| RECONCILING | RESOLVE | SUCCEEDED/FAILED_TERMINAL/MANUAL_REVIEW | query 结果 |

---

## 11. ResourceReservation（P2-1 合同，实现级）

状态：`REQUESTED | HELD | RELEASED | PREEMPTED | EXPIRED | FAILED`

| command | from | to | guard |
|---|---|---|---|
| RESERVE | — | HELD | 多资源原子预留成功（见 §13） |
| RELEASE | HELD | RELEASED | 正常归还 |
| PREEMPT | HELD | PREEMPTED | 仅安全恢复阶段；已 DISPATCHING EO 不可假定取消 |
| EXPIRE | HELD | EXPIRED | TTL |
| FAIL | REQUESTED | FAILED | 配额不足 |

资源守恒：Σ(HELD) ≤ Quota(org, resource, price_book_version)。

---

## 12. MemoryRecord / EvalRun

Memory：`CANDIDATE | VERIFIED | SUPERSEDED | REVOKED | EXPIRED`；撤销→Impact Graph Revalidation（下游 Decision/候选标 `REVALIDATION_REQUIRED`）。

EvalRun：`DRAFT | FROZEN | RUNNING | SCORED | DECIDED | INVALIDATED`；FROZEN 后不可改任务集/指标/排除规则。

---

## 13. Scheduler 实现级合同（P2-1，文档先冻结）

### 13.1 对象

| 对象 | 职责 |
|---|---|
| ExecutionQueue | 可调度 Goal/Work 条目；优先级、入队时间、aging_score |
| ResourceReservation | 多资源原子预留 |
| BudgetLedger | 借记/贷记；绑定 `price_book_version` |
| GoalPriorityPolicy | 优先级与公平性参数 |
| PreemptionRecord | 抢占审计 |
| SchedulingDecision | 可重放输入快照 + 决策输出 |

### 13.2 多资源原子预留

单事务内锁定所需资源行（CPU/内存/Token/外部调用配额）；任一不足 → 全部失败，Queue 条目保持可调度。

### 13.3 Aging、公平性、优先级反转

- `aging_score = base_priority + f(wait_time)`，参数入 Policy 版本；
- 防饥饿：低于阈值的低优先级条目在 T_age 后提升；
- 优先级反转：持有 HELD 资源的低优先级任务完成或被安全抢占前，高优先级可等待并记录。

### 13.4 Checkpoint / Resume

- 仅允许在无 DISPATCHING/UNKNOWN ExternalOperation 的阶段 checkpoint；
- Resume 加载 SchedulingDecision 输入快照；确定性边界：同一快照+同一 Policy 版本 → 同一决策（并列用稳定排序键打破）。

### 13.5 可重放调度输入

SchedulingDecision 必须保存：队列快照哈希、配额快照、Policy 版本、price_book_version、随机种子（若有）、决策时间。重放用于审计，不自动再执行副作用。

## 14. 签署检查表

- [x] Goal / Work / Run / Discovery / Build / Deployment / Gate / Permit / EO 命令表已具备
- [x] Scheduler 实现级合同（Queue、原子预留、Aging、checkpoint、可重放）已写入
- [ ] 与 ORM Check Constraint 一一对照的实现 PR（G0/P2-1）
- [ ] 三方复审升 CURRENT
