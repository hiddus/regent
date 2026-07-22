# Regent P1 剩余编码执行计划

> 状态：ACTIVE / P1 剩余工作的唯一执行清单（Graduation 阶段）  
> 日期：2026-07-22  
> 产品基线：`p1-core-capability-requirements.md`  
> 技术基线：`p1-core-final-technical-spec.md`  
> 范围原则：继续原 P1 至全局 DoD；P2 路线见 `p2-platform-plan.md`，须先过 P2-0  
> 下一阶段入口：`p1-graduation-01`（不得直接开始 `p2-scheduler-01`）

## 1. 当前结论

P0 已完成。P1 的 0011—0022 与 R1—R8 主链编排骨架已接通；R9—R11 部分诚信与证据项已落地（真实 Docker 构建、Permit、Static Preview、内部烟雾观测排除、Dead Letter 查询/重放、mypy strict、Pytest 通过）。

**2026-07-22 复审结论**：P1 **功能链接近完成，但尚未满足全局 DoD，不能宣布 P1 完成，不能进入纯 P2 开发。**

仍阻塞 Graduation 的问题：

1. 质量门禁：Ruff 等 DoD 12 项须保持全绿；
2. 验收不得用 API 注入 activation 冒充非开发用户完成核心任务；
3. Preview 不得注入合成「Complete task」交互（缺 `data-regent-event` 应失败）；
4. Evidence 不得仅等于 Goal 文本自证；需至少一个受控外部/研究证据源；
5. 须有可信端到端验收记录（含浏览器核心旅程）；
6. 凭据泄露须轮换并清理；诊断脚本禁止入库；
7. 须建立 Git 基线、标签与可回滚发布；
8. 本文件与 `p2-platform-plan.md` 的 P2-0 门禁对齐后，方可开工 P2-1。

准确状态：

```text
P1 功能链接近完成，但可信验收、安全基线和发布纪律尚未毕业。
```

P1 唯一剩余目标不变：

```text
未知 Goal
→ 有来源的证据与产品发现
→ 候选假设比较和唯一决策
→ 版本化需求与能力解析
→ 可追溯生成、隔离构建和受控发布
→ 人类可完成核心任务的 Preview
→ 真实 Observation
→ 唯一 CONTINUE / REVISE / STOP 决策
```

Graduation 批次与 P2 路线见 [`p2-platform-plan.md`](p2-platform-plan.md) **P2-0**。
## 2. 保留、停止与延后

### 2.1 继续保留

- 0022 的 Confirm/Start 分离、`GoalExecutionRequested`、Worker 执行、短轮询、失败重试、幂等和 Dead Letter；
- AppProject 作为长期 App 身份，Goal 作为一次目标/迭代周期；
- 0011—0016 的既有表、状态机、服务、API、Artifact 和治理规则；
- 受控单 Agent/固定模板默认策略；
- `python-web-v1` 作为 P1 唯一 Runtime Profile；
- 静态 App 作为合法产品形态，但只有满足所选假设和核心用户任务时才可通过。

### 2.2 立即停止

- 将 Goal 直接生成静态页面的旁路视为正式 P1 主链；
- 用 Goal metadata 中的进度文案代替真实控制对象状态；
- 仅凭文件存在、`<main>`、按钮或事件属性判定 App 可体验；
- 由前端逐个调用 Discovery、Generation、Build 或 Release API 编排流程；
- 在主链跑通前继续创建第三个验证 App。

### 2.3 延后

- 新 Runtime Profile、通用低代码平台、完整账号系统；
- 多 Agent 编排、多 Goal 资源竞争、Champion/Challenger 平台；
- SSE、通用工作流 DSL、完全无人监管的生产发布；
- 为单个验证 App 向 Core 增加业务对象。

## 3. 剩余编码批次

以下批次只是实施顺序，不是新产品版本。P1 仍按整体口径验收。

### R1：执行主链与状态投影

目标：让 Start 进入原 P1 控制面，禁止直接进入静态 Preview。

代码工作：

