# Regent P1 / G0 剩余任务核对清单

> 状态：**SUPERSEDED（历史执行清单，2026-08-11 标注）**  
> 日期：2026-07-22  
> 当时的永久定义：`docs/definitions/REGENT-DEFINITION-1.0.txt`（已被 2.0、再被 3.0 取代）  
>
> ⚠️ 本文是定义 1.0 时期的 P1/G0 核对清单，**不是现行编码执行清单**：
> 现行唯一编码执行清单是 [`../Regent-Plan.md`](../Regent-Plan.md)，现行唯一规范定义源是
> [`definitions/REGENT-DEFINITION-3.0.txt`](definitions/REGENT-DEFINITION-3.0.txt)。
> 下文的"恒定属性"表是 1.0 的 7 条属性，已被 3.0 的 9 条取代；其中"默认单 Agent / 本阶段不扩多 Agent /
> 禁止默认多 Agent"等条目按 3.0 ATTRIBUTE_4/7 只约束**生产默认与现实权限扩大**，不约束沙箱内的候选拓扑与组织试验。  
> 产品/验收：`Regent-PRD.md` §6 Graduation 矩阵  
> 技术合同：`Regent-Technical-Spec.md` + `docs/appendices/*`  
> 平台路线：`docs/p2-platform-plan.md`  
> 测量：`Regent-Measurement-Decision-Framework.md`（P2-4，非本阶段开工）

## 0. 总判（相对当时锁死的定义，已被 3.0 取代）

```text
REGENT-DEFINITION-1.0 = 接收自然语言 Goal，在边界内自治解释与补齐能力，
形成最小人机组织，创建并运营可脱离 Core 的 App，依据可验证外部结果持续调整，
直至成功 / 耗尽 / 失败 / 取消。
```

| 定义恒定属性（1.0，已被 3.0 取代） | 当时实现 | 当时阶段任务 | 当时不得做 |
|---|---|---|---|
| 1 Goal 驱动 | Start→主链已通；GoalSpec 为解释结果 | G1：≥3 未知 Goal | 要求用户先写完整 PRD |
| 2 边界内自治 | Permit/Lease 部分存在 | **G0 ExternalOperation** 原子派发与对账 | 无限权限、无 Permit 副作用 |
| 3 能力可演进 | RESEARCH_MORE→能力 REUSE 已有雏形 | 固化能力缺口→REUSE/CONFIGURE/…；禁 Core 种子 feed | 把 RSS/垂直工具写进 Core |
| 4 组织为手段 | 默认单 Agent | **延后** P2-4 Eval → P2-5；本阶段不扩多 Agent | 默认多 Agent、投票代替验收 |
| 5 独立 App | Preview/Build 主链存在 | G4/G5 可复现构建与 Requirement 驱动 | App 业务吸进 Core |
| 6 外部结果闭环 | Evidence Connector + 冒烟不足 | G2/G3 + **G6/G7 产品证据规模** | 内部 smoke 满足 Gate |
| 7 明确终止 | Gate/Decision 骨架 | 唯一 Decision；不足则 INSUFFICIENT_EVIDENCE | 无限运行、伪 PASSED |

**编码门禁**

| 允许 | 禁止 |
|---|---|
| P1 残留热修 | 无 P2Start 的 Scheduler（已过时） |
| **`p2-scheduler-01+`（P2Start 已签）** | 无 P2-4 的默认多 Agent |
| 故障注入、Journey、产品证据窗 | 用单 Goal/单用户宣称 PRODUCT（已过时） |

**权威准入顺序（已完成至 J）**

```text
A…I 已完成 → J. p2-scheduler-01  ← 当前
```

---

## 1. 已完成（可保留，不再作为当前主攻）

- 主链事件编排骨架（Discovery→…→Preview）与 Confirm/Start 分离；
- 隔离 Docker Build / Static Preview / Permit claim 基础；
- 内部 smoke 不得满足产品 Gate（方向已落地，须持续守住）；
- RESEARCH_MORE → 能力池 `allowlisted-http-source-v1` REUSE → 重发现（能力演进雏形）；
- Dead Letter 查询/重放入口；mypy/Pytest 基线。

