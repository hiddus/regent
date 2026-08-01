# Agent 内核可执行修复计划（复审收敛版，2026-08-01）

> 状态：ACTIVE（M0–M5 工程接线已落地；**M6 5% agentic canary 已开窗** 2026-08-01，默认仍 artifact-backed；出口 Gate / 真实用户闭环仍待观察）。
> 输入：`agent-core-restoration-plan-2026-08-01.md`、其 review patch、当前源码、PRD、Technical Spec 与 GQ-3 生产报告。
> 目标：先恢复一条可运行、可修复、可预览、可增量迭代的强单 Agent 交付闭环，再决定是否恢复 Skills 扩展、GQ-4 和多 Agent 投资。
>
> 已落地（截至 M5，不含 M6）：
> - M0 冻结任务集 / failure taxonomy / Run 账本 / provider 录制样本
> - M1 finish_reason、HTTP 退避、submit、预算诊断、file-manifest/v1
> - M2 runtime-profile/v1、全量 verification、Profile 驱动 smoke、accepted_workspace_snapshot
> - M3 同轨迹 repair、glob/grep/edit_file/read_artifact、repair_policy、compact 保留失败
> - M4 RuntimePreview 路由、晋级 hash 门、TaskCard primary_failure/recovery
> - M5 三 Skill + 路由 + 消融报告骨架
> - 默认 generation_strategy 仍为 artifact-backed；GQ-4 关闭
> - M6：5% agentic canary 已开窗（见 `docs/m6-canary-window-2026-08-01.json`）；出口 Gate 未宣称达标

## 1. 复审结论

原计划对隐式运行时契约、递归修复、静默截断、产物快照和知识未接入等问题的方向判断基本成立；review patch 对产品与安全边界的修正也成立。需要进一步收敛的地方有四点：

1. 代码体量不能证明治理层是故障的因果来源；冻结新增投资即可，不先做大规模删除。
2. `success_criteria` 是用户价值合同，不应用来承载 health、入口或启动命令等基础设施契约；这些应属于版本化 Runtime Profile。
3. 轮次耗尽后的文件只能保存为诊断 Artifact，不能以 `degraded` 方式进入候选交付。
4. 先修测量和失败语义，再修改生成策略；否则成功率变化无法归因。

当前生产报告只能证明漏斗严重退化，不能比较 `artifact-backed` 与 `agentic`：两臂成功率均为 0，agentic 仅 4 个样本，成本记录为 0，且流量窗并未隔离。GQ-4 必须保持关闭。

## 2. 冻结范围与成功定义

### 2.1 立即冻结

- 不新增 Hive、自适应组织、组织指标、GQ 推广或新的治理模块。
- 不删除治理代码，不做跨域重构；只允许修复阻断单 Agent 闭环的接线。
- `generation_strategy` 继续默认 `artifact-backed`；agentic 只用于隔离的评测任务。
- 禁止把宿主 Docker socket 挂入持有模型密钥或数据库凭据的 worker。

### 2.2 唯一产品成功定义

一次成功必须同时满足：冻结 Goal 的验收 Journey 通过；产物在认证 Runtime Profile 中真实启动；项目测试与 smoke 有可追溯结果；可访问 Preview 使用同一运行语义；用户反馈能生成 REVISE；第二版基于第一版 workspace 增量修改。内部状态、静态检查或 zip 生成均不能单独计为成功。

## 3. 执行原则

- 每个批次只修一种失败语义，并带失败测试、成功测试、观测字段和回滚开关。
- 先在固定的 12 个离线任务上验证，再开 5% canary；不得直接重跑未冻结的生产 Goal 作为证明。
- 所有阈值在运行前登记；失败和超时进入分母。
- P0/P1 阶段禁止以 prompt 调参掩盖运行时错误。

## 4. 分阶段实施

### M0：建立可信基线（2–3 天）

目标：让每次失败能定位到唯一阶段，并让成本、时间和产物完整性可计量。