1. 修改 `GoalExecutionRequested` handler，校验 Goal 为 `ACTIVE`、最新 GoalSpec 为 `FROZEN`；
2. 删除 handler 对 `AppPreviewService.generate()` 的直接调用；
3. 创建首个 Discovery 请求并在同一事务写入下一条 Outbox 事件；
4. 为所有阶段定义完整事件信封、幂等键、失败码、重试和 UNKNOWN 处理；
5. 将 `execution_stage` 改为底层对象的可重建投影，不再作为事实源；
6. AppProject status 返回当前真实阶段和对应对象 ID；
7. Conversation 只投影事实事件，不决定执行步骤。

建议事件链：

```text
GoalExecutionRequested
→ DiscoveryRoundRequested
→ DiscoveryCompleted
→ RequirementRequested
→ RequirementValidated
→ CapabilityResolutionRequested
→ CapabilityResolutionSatisfied
→ GenerationRunRequested
→ WorkspaceSnapshotReady
→ DependencyResolutionRequested
→ AppBuildRequested
→ AppBuildPassed
→ PreviewDeploymentRequested
→ PreviewDeploymentSucceeded
```

主要代码入口：

- `core/src/regent/worker/main.py`
- `core/src/regent/application/goal_execution_service.py`
- `core/src/regent/runtime/dispatcher.py`
- `core/src/regent/runtime/long_tasks.py`
- `core/src/regent/application/app_guidance_service.py`
- `core/src/regent/api/app_projects.py`

测试门禁：

- Goal Worker 不再直接调用静态 Preview 生成；
- 每个可写事件都有已注册 handler；
- 重复事件不产生重复控制对象；
- 任一阶段 Worker 崩溃后可恢复；
- 未注册事件进入 Dead Letter，不无限重试；
- UI 状态能从底层对象重建。

完成条件：确认并启动 Goal 后，数据库首先出现 DiscoveryRound，而不是 AppPreviewRelease。

### R2：Discovery 与 Hypothesis Decision

目标：让 Core 先用证据选择产品机会，再生成 App。

代码工作：

1. 接入 Goal Eligibility，意图不足时进入澄清或 `BLOCKED`，不得臆造产品；
2. 创建并执行 `DiscoveryRound`；
3. 通过受限 Evidence Connector 获取来源，保存来源快照和内容哈希；
4. 复用 `DiscoveryWorker` 和 `ProductDiscoveryService` 生成结构化候选；
5. 形成至少两个实质不同且有证据的 ProductHypothesis，或明确输出 `RESEARCH_MORE/BLOCKED/STOP`；
6. 按冻结策略形成唯一 HypothesisDecision；
7. 在对话时间线展示用户可理解的研究结论、候选差异和选择理由。

主要代码入口：

- `application/discovery_round_service.py`
- `application/discovery_worker.py`
- `application/product_discovery_service.py`
- `infrastructure/evidence_sources.py`
- `api/product_creation.py`

测试门禁：

- 无 Evidence 引用的候选不能被选择；
- Prompt Injection 来源不能改变 Goal 硬约束；
- 候选无实质差异时不能伪装成两个假设；
- 相同输入摘要保持决策幂等；
- 没有合法 Evidence Source 时进入明确非成功状态。

完成条件：任一 Preview 都能追溯到 DiscoveryRound、候选、证据引用和唯一选择决策。

### R3：Requirement 与 Capability Resolution

目标：让冻结需求成为生成的权威输入，并在生成前解决能力缺口。

代码工作：

1. 从选中的 Hypothesis 自动创建 RequirementRevision；
2. 继承 GoalSpec 硬约束、非目标和 Evidence 引用；
3. 执行 Schema、差异和约束验证后将 revision 固化为 `VALIDATED`；
4. 从需求推导 CapabilityRequirement；
5. 创建 CapabilityResolutionPlan；
6. 严格按 `REUSE → CONFIGURE → COMPOSE → BUILD → REQUEST_HUMAN → BLOCK` 解析；
7. 只有 ResolutionPlan 满足生成前置条件，才写入 Generation 请求。

主要代码入口：

