# Multi-Agent 补丁完整性检验（2026-07-31）

> 对象：未提交工作树改动（相对 HEAD e3a4cc6）+ 关联提交 0c64810 / e3a4cc6
> 方法：git diff 逐文件读码 + 接口调用方排查 + 单元测试运行
> 结论：最新修复**符合**期望修补；§12.7 七条护栏全部在代码层落地，F1 实质闭合，MA-0..MA-6 实现到位（MA-5 为明确骨架），10/11 调研借鉴项闭合。

## 一、期望修补清单 vs 落地核验

### A. 我的评审发现 F1/F2/F3
| 发现 | 期望修补 | 代码/文档落地 | 判定 |
|---|---|---|---|
| F1 CERTIFIED flag 早于 MA-2 认证合同 | 生产 opt-in 须受五类摘要复算约束 | `member_contract.verify_template_certification`（失败即闭）；`organization_engine` 对 `pm-dev-independent-qa-v1` 强制复算，不通过 DENY；迁移 `0040` 回填摘要；Tech-Spec/Plan 改写 | ✅ 闭合 |
| F2 MA-3 可并行 | 标注并行线 | Plan 已落地并接入生成主链；并行属排期提示，非正确性问题 | ✅ 合理 |
| F3 OTel GenAI | 显式入验收 | Plan 注明「OTel GenAI conventions 已写入验收，供应商栈未接」 | ✅ 已记录，留待后续 |

### B. Plan §12.7 过度修复护栏（全部代码落地）
1. opt-in 不得绕过裁剪复活被排除模板 → `organization_engine` **删除**了"preferred 可加回 pruned 候选"的危险分支 ✅
2. 缺安全单 Agent champion 时 fail closed → `task_features` 删除兜底首个候选（改 `R_NO_SAFE_SINGLE_AGENT_FALLBACK`）；`organization_service` 未知基线默认 1.0 ✅
3. CERTIFIED 须嵌入式五类摘要复算 → 同上 F1 ✅
4. 持久计划终态不可普通 upsert 改写 → `execution_plan` 终态同态直接返回、异态抛 DomainError ✅
5. Artifact 查询须带 Goal 范围 → `context_artifact.read_by_hash` 强制 `goal_id` ✅
6. A2A 未知状态拒绝投影 → `a2a_projection.project_run_state` 未知态抛 ValueError ✅
7. 框架仅当替换 Kernel 时拒绝 → `assert_not_replacing_kernel` 由硬黑名单改为 `replaces_kernel` 标志 ✅

### C. MA-0..MA-6 实现状态（来自 Plan §12 + 代码）
- MA-0 合同冻结 ✅；MA-1 三指标/MAST **Schema+单测完成，生产分类路径未接线**（对齐 PRD §12）；MA-2 模板整体认证+迁移0040 ✅；MA-3 长任务耐久接入生成主链 ✅（`agent_runner` 压缩前存 Transcript、大结果卸载、todo_write 落持久计划；`generator` 注入服务）；
- MA-4 TaskFeatures 裁剪+`dispatch_decisions` 审计 ✅（`hive_runtime` 写 PM→Dev→QA 审计行）；MA-6 P2-5 Gate 钩子+A2A 投影 ✅ 未激活；
- MA-5 P2-4 冻结实验 = **半落地骨架**，完整生产盲评窗口属实验窗口交付物（与"无正净收益 DecisionRecord 不扩大生产默认/现实权限"原则一致，非遗漏；沙箱试验不在此禁令内）。

> **口径更新（2026-08-11）**：净收益 / P2-4 Gate 只约束**生产晋级与扩大现实权限**，不约束沙箱探索。

### D. 调研 11 项借鉴闭合（A1–E）
10/11 已闭合，仅 #9 OTel 显式记录为后续对齐。A2A 映射、MCP 边界、指标合同、MAST 词表、成员三要素、模板整体认证、任务属性裁剪、过程可检查、上下文卸载、持久计划 均到位。

## 二、接口变更安全性
- `read_by_hash(goal_id, content_hash)` 新签名：`core/src` 内无其他调用方 → 无破坏。
- `assert_not_replacing_kernel(framework_name, *, replaces_kernel)` 新签名：同上，无破坏。

## 三、遗留 / 优化项（非阻塞）
- O1：`verify_template_certification` 在 engine 中按模板名 `pm-dev-independent-qa-v1` 硬编码触发；未来新增第二类固定模板时需泛化为「所有 CERTIFIED 模板均复算」。
- O2：确认 `offload_tool_result` 内部尊重 20k Token 阈值，避免对极小结果也落盘（效率，非正确性问题）。
- O3：Artifact 读取的 Goal 隔离目前只覆盖 `read_by_hash`；其余读路径（如按 id）建议也带 Goal 范围以彻底隔离。
- O4：MA-5 完整生产盲评窗口未跑 —— 这是 P2-5 激活前的唯一前提，按设计保留为实验窗口交付物。
- O5：F3 OTel 供应商栈接入未做，已写入验收待后续。

## 四、测试
- 新增/扩展：`test_multiagent_supplement`（认证失效、强顺序裁剪、计划不可重开、Artifact 卸载/回hydrate、A2A 边界）、`test_aar1_foundation.TestOrganizationEngineF3`（过度修复护栏）。
- **运行结果（实证）**：在 `D:/users/showmac/documents/agentOS/.venv` 中安装 `aiosqlite` 后运行——
  - 首次跑（basetemp 路径因 shell `%TEMP%` 未展开而损坏）：`.......E.....................................` → **42 passed / 1 error**，唯一 error 是 `test_context_artifact_offload_and_rehydrate` 的 setup 阶段因 basetemp 路径非法而 OSError，**非代码缺陷**。
  - 改用干净 basetemp `C:/Users/showmac2025/AppData/Local/Temp/pytest_regent_tmp` 重跑：`.............................................` → **45 passed / 0 failed / 0 error**，该用例正常通过。
  - 结论：多 Agent 补丁相关测试**全绿**，§12.7 七条护栏与 F1 闭合均有对应断言覆盖。

> 本检验不改任何验收口径；O1–O5 为可选增强，不阻碍当前补丁验收。