| ID | 改动 | 产出与验收 |
|---|---|---|
| M0-1 | 冻结 12 个任务：4 个静态/轻后端、4 个 Flask/FastAPI、4 个多文件前端；固定模型、工具、预算、Runtime Profile 和 Journey | 清单、hash、难度标签、成功判定、超时判定入库；调试集与最终集分离 |
| M0-2 | 建立稳定 failure taxonomy：`MODEL_TRUNCATED`、`TOOL_CALL_INVALID`、`ARTIFACT_INCOMPLETE`、`STATIC_FAILED`、`TEST_FAILED`、`START_FAILED`、`SMOKE_FAILED`、`BUDGET_EXHAUSTED`、`PREVIEW_FAILED` | 每个失败只有一个 primary code，可带 secondary causes；未知异常不得映射为成功 |
| M0-3 | 统一 Run 账本：跨 repair 累计模型调用、token、墙钟、工具成本、快照 manifest、transcript 与 verification | 人工构造两轮 repair，账本总数等于两轮之和；成本不再为假 0 |
| M0-4 | 录制/回放真实 provider 响应样本 | 覆盖正常 tool call、`finish_reason=length`、429、5xx、超时、畸形 arguments；敏感内容脱敏 |

出口 Gate：12 个任务均能稳定落入明确终态；同一录制输入重复执行得到相同 primary failure code；账本无缺字段。未通过不得进入 M1。

### M1：停止静默失败（3–5 天）

目标：修复“系统说成功但实际上没有完整产物”的路径。

| ID | 改动 | 产出与验收 |
|---|---|---|
| M1-1 | provider 显式处理 `finish_reason`；设置可配置输出上限；任一 tool call JSON 解析失败即返回 `MODEL_TRUNCATED` 或 `TOOL_CALL_INVALID` | 截断/畸形回放不得变成无工具调用的正常结束 |
| M1-2 | 对 429、5xx、连接错误和超时做有抖动的有限退避；400/401/403 不重试 | 两次 429 后成功；401 只调用一次；所有尝试计入账本 |
| M1-3 | 增加显式 `submit`；“没有 tool call”只表示模型停止，不能表示完成 | 未 submit 的 run 不能产生 ReleaseCandidate；submit 后仍必须经过 verification |
| M1-4 | 轮次/时间/token 耗尽时先保存 workspace、manifest 和 transcript，再以 `BUDGET_EXHAUSTED` 失败 | 文件可恢复，但不能发布、不能计入成功 |
| M1-5 | 用版本化 manifest 文件策略替代双重后缀白名单；显式支持 `.ts/.tsx/.jsx/.vue/.svg/.sql` 与无后缀文本文件，排除依赖目录、VCS、二进制和密钥；文件数与总字节都有上限 | 30 个 `.tsx` 完整入快照；任何排除/截断均列出文件与原因并使完整性校验失败 |

出口 Gate：M0 回放中的静默成功为 0；产物 manifest 完整率 100%；所有预算耗尽都保留诊断 Artifact 且发布数为 0。

### M2：统一 Runtime Profile 与验证语义（4–6 天）

目标：消除生成器不知道、验证器却强制执行的隐式契约。

| ID | 改动 | 产出与验收 |
|---|---|---|
| M2-1 | 定义 `runtime-profile/v1`：允许的项目形态、入口模块/对象、启动命令、工作目录、health/readiness 路由、依赖安装、测试命令、网络策略和 Preview 类型 | JSON Schema + 版本 + 兼容策略；Profile 在生成前进入上下文，在验证时引用同一对象 |
| M2-2 | smoke 只探测 Profile 声明或运行时发现的路由；删除无条件追加 `/health` 与 `/api/health` | 无 health 的 Profile 可通过真实业务路由；声明 health 的 Profile 缺端点时产生可行动错误 |
| M2-3 | 一次 verification 尽可能收集 static、tests、start、smoke 的全量结果；仅在后续阶段物理不可执行时标注 `blocked_by`，不把 skipped 当 passed | 一个含静态与测试错误的 fixture 单轮返回两类 gap；启动被静态错误阻断时原因明确 |
| M2-4 | “无测试”按 Profile 处理：要求测试的 Profile 为失败；允许无测试的探索 Profile 为明确降级且不能晋级正式交付 | 两类 Profile 的门禁行为稳定、可解释 |
| M2-5 | sandbox 使用独立最小权限执行服务；生产网络默认关闭，经 Permit 和 allowlist 代理开启；local driver 仅限测试并对网络 fail-closed | worker 无宿主容器管理权限；无 Permit 外连失败；有 Permit 只访问声明目标 |