- `application/requirement_revision_repository.py`
- `application/capability_resolution_service.py`
- `application/product_discovery_service.py`
- `api/product_creation.py`

测试门禁：

- 原始 App 草案的 `first_deliverable` 不能替代 RequirementRevision；
- 需求修订不能弱化 Goal 硬约束；
- 能力缺口不能静默退化成无功能装饰控件；
- Resolution 顺序稳定且可重放；
- BLOCK/REQUEST_HUMAN 不得继续生成。

完成条件：每项主要生成内容可追溯到冻结需求；缺少关键能力时流程明确停止或请求人类。

### R4：正式 Generation

目标：所有模型输出先成为受约束的 FileChangeSet 和 WorkspaceSnapshot。

代码工作：

1. 根据 RequirementRevision 和 ResolutionPlan 创建冻结 GenerationPlan；
2. 创建 GenerationRun 和 Outbox 请求；
3. 使用 `ArtifactBackedCodeGenerator` 物化完整源码 Artifact；
4. 验证 CREATE/REPLACE/DELETE、路径、哈希、配额和允许文件集；
5. 使用 WorkspaceWriter 原子提交；
6. 生成不可变 WorkspaceSnapshot、manifest、source archive 和内容摘要；
7. 保存模型、提示、Schema、用量和失败信息；
8. 可复用现有静态 App 提示配置，但禁止直接写 Preview 目录。

主要代码入口：

- `application/generation_service.py`
- `infrastructure/code_generator.py`
- `infrastructure/workspace_writer.py`
- `api/app_delivery.py`

测试门禁：

- 无 VALIDATED Requirement/满足的 ResolutionPlan 不可生成；
- 模型输出不能路径逃逸、写 symlink 或绕过 Artifact；
- 同一输入摘要重放不重复计费或产生不同事实；
- 生成失败必须收敛到稳定终态；
- WorkspaceSnapshot 可完整重放。

完成条件：生成结果全部进入 GenerationRun、FileChangeSet 和 WorkspaceSnapshot 证据链。

### R5：Dependency 与隔离 Build

目标：恢复 0014 正式构建门禁，禁止用静态文件检查冒充 AppBuild。

代码工作：

1. 从 WorkspaceSnapshot 创建 DependencyResolution；
2. 联网物化依赖必须持有 Permit 并通过受控 Egress；
3. 生成锁文件、依赖 Bundle、hash 和 SBOM；
4. 创建 AppBuild；
5. 通过独立 Sandbox/Build Provider 执行断网、非 root、只读和限额验证；
6. 保存 VerificationReport；
7. 外部结果不确定时进入 UNKNOWN 并查询对账；
8. 只有 `PASSED` Build 才发出发布请求。

主要代码入口：

- `application/build_service.py`
- `infrastructure/sandbox.py`
- `api/app_delivery.py`

基础设施前置：

- 提供不向容器 Worker 暴露宿主 Docker Socket 的独立 Build Provider；
- 修复或替换此前异常的 Docker 存储层；
- 固化 `python-web-v1` resolver/sandbox 镜像及摘要。

测试门禁：

- 无 WorkspaceSnapshot 不可 Build；
- 构建默认断网、非 root、只读根文件系统；
- 依赖哈希或 SBOM 不完整时失败；
- UNKNOWN 只对账，不盲目重试副作用；
- Provider 输出路径必须位于独立 output root。

完成条件：生成 App 获得可查询、可恢复、带 VerificationReport 的 `PASSED` AppBuild。

### R6：标准 Release 与 Preview

目标：以 0015 的 ReleaseCandidate/Deployment 作为正式发布事实。

代码工作：

1. 从 `PASSED` AppBuild 创建不可变 ReleaseCandidate；
2. 执行既有审批和 Permit 规则；
3. 创建 Preview Deployment；
4. Provider 发布构建产物并保存 external deployment ID、endpoint 和制品摘要；
5. 支持查询、UNKNOWN 对账和回滚；
6. Console 的“打开 Preview”读取成功 Deployment endpoint；
7. `AppPreviewRelease` 暂时保留历史兼容，但新主链不以其作为正式发布事实。

