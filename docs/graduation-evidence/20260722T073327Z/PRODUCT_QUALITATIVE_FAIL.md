# 内部员工体验 — 定性产品失败记录

> 记录时间：2026-07-22  
> 证据窗：`20260722T073327Z`  
> 结论：**PRODUCT 方向性失败信号**（非「用户不够」）

## 反馈摘要（产品 Owner 口述）

- 已招募多名**内部员工**体验当前 Preview / 产出。
- 一致认为：**产出内容不是他们想要的**；与预期差距过大。
- 解释：属真实用户侧定性拒绝，应按 PRD「Preview 拒绝 → REVISE 或 STOP」处理，**不得**继续用自动化 Journey / 单次点击 CONTINUE 推进 PRODUCT 毕业。

## Graduation 影响

| 项 | 原状态 | 更新后 |
|---|---|---|
| G6 招募 | 缺用户 | 有用户，但 **Journey 定性失败**（FAIL_JOURNEY 方向） |
| G7 | INSUFFICIENT | 负面定性已存在；Gate 管道未把「不满意」映射为 REVISE/STOP |
| PRODUCT_EVIDENCE_GRADUATED | INSUFFICIENT_EVIDENCE | **HOLD / REVISE_REQUIRED**（禁止假装接近 PASSED） |
| P1 结束 | 日历阻塞 | 额外：**产品质量阻塞**（优先级更高） |
| P2Start | BLOCKED | 仍 BLOCKED；在 REVISE 闭环落地前不得开工 Scheduler |

## 系统根因（代码证据，按优先级）

1. **外部证据启发式过宽 + 默认 RSS REUSE**：含「摘要」等词即强制 RESEARCH_MORE，并把 TechCrunch/HN 等 feed 绑到无关 Goal（例：周报粘贴工具）。
2. **CapabilityResolution 空壳 SATISFIED**：能力缺口不驱动真实解析。
3. **Discovery 浅 `candidate_key`**：如 `smart_parser` / `structured_template`，未锁定用户可验证产品形态。
4. **生成验收只验 `data-regent-event` hook**：不对齐 GoalSpec 成功标准。
5. **Gate：`minimum_samples=1` + 点击 → CONTINUE**：用户口头不满意进不了 REVISE。

## 下一步（建议 GO）

停止「堆用户冲 G6」；改为 **产品质量 REVISE 批次**（仍属 P1，非 P2）：

1. 收紧 `goal_requires_external_evidence`；禁止无关 Goal 注入默认新闻 feed。
2. CapabilityResolution 接真缺口，禁止空 SATISFIED。
3. 假设/生成绑定 GoalSpec success_criteria；预览校验对题。
4. Gate：负面反馈 / guidance MODIFY → REVISE；提高样本门槛。
5. 用同一批内部员工做 **REVISE 后对照体验**（满意率），再谈 PRODUCT 毕业。

## 签署建议

- 产品：记录本文件为 G6 定性失败 Artifact。  
- 技术：开 `p1-revise-quality-01`（名称示意），**NO-GO** 继续 graduation-04 用户堆量。