出口 Gate：3 个认证 Profile（静态 Web、Flask、FastAPI）各有最小 golden fixture；生成、验证、Preview 引用相同 Profile hash；隐式 health/入口失败为 0。

### M3：把 repair 改为真正的单轨迹收敛（5–8 天）

目标：修复轮保留因果链、共享预算，并以小范围编辑收敛。

| ID | 改动 | 产出与验收 |
|---|---|---|
| M3-1 | 删除 `self.run()` 递归 repair；在同一 RunState 中把结构化 gaps 作为新 user turn 追加 | conversation、transcript、预算和 compact events 连续；DB 轮数与实际一致 |
| M3-2 | 增加 `glob`、`grep`、精确 `replace`/`edit_file`；编辑需 old text 唯一匹配或带版本 hash，冲突时失败 | 改一行无需重发整文件；冲突不会静默覆盖 |
| M3-3 | 增加 `read_artifact`，所有 offload 引用均可读、带 hash、大小和保留期 | 大测试日志 offload 后 Agent 可读取；悬空引用测试失败 |
| M3-4 | repair 策略由 failure code 路由；仅在同类失败重复时启用受预算限制的候选分支，由独立 verifier 选择 | 禁止默认温度阶梯；记录分支参数与成本；总预算硬上限不被分支绕过 |
| M3-5 | compact 保留约束、失败码、关键 traceback 和未解决决定；优先去除可重读的文件全文与 tool arguments；清除前先持久化 | 压缩后能回答“当前失败原因、证据和下一步”；workspace 内容按需重读 |

出口 Gate：冻结任务集 `first_runnable_rate ≥ 50%`，`mean_repair_rounds ≤ 2.5`，预算越界为 0；相对 M2 基线，单轮输出 token 中位数下降至少 50%。阈值若样本不足，只报告区间，不宣称达标。

### M4：补齐真实 Preview 与增量迭代（4–7 天）

目标：让用户看到的 Preview 与验证过的运行时一致，并确保 REVISE 不从零开始。

| ID | 改动 | 产出与验收 |
|---|---|---|
| M4-1 | Preview 按 Runtime Profile 选择静态托管或隔离运行时，不再把后端应用解压成静态文件 | Flask/FastAPI 的 POST 与持久化 Journey 在 Preview 中真实执行 |
| M4-2 | 成功 verification 后原子写入不可变 `last_good_workspace`；REVISE 从该基线克隆 | 第二轮 changeset 含 REPLACE/ADD，未修改文件 hash 不变，不从空目录开始 |
| M4-3 | 发布前重新校验 manifest、Profile hash、verification hash 与 Preview deployment hash | 任一 hash 不一致即拒绝晋级 |
| M4-4 | 控制台展示 primary failure、诊断 Artifact、预算和 2–3 个可执行恢复选项 | 不再只显示“失败/允许/拒绝”；选项与后端真实动作对应 |

出口 Gate：至少 3 个 golden App 完成 `生成 → 测试 → Preview → 用户操作 → REVISE → 第二版`；刷新后数据仍在；第二版不是从零生成。

### M5：Skills 最小闭环（5–8 天，M4 后）

目标：证明知识确实能改善新任务，不先建设大而全的 Skill 平台。

| ID | 改动 | 产出与验收 |
|---|---|---|
| M5-1 | 只实现 Skill manifest、按需读取与版本/hash 记录；Skill 不能授予额外权限 | transcript 可追溯“为何选、读了什么版本”；权限仍由 Permit 决定 |
| M5-2 | 首批只做 3 个 Skill：`runtime-contract`、`web-app-scaffold`、`test-harness` | 每个 Skill 有说明、脚本/模板、适用条件和反例；不先做 7 个 |
| M5-3 | 规则到 Skill 双向映射；验证错误返回对应做法引用，但 verifier 不修改产物 | 每类支持的 gap 都能定位到可操作指导 |
| M5-4 | 在冻结集做 Skill on/off 消融，并用未见新 Goal 做迁移验证 | 预注册成功率差、成本与延迟阈值；置信区间不支持收益则停止扩充 |

出口 Gate：Skill 路由准确率 ≥90%；on/off 成功率提升达到预注册阈值且置信区间不跨 0；成本/延迟不突破护栏。未通过则保留静态 Profile，不进入跨 run LLM 记忆。

