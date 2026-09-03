# Agent 内核可执行修复计划：专家复审校正单

> 本校正单是 `agent-core-restoration-executable-plan-2026-08-01.md` 的组成部分；如有冲突，以本文件为准。

## 1. 产品范围校正

M0 不同时覆盖多种异质应用。先冻结一个 reference archetype：**带持久化、一个外部证据输入和一个可执行 Journey 的轻后端 Web App**。围绕它建立 12 个确定性工程回放任务；其他框架和产品形态暂列非目标。

## 2. 顺序校正

真实 Preview、成功基线和最小 Observation 从 M4 前移到 M2，因为它们是判断 loop 是否有效的前提：

- M2 增加按 Runtime Profile 运行的最小真实 Preview。
- verification 成功后原子写入不可变 `accepted_workspace_snapshot`；REVISE 从该指针克隆。不要继续复用只在失败路径使用的 `last_good_draft_uri` 命名。
- 真实用户 Journey/反馈生成带来源的 Observation，并能驱动一次 REVISE；内部 smoke 不得成为产品 Observation。
- M2 出口至少有一个固定 fixture 完成 `Preview → Observation → 基于 accepted snapshot 的 REVISE`。这只证明工程闭环，不证明产品成功率。
- M4 改为加固以上链路：租户隔离、进程回收、持久卷配额、并发修订、失败回滚、provenance 与 GC。

## 3. 技术事实校正

- B5 不是“数学上至少三轮”或“结构上不可能触达 smoke”；准确描述是验证短路导致反馈不完整、修复轮与归因延迟增加。新 verifier 应在物理可执行范围内收集全量结果；不可执行的阶段标为 `BLOCKED`。
- A2 的入口契约隐藏成立，但标准 FastAPI 文件只要存在模块级 `app`，即使另有 `__main__` 启动代码也不必然失败。
- D1 应表述为 compose/development 默认 local 缺少隔离；production 构造路径已有 local fail-closed guard。修复是让部署配置与 guard 一致，并使用独立 rootless/等价 sandbox service，禁止 Docker socket 直挂 worker。
- 快照除 80 文件静默截断外，还会静默跳过单文件超过 200 KB 的内容；两者都必须进入 manifest 完整性失败。
- provider 重试必须服从总 deadline、`Retry-After` 和错误白名单；400/401/403 不重试。

## 4. 指标口径校正

- `first_runnable_rate`：首次模型生成结束、repair 前，在冻结 Profile 中启动并通过最低 Journey 的独立 Goal 数 / 所有进入生成的 Goal 数。超时和基础设施失败留在分母并另行分类。
- `preview_ready_rate`：存在 hash 与已验证 workspace/Profile 一致且最低 Journey 可执行的 Preview 的 Goal 数 / 所有进入生成的 Goal 数。
- `verified_success_rate`：独立 verifier 完成冻结 Journey 的 Goal 数 / 全部意向分配 Goal 数。
- `mean_repair_rounds`：首次 verification 失败后追加的修复回合数；同时报告中位数与 P95。
- 用 `unplanned_rescue_rate` 取代笼统的人工介入率；必要审批单独报告完成率和等待时间，不能视为坏事。
- 时延拆为 queue、model/tool run、verification/deploy、human wait；不得把陈旧排队时间全部归因给 Agent。
- 修复期北极星为 `verified_iteration_rate` 与 `cost_per_independently_verified_iteration`。

12 个冻结任务只作为工程门，不作显著性声明。产品/策略比较必须预注册最低改善量、成本与分段时延护栏，做功效计算，并至少遵守现有每臂 30 个独立 Goal 的下限。基础设施误失败率还需人工抽样复核，不能由分类器自证。

## 5. 产品试点校正

M6 先用 5–10 名目标 Operator、3–5 个真实 Goal 做 pilot；“3 位用户 + 1 次迭代”只算闭环演示。除真实反馈驱动 V2 外，还需记录任务完成率、首次价值时间、再次使用意愿/可用性、非计划救援、必要审批和完整成本。阈值在 pilot 前按基线与容量冻结，不以拍脑袋百分比自动触发 GQ-4。

## 6. 最小合并队列

1. 契约失败测试：截断/畸形 tool call、health profile、snapshot truncation、budget exhaustion 诊断。
2. provider 完整性与有限重试。
3. versioned Runtime Profile，生成与验证共用。
4. versioned file manifest，统一 snapshot/materialization/verifier。
5. RunState 去递归，统一 deadline/token/transcript；耗尽保存诊断 Artifact 且禁止晋级。
6. edit/grep/glob/read_artifact + `PASS/FAIL/BLOCKED` 验证模型。
7. 独立 sandbox service 与网络 Permit；收敛 compose/README。
8. 动态 Preview 最小链路。
9. `accepted_workspace_snapshot` 指针、hash、生命周期与 REVISE 恢复。
10. Skills on/off 消融；最后才做缓存、compact 优化和治理回收。

各阶段工期仅是容量估算，承诺以 Exit Gate 为准。