**G0 已合入并部署**（`20260722-p1-g0-eo-r34`，迁移 `0024`）。证据包：`docs/graduation-evidence/20260722T073327Z/`。

**当前总判（2026-07-22 Owner 校准后）**

| 层级 | 状态 | 说明 |
|---|---|---|
| SYSTEM | `PASSED_PENDING_PRODUCT_COUNTERSIGN` | 技术齐；待人工会签 |
| PRODUCT | **`PASSED`** | 演进闭环已证；**已废除**强制 7 天窗（见 `PRODUCT_EVOLUTION_GATE.md`） |
| P2Start | `BLOCKED` | 差 SYSTEM 会签 + 文档 CURRENT |
| **P1 编码主线** | **可停手** | 禁止 Scheduler，直至 P2Start |

下一动作：完成 `COUNTERSIGN_CHECKLIST.md` → 文档 CURRENT → `P2StartDecisionRecord` → 再开 Scheduler。  
合并程序：`IMMEDIATE_MERGE_PROCEDURE.md`（不再等 Day+7）。

---

## 2. 立即停止 / 延后

### 2.1 立即停止

- 开工 `p2-scheduler-01` 或任何纯 P2 平台批次；
- 默认多 Agent / 组织 Designer 实现；
- Core 内置产品级 RSS/垂直业务模型；
- API 注入 activation / 伪 Observation 充当 G6/G7；
- 单次 Journey 或单用户宣布产品毕业；
- 修改当时的冻结定义 `REGENT-DEFINITION-1.0` 文案以迁就实现（现行等价约束：不得改写 `REGENT-DEFINITION-3.0`）。

### 2.2 延后（有前置）

| 项 | 前置 |
|---|---|
| P2-1 Scheduler | G0+G8+双层 Graduation+P2Start+文档 CURRENT |
| P2-3 Memory 全闭环 | SYSTEM 毕业后按路线 |
| P2-4 Eval Harness | Measurement Framework；投入顺序上先于自适应组织的**生产晋级** |
| P2-5 自适应组织**扩大现实生产权限 / 晋级生产默认** | P2-4 统计 Gate 正净收益。沙箱内的候选拓扑提出与组织试验按定义 3.0 ATTRIBUTE_2/7 默认开放，不受本前置约束（`Regent-Plan.md` §0.1/§1.1） |
| 多 Runtime / 生产发布 / 自我改进 / 能力市场 | 按 PRD §8 顺序 |

---

## 3. 当前唯一执行批次（按依赖排序）

### `p1-graduation-00` — G0 Durable External Effects（最高优先）

对齐定义属性 **2（边界内自治）** + Graduation **G8 前置**。合同：`docs/appendices/Durable-Execution-and-External-Effects.md`。

**进度（2026-07-22）**：**完成**（代码+部署+G8 单元/Worker 重启证据）。  
残留（不阻塞 SYSTEM 技术推荐通过）：共享生产环境未做 kill-mid-dispatch 混沌；Evidence HTTP 挂 EO 为可选增强。

### `p1-graduation-01` — 系统诚信与发布基线（G9–G11 + 质量）

**进度**：**完成**（G9 扫描 PASS；G10 ruff/pytest G0 绿；G11 发布 `20260722-p1-g0-eo-r34`）。

### `p1-graduation-02` — 目标执行证据链（G1–G5）

**进度**：**完成（证据层）** — ≥3 未知 Goal → PREVIEW + Decision；G3 假设可机器列出（`g3_hypotheses.json`）；G2 含能力 REUSE 审计。

### `p1-graduation-03` — 故障注入与 SYSTEM 签署（G8 + G12）