### M6：受控 Canary 与产品闭环（至少 1 周观察）

目标：证明修复对真实用户任务有效，而非只对 fixtures 有效。

- 先 5% agentic canary，最多扩大到 10%；同模型、同任务分层、同总预算、同 Profile。
- 严重安全事件、发布错误或成本/P95 超护栏立即回退到 artifact-backed。
- nightly 运行真实模型到真实 sandbox/Preview 的端到端测试；提交级 CI 使用录制回放，避免外部模型抖动成为唯一门禁。
- 至少完成一条真实闭环：1 个真实 Goal、3 位真实用户、1 条可归因反馈、1 次基于 last-good 的增量 REVISE。

出口 Gate：`preview_ready_rate ≥ 60%`、`first_runnable_rate ≥ 50%`、`human_intervention_rate ≤ 0.3`、`mean_repair_rounds ≤ 2.5`，且至少一条真实闭环成立。GQ-4 是否重开需单独 DecisionRecord；这些指标不能自动触发默认切换。

## 5. 测试与验收矩阵

| 层 | 每次提交 | Nightly / Canary | 必须捕获 |
|---|---|---|---|
| 单元 | provider 解析、manifest、预算、Profile schema、edit 冲突 | — | 截断、畸形 JSON、分类、边界值 |
| 组件 | 录制 provider + 临时 workspace + verifier | 更多框架/Profile | repair 连续性、全量 gaps、offload 可读 |
| 集成 | rootless/等价 sandbox fixture | 网络 Permit、依赖代理、真实启动 | 隔离、超时、进程清理、端口与命名空间 |
| E2E | golden fixture 回放 | 真实模型 → 真 sandbox → 真 Preview → REVISE | 用户 Journey、hash 一致性、增量基线 |
| 产品 | — | 盲评与 3 位真实用户 | 有用性、人工负担、反馈驱动迭代 |

所有门禁必须保存：任务集 hash、代码版本、模型与参数、Profile/Skill hash、预算、原始 failure code、产物 manifest、verification evidence 和统计脚本版本。

## 6. 合并与回滚策略

- 每个 M 阶段拆为 2–4 个可独立回滚的 PR；先加观测/测试，再改行为，最后删除旧路径。
- 新行为全部置于版本或 feature flag 后；数据库变更先向后兼容双写，再切读，最后清理。
- 回滚不删除诊断 Artifact 和 transcript；已开始的 Run 使用创建时冻结的 Profile/策略完成或明确取消。
- 禁止把 M1–M4 合成一次“大重构”；任何阶段 Gate 未过，只修该阶段，不并行扩大范围。

## 7. 责任边界

| 角色 | 负责 | 不负责 |
|---|---|---|
| 产品负责人 | 冻结任务、Journey、真实用户闭环、阈值与停止规则 | 用内部 pass 代替产品成功 |
| Agent Runtime | provider、loop、tools、compact、预算与 Skills 接入 | 自行改变用户成功标准 |
| Runtime/SRE | Profile、sandbox、Preview、网络与进程生命周期 | 通过宿主 Docker socket 省略隔离设计 |
| QA/Eval | fixtures、回放、盲评、统计与证据保全 | 用 mock 数量或单次 demo 宣称成功 |
| Governance | Permit、审计、不可变 Artifact 和晋级门禁 | 在单 Agent 未闭环前扩建组织层 |

## 8. 明确不做

- 不以提高 temperature、增加 token、增加 repair 次数或增加 Agent 数量作为独立修复。
- 不把文件策略改为无限制黑名单扫描，不把密钥或依赖目录带入产物。
- 不把预算耗尽、无测试、skipped smoke、静态 Preview 或 zip 生成记为成功。
- 不在 M4 前建设跨 run LLM 记忆；先证明单次 Run 能正确收敛并保留 last-good。
- 不在 M5 消融通过前扩充 Skill 数量；不在 M6 真实闭环通过前恢复 Hive/GQ-4 投资。

## 9. 首个执行批次

批准后第一批只做 M0 与 M1-1/M1-4，共四个交付物：冻结 12 任务清单、失败码合同、统一 Run 账本、provider 截断与预算耗尽的失败测试。该批次不改变默认生成策略、不改 Preview、不做 Skills，也不触碰治理模块。完成后以回放结果决定是否进入 M1 其余条目。