主要代码入口：

- `application/release_service.py`
- `infrastructure/deployment.py`
- `application/permit_service.py`
- `api/app_delivery.py`

测试门禁：

- 无 PASSED Build 不可创建 ReleaseCandidate；
- 无有效 Permit 不可发布；
- 重复发布请求不产生第二个外部副作用；
- UNKNOWN 能查询收敛；
- 回滚恢复上一不可变 Release。

完成条件：Preview 可从 Deployment 追溯到 ReleaseCandidate、Build、Snapshot、Requirement、Decision 和 Evidence。

### R7：可体验性 Gate

目标：把“页面可加载”提升为“真实用户能完成一个核心任务”。

代码工作：

1. 从 RequirementRevision 提取首轮主要用户路径和成功断言；
2. 在 Build/Preview smoke 中启动真实浏览器；
3. 执行打开、输入/点击、状态变化、结果呈现和关键事件检查；
4. 捕获 JavaScript 错误、无响应控件和虚假交互暗示；
5. 主路径失败时将 Build 或 Release 标记失败，并复用现有重试语义；
6. 对纯展示型产品验证其目标确实不要求交互，避免强行增加业务复杂度。

Idea Validator 回归要求：

```text
用户理解操作
→ 提交或选择 Idea
→ 页面产生可见结果
→ 用户能完成约定的下一步或明确结束任务
→ 价值事件被记录
```

ResearchLoop 回归要求：其产品形态和主路径必须来自证据、HypothesisDecision 和 Requirement；静态实现只有在足以验证该假设时才通过。

测试门禁：

- HTML 中有按钮不等于通过；
- `data-regent-event` 存在不等于用户获得价值；
- 所有看起来可交互的主要控件必须有效；
- 主路径无可见结果、脚本报错或价值事件缺失时失败。

完成条件：至少一个未知 Goal 生成的 App 能被非开发人员独立完成主要任务。

### R8：Observation、Decision 与一次迭代

目标：完成原 P1 最后一段证据闭环。

代码工作：

1. 将指标定义绑定到 Goal、RequirementRevision、ReleaseCandidate 和 Deployment；
2. 收集版本化行为事件和必要的定性反馈；
3. 排除 Bot、内部账号和测试流量；
4. 执行 GateEvaluation；
5. 证据不足时返回 `INSUFFICIENT_EVIDENCE`；
6. 形成唯一 `CONTINUE/REVISE/STOP` IterationDecision；
7. `REVISE` 创建诊断 Work，绑定唯一主要假设；
8. 由该 Work 进入新的 RequirementRevision，并完成一次新版本 Preview；
9. 新旧版本均可追溯、比较和回滚。

主要代码入口：

- `application/observation_service.py`
- `application/feedback_service.py`
- `api/observations.py`
- `api/feedback.py`

测试门禁：

- 无成功 Deployment 不可绑定正式产品指标；
- Observation 必须有事件 ID、签名、归因版本和 release/deployment 绑定；
- Bot/internal/test 事件不进入产品决策；
- 一个 GateEvaluation 只能产生一个 IterationDecision；
- REVISE 必须绑定一个主要假设和一个新 Work。

完成条件：真实用户数据产生一次决策；若为 REVISE，Core 在人工批准下自动完成一个新 Preview。

### R9：诚信恢复与部署基线

目标：移除作弊回路、恢复 fail-closed、修正部署与安全基线，使 Gate/决策重新具备判定可信度。

代码工作：

1. 烟雾观测去伪（`smoke_test_service.py`）：真实 HTTP 探活；观测标记 `is_internal=True`；默认 Gate 指标不得由系统自身行为满足；
2. 删除 Discovery 决策改写 fallback（`execution_orchestrator.py`）：禁止将 RESEARCH_MORE/STOP 事后改写为 SELECT；
3. 恢复 fail-closed：`DockerDependencyMaterializer` 无 Egress 代理时抛 `PermissionError`；`DockerSandboxDriver.build()` 恢复真实 docker 执行；Worker 替换 `_noop_permit_validator`；
4. 安全整改：`redeploy_p1.py` 移除明文密码，改环境变量；建立 git 提交纪律；
5. 部署顺序修正：停服 → 构建镜像 → 跑迁移 → 启服 → 切换 `current` 软链；
6. Gate 处理：无真实外部观测时返回 `INSUFFICIENT_EVIDENCE`，禁止伪造 CONTINUE。

