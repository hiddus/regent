# DecisionNote: 混合方案 H0 — 可控单脑控制面

**日期**：2026-08-03  
**状态**：ACCEPTED — 开工落地  
**方案**：[`execution-plan-hybrid-control-experience-ops-2026-08-03.md`](execution-plan-hybrid-control-experience-ops-2026-08-03.md)

---

## 0. 决策

H0 焊死 Primary Agent 控制面，并预埋 H1/H2 契约：

| ID | 内容 |
|----|------|
| H0-1 | 用户 Abort → 取消当前 lease 协作停 → `exit_kind=STOP` + 草稿 |
| H0-2 | 危险工具可 Permission 阻塞（once / session-always / deny） |
| H0-3 | `ask_user_question` 工具 → `ASK_HUMAN` |
| H0-4 | COMPLETE `result_bundle` 对外 API + 控制台结果卡 |
| H0-5 | 子 Agent 深度 ≤1、可随 Abort 停、预算继承父帽 |
| H0-6 | `RegentEvent` schema + 关键路径写入 goal 环形缓冲 |
| H0-7 | `execution_mode ∈ {ask,act}` 预埋（默认 ask） |

不变量继承方案 H-A…H-E。Act ≠ 跨 Goal 永久旁路。

## 1. 拍板默认（Q1–Q5）

- 新 Goal 默认 **ask**
- H0 落 RegentEvent 缓冲（UI 可简陋）
- H2 时间线只读（本期不做）
- Hive 不启用
- Act：同 Session 续跑可免 plan_approve；删除/外发/重规划仍 ask