**进度**：**技术证据完成** — `G8_FAULT_INJECTION_REPORT.json` + `DoD_Evidence_Pack.json`；`SYSTEM_GRADUATED=PASSED_PENDING_PRODUCT_COUNTERSIGN`（待产品会签）。

### `p1-graduation-04` — 产品证据毕业（G6–G7） ← **暂停堆量**

**2026-07-22 更新**：内部员工体验反馈 — 产出与预期差距过大 → `PRODUCT=REVISE_REQUIRED`（见 `PRODUCT_QUALITATIVE_FAIL.md`）。  
**NO-GO**：继续招募刷 G6。**GO**：先开产品质量 REVISE（下方批次）。

### `p1-revise-quality-01` — 演进闭环可演示（当前主攻）

对齐定义 **1/3/6/7**。**本阶段收敛闭环，不收敛「体验已达预期」。**

1. ~~收紧 `goal_requires_external_evidence`；非新闻 Goal 禁止默认 RSS REUSE~~（已合入）
2. ~~`CapabilityResolution` 接真缺口（禁空 hash 捷径）~~（已合入；空缺口=诚实 SATISFIED）
3. ~~Gate：`product_rejection` → FAILED → REVISE API~~（`POST .../product-rejection`）
4. Discovery 假设锁定用户可验证产品形态（后续）
5. Generation acceptance 绑定 GoalSpec `success_criteria`（后续）
6. 用员工拒绝路径演示 ≥1 次 REVISE→新 Discovery；**无强制日历窗**；闭环齐即可签 PRODUCT。

跟踪：`PRODUCT_QUALITATIVE_FAIL.md`；PRD §5.2 已校准为闭环门槛。

---

## 4. Graduation 映射（替代旧「全局 DoD 12 条」口号）

以 PRD §5 矩阵为唯一可证伪验收；下列仅作索引：

| 层级 | 条目 | 批次 |
|---|---|---|
| SYSTEM | G1–G5 | graduation-02 |
| SYSTEM | G8 | graduation-00 → 03 |
| SYSTEM | G9–G11 | graduation-01 |
| SYSTEM | G12 | graduation-03 |
| PRODUCT | G6–G7 | graduation-04 |

旧文件 §4「单 Goal / 单用户」DoD **作废**，以本表与 PRD §5 为准。

---

## 5. 与永久定义的一致性检查（每批合并前）

- [ ] 是否仍只需自然语言 Goal 即可启动（属性 1）？
- [ ] 外部副作用是否都经 Permit+EO 边界（属性 2）？
- [ ] 能力缺口是否走 Resolution 而非写死 Core 工具（属性 3）？
- [ ] 是否把多 Agent 当成必选项（违反属性 4）？
- [ ] 生成物是否可脱离 Core（属性 5）？
- [ ] Gate 是否只用外部可验证结果（属性 6）？
- [ ] 是否有证据化终态而非空转（属性 7）？

任一「否」→ 修正实现或文档解释，**禁止改定义**。

---

## 6. 风险（更新）

| 风险 | 等级 | 处置 |
|---|---|---|
| ExternalOperation 未实现却宣称 G8 | 阻塞 | 先 graduation-00 |
| 产品证据规模不足却毕业 | 阻塞 | 严格执行 G6/G7 |
| 执行清单与旧 R1–R11 双源 | 高 | 以本文+PRD v2 为准 |
| 提前 Scheduler | 阻塞 | 无 P2StartDecisionRecord 不开工 |
| 定义被阶段文案替换 | 阻塞 | 只引用当时的 REGENT-DEFINITION-1.0（现行：只引用 REGENT-DEFINITION-3.0） |

---

## 7. 编码启动结论

```text
GO：SYSTEM 人工会签 → 文档 CURRENT → P2StartDecisionRecord
NO-GO：无 P2Start 却开工 Scheduler
NO-GO：再用「等 N 天」阻塞 PRODUCT（已废除）
```

**P1 关门剩余**：SYSTEM 会签 + CURRENT + P2Start（PRODUCT 已按演进门槛 PASSED）。