测试门禁：

- 移除烟雾自签回路后，主链在无真实观测时应停在 `INSUFFICIENT_EVIDENCE`；
- `test_dependency_materializer_fails_closed_without_proxy`、`test_sandbox_command_is_offline_and_restricted` 必须通过；
- 部署脚本无密钥泄漏、迁移先于服务启动。

完成条件：Gate 不再因系统自证观测而 PASSED；安全测试恢复绿灯；部署脚本顺序正确。

建议提交：`p1-integrity-01` → `p1-integrity-02` → `p1-integrity-03`

### R10：证据层真实化

目标：补齐真实部署/构建、需求驱动生成与 Gate 修正，使 DoD 第 2/3/5/6/7/8/9 条具备实质证据。

代码工作：

1. 真实 Preview Provider：替换 `InMemoryDeploymentProvider`，发布到可访问 endpoint；
2. 真实隔离构建：配置 Egress 代理，恢复 wheel 拉取、lockfile、bundle 哈希与 SBOM；
3. 需求接入生成：Requirement/Capability 贯通 GenerationPlan，禁止硬编码模板；
4. 真实 Evidence Connector：替换 `InMemoryEvidenceSourceConnector([])`；
5. Gate 修正（R7+R8）：浏览器级主路径验证；仅接受真实、非内部、非 Bot 的 Observation；
6. 验收脚本修正：查询底层控制对象，不再以 `goal.metadata` 为通过依据。

测试门禁：

- Preview endpoint 可 HTTP 访问并完成核心用户任务；
- AppBuild 产物 hash 可复现；
- 无真实观测时 Gate 不得 PASSED；
- 端到端使用 AI 从业者 Goal 跑通可信 CONTINUE/REVISE/STOP。

完成条件：DoD 5/6/7/8/9 实质达成；`preview.invalid` 与 passthrough 构建/依赖全部退出主链。

建议提交：`p1-evidence-01` → `p1-evidence-02` → `p1-evidence-03`

### R11：可靠性与治理加固

目标：建立机制防线，补齐幂等/并发/Permit 与死信恢复，满足 DoD 第 10/12 条。

代码工作：

1. Outbox 死信重放与审计；
2. `PreviewDeploymentSucceeded` 补充 `idempotency_key`；conversation ordinal 并发安全；
3. Permit 生命周期与 rollback 路径验证；
4. `execution_stage` 覆盖 R1–R6 中间态；
5. ORM `naming_convention` + CI `alembic check`；清理 `certified_at` 漂移；
6. Ruff、Mypy strict、Pytest 全量通过。

测试门禁：

- Worker 重启、重复投递、Provider UNKNOWN 下可恢复且幂等；
- 死信可查询、可审计、可受控重放；
- 全量质量门禁通过。

完成条件：DoD 10/12 达成；运维具备可观测、可恢复、可审计闭环。

建议提交：`p1-reliability-01` → `p1-reliability-02` → `p1-reliability-03`

## 4. 全局 Definition of Done

P1 只有同时满足以下条件才完成：

1. 一个事前未知的 AI 从业者 Goal 通过同一 Start 入口运行；
2. 形成可追溯的 GoalSpec、DiscoveryRound、Evidence、至少两个 ProductHypothesis 和唯一 HypothesisDecision；
3. 形成 VALIDATED RequirementRevision 和满足的 CapabilityResolutionPlan；
4. 形成 GenerationPlan、GenerationRun、FileChangeSet 和 WorkspaceSnapshot；
5. 形成 DependencyResolution、PASSED AppBuild 和 VerificationReport；
6. 形成 ReleaseCandidate 和 SUCCEEDED Preview Deployment；
7. 非开发人员能完成至少一个与产品承诺一致的核心任务；
8. 收到真实、非内部、非 Bot 的 Observation；
9. 形成 GateEvaluation 和唯一 `CONTINUE/REVISE/STOP` 决策；
10. 全链路在 Worker 重启、重复投递和 Provider UNKNOWN 下可恢复、幂等和审计；
11. Core 与生成 App 不互相 import，Core 不包含 Challenge 业务模型；
12. Ruff、Mypy strict、Pytest、迁移、协议、安全拒绝和端到端测试全部通过。

`AppPreviewRelease PREVIEW_READY`、静态页面可访问、一次 activation 点击或两个 AppProject 存在，均不能单独作为 P1 完成证据。

## 5. 建议提交顺序

每个提交保持可回滚并包含测试：

1. `p1-orchestrator-01`：事件目录、handler 覆盖检查、Start 禁止直达 Preview；
2. `p1-orchestrator-02`：Goal → DiscoveryRound 持久化接通；
3. `p1-orchestrator-03`：Discovery → HypothesisDecision；
4. `p1-orchestrator-04`：Decision → RequirementRevision；
5. `p1-orchestrator-05`：Requirement → CapabilityResolutionPlan；
6. `p1-orchestrator-06`：Resolution → GenerationPlan/Run/Snapshot；
7. `p1-orchestrator-07`：Snapshot → DependencyResolution/AppBuild；
8. `p1-orchestrator-08`：Build → ReleaseCandidate/Deployment；
9. `p1-prototype-gate-01`：浏览器级主要用户路径验证；
10. `p1-feedback-01`：Deployment → Observation → Gate → Decision；
11. `p1-challenge-01`：AI 从业者真实用户验证和一次决策；
12. `p1-challenge-02`：若决策为 REVISE，完成一次新版本闭环。
13. `p1-integrity-01`—`03`：R9 诚信恢复与部署基线；
14. `p1-evidence-01`—`03`：R10 证据层真实化；
15. `p1-reliability-01`—`03`：R11 可靠性与治理加固。

提交顺序允许在同一编码批次合并，但不得跳过依赖门禁。**R9 → R10 → R11**，R9 未完成前后续验收结果无效。

## 6. 当前风险与处置

| 风险 | 等级 | 处置 |
|---|---|---|
| 烟雾测试自签观测导致 Gate 恒真 | 阻塞 R9 | 移除自证回路，恢复 fail-closed |
| 构建/部署 passthrough 假实现 | 阻塞 R10 | 接入真实 Provider 与 sandbox 执行 |
| 部署脚本明文密码与迁移顺序错误 | 高 | R9 安全整改与顺序修正 |
| 独立 Build Provider 尚未生产可用 | 阻塞 R10 | 先完成 Provider 和固定镜像，不挂载宿主 Docker Socket |
| 多个 202 API 没有 Worker handler | 高 | 建立事件目录和 CI handler 覆盖检查 |
| 单 handler 长时间占用 Lease | 高 | 按持久化阶段拆分，外部调用与事务分离 |
| AppPreviewRelease 与 Deployment 两套发布事实 | 高 | 新主链回归 Deployment，旧表只兼容历史 |
| Goal metadata 成为伪事实源 | 中 | 状态从底层对象投影并提供重建测试 |
| Evidence Source 不足 | 中 | 明确 BLOCKED/RESEARCH_MORE，禁止退化为模型常识直出页面 |
| Preview 结构检查误报成功 | 高 | 增加浏览器级主要任务 smoke |
| 真实样本不足 | 中 | 返回 INSUFFICIENT_EVIDENCE，不伪造决策 |

## 7. 编码启动结论

结论：`GO`，从 **`p1-graduation-01`（P2-0 / P1 Graduation）** 开始，见 `p2-platform-plan.md`。

R1—R11 编排与部分诚信项已推进，但 2026-07-22 复审确认：**全局 DoD 未齐，不得进入 `p2-scheduler-01`。** Graduation 完成并留档后，再按 P2-1→P2-8 顺序扩展平台能力。
