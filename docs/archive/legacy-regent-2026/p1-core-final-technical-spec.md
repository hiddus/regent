# Regent P1 Core 最终编码技术规范

> 状态：FINAL / 唯一有效编码技术文档  
> 日期：2026-07-18  
> 审查：产品、主架构、技术架构师联合确认  
> 启动结论：CONDITIONAL GO

## 1. P1 最小闭环

P1 只交付一次“未知产品 Goal 到可体验 Preview，再根据真实 Observation 形成一次迭代决策”的通用闭环：

Goal  
→ Eligibility  
→ Evidence Acquisition  
→ DiscoveryRound  
→ ProductHypotheses  
→ HypothesisDecision  
→ RequirementRevision  
→ CapabilityResolutionPlan  
→ GenerationPlan  
→ FileChangeSet  
→ WorkspaceSnapshot  
→ DependencyResolution  
→ Offline VerifyBuild  
→ ReleaseCandidate  
→ Preview Deployment  
→ Observation  
→ GateEvaluation  
→ CONTINUE、REVISE 或 STOP

AI 从业者 Goal 仅用于端到端验收，不预设 App。

## 2. P1 范围

包含：

- product_creation Goal 资格判断；
- 受 Permit 的来源快照；
- 至少两个有证据的实质候选，或明确 RESEARCH_MORE、BLOCKED、STOP；
- 不可变需求版本；
- 能力解析顺序；
- 可信生成协议；
- 一个 Bootstrap Runtime Profile；
- 依赖解析和离线沙箱构建；
- Preview 发布、查询、对账和回滚；
- 通用指标绑定和一次 Gate 决策；
- 一个真实、事前未知的低风险能力缺口认证。

延后到 P1.1/P2：

- 多 Goal 竞争与抢占；
- Champion/Challenger 平台；
- 完整长期记忆平台；
- 多 Runtime Profile；
- 自动生产发布；
- 支付闭环；
- 动态组织扩展；
- Core 自动升级；
- 通用 Agent 市场。

## 3. 三层架构

### Core Kernel

复用 P0：Goal、Work、Run、Artifact、Evidence、Audit、Outbox、Timer、Permit、HumanTask、Secret、Capability、ToolSpec、Observation。

P1 只新增通用控制面与可信原语：

- Discovery control plane；
- Requirement revision；
- Capability resolution plan；
- Generation control plane；
- WorkspaceWriter；
- long-running command runtime；
- dependency/sandbox ports；
- release/preview control plane；
- gate evaluation。

### Certified Capability Pool

预置并版本化：

- product-discovery-v1；
- app-requirement-v1；
- capability-resolver-v1；
- code-generation-v1；
- python-web-v1 Runtime Profile；
- allowlisted-http-source-v1；
- preview-deployment-v1。

它们通过现有 Capability、ToolSpec 和 Certification 注册，不成为业务领域对象。

### Generated Apps

所有业务代码、页面、数据模型、指标名称和产品流程位于独立 Workspace。Core 与 App 禁止源码依赖和数据库共享。

## 4. 模块布局

新增建议：

- core/src/regent/domain/product_creation.py
- core/src/regent/domain/generation.py
- core/src/regent/domain/delivery.py
- core/src/regent/application/goal_eligibility_service.py
- core/src/regent/application/product_discovery_service.py
- core/src/regent/application/requirement_revision_service.py
- core/src/regent/application/capability_resolution_service.py
- core/src/regent/application/generation_service.py
- core/src/regent/application/build_service.py
- core/src/regent/application/release_service.py
- core/src/regent/application/gate_evaluation_service.py
- core/src/regent/infrastructure/evidence_sources.py
- core/src/regent/infrastructure/workspace_writer.py
- core/src/regent/infrastructure/sandbox.py
- core/src/regent/infrastructure/deployment.py
- core/src/regent/runtime/long_tasks.py
- core/src/regent/api/product_creation.py
- core/src/regent/api/app_delivery.py
- capabilities/bootstrap/

Domain 层不得依赖 FastAPI、SQLAlchemy、Docker 或具体 App。

## 5. 状态机

### DiscoveryRound

REQUESTED → RESEARCHING → READY → DECIDED  
REQUESTED 或 RESEARCHING → BLOCKED  
RESEARCHING → FAILED  
READY → EXHAUSTED

DECIDED、FAILED、EXHAUSTED 为终态。BLOCKED 可在新 Evidence 或授权到达后回到 RESEARCHING。

### RequirementRevision

DRAFT → VALIDATED → SUPERSEDED  
DRAFT → WITHDRAWN  
VALIDATED → WITHDRAWN

content 在创建后不可修改。SUPERSEDED 只表示有后继版本。

### CapabilityResolutionPlan

DRAFT → FROZEN → SATISFIED  
FROZEN → WAITING_HUMAN、BLOCKED 或 FAILED  
WAITING_HUMAN → FROZEN 或 BLOCKED

条目决策顺序固定：REUSE → CONFIGURE → COMPOSE → BUILD → REQUEST_HUMAN → BLOCK。

### GenerationPlan

DRAFT → FROZEN → EXECUTING → COMPLETED  
EXECUTING → FAILED 或 CANCELLED

### GenerationRun

REQUESTED → PLANNING → GENERATING → VALIDATING → COMMITTING → COMPLETED  
任意活动态 → FAILED 或 CANCELLED

### DependencyResolution

REQUESTED → RESOLVING → MATERIALIZED  
RESOLVING → REJECTED、FAILED 或 UNKNOWN  
UNKNOWN 经对账后 → MATERIALIZED 或 FAILED

### AppBuild

QUEUED → RUNNING → PASSED 或 FAILED  
RUNNING → UNKNOWN  
UNKNOWN 经对账后 → PASSED 或 FAILED

RECONCILED 是 Evidence，不是最终状态。

### ReleaseCandidate

DRAFT → READY → APPROVED 或 REJECTED

部署结果从 Deployment 派生，不把 DEPLOYED 写回 ReleaseCandidate。

### Deployment

REQUESTED → DEPLOYING → SUCCEEDED 或 FAILED  
DEPLOYING → UNKNOWN  
UNKNOWN 经对账后 → SUCCEEDED 或 FAILED  
SUCCEEDED → SUPERSEDED 或 ROLLED_BACK

### IterationDecision

不可变值：CONTINUE、REVISE、STOP。REVISE 必须指定唯一主要假设和新 Work。

## 6. 数据模型与迁移

### 0011 discovery

discovery_rounds：

- id、goal_id、round、status、version；
- input_snapshot_hash、budget、policy_version；
- idempotency_key、created_by；
- correlation_id、failure_code；
- created_at、updated_at。

product_hypotheses：

- id、round_id、candidate_key；
- content_json、content_hash；
- eligibility、invalid_reasons；
- generator_ref、created_at。

hypothesis_evidence_refs：

- hypothesis_id、evidence_id、claim_key、relation。

hypothesis_decisions：

- id、round_id、decision；
- selected_hypothesis_id；
- rationale、evidence_digest、policy_version；
- created_by、created_at。

Decision 每轮唯一且不可修改；选择状态由 Decision 派生。

### 0012 requirements and resolution

requirement_revisions：

- id、goal_id、hypothesis_id；
- requirement_key、revision、predecessor_id；
- status、version；
- content_json、content_hash；
- generator_ref、created_by、created_at。

唯一 requirement_key、revision。

capability_resolution_plans：

- id、requirement_revision_id、status、version；
- content_hash、policy_version、created_at。

capability_resolution_items：

- id、plan_id、requirement_key；
- capability_name、gap_type；
- resolution_method；
- capability_id、tool_spec_id；
- status、evidence_refs。

### 0013 generation

generation_plans、generation_runs、file_change_sets、workspace_snapshots。

WorkspaceSnapshot 权威字段是 manifest_uri、manifest_hash、source_archive_uri、source_hash；workspace_path 仅是 locator。

### 0014 build

dependency_resolutions、app_builds、verification_reports。

Dependency bundle、SBOM、构建制品和日志全部使用 Artifact URI 和 hash。

### 0015 release

release_candidates、deployments。

Permit、HumanTask、idempotency_key 和 external_deployment_id 必须建立约束。

### 0016 feedback

metric_definition_bindings、gate_evaluations、iteration_decisions。

Core 保存指标定义与值，不预置指标名称含义。

所有聚合包含 version、correlation_id、created_by、failure_code 和审计。幂等键使用带作用域的唯一约束。

## 7. Generation Protocol

编码实现必须遵守已冻结的 Generation Protocol v1：

- GenerationPlan 绑定所有输入 hash 和生成器版本；
- 模型输出 FileChangeSet，不直接操作文件；
- P1 使用完整文件 CREATE、REPLACE、DELETE；
- WorkspaceWriter 是唯一落盘原语；
- 临时目录应用、校验后原子提交；
- manifest 和 source archive 成为权威 Snapshot；
- 可复现性通过重放冻结变更集保证；
- 不要求再次调用模型逐字生成。

## 8. Evidence Acquisition

- Product Discovery 仅对 product_creation Goal 启动；
- 研究必须引用 SourceSnapshot 或已有 Evidence；
- 模型常识只能是 assumption；
- 外部内容视为不可信数据，检测提示注入；
- Connector 受 Permit、域名、时间窗口、字节和数据分类限制；
- 证据不足时允许 RESEARCH_MORE、BLOCKED 或 STOP。

## 9. Runtime 与 Build

P1 只支持 python-web-v1。它是 ABI，不是产品模板。

依赖阶段：

1. DependencyResolve：受 Permit 联网、白名单源、冻结版本和 hash、生成 bundle 与 SBOM；
2. VerifyBuild：完全断网、非 root、资源受限、无生产 Secret。

不支持的运行时不能偷偷生成 Adapter；必须选择其他候选、HumanTask 或 BLOCKED。

## 10. Long-running Command Contract

现有 Worker 在执行 Generation、Build、Deploy 前必须增加：

- command_type 与 schema_version；
- handler registry；
- 原子 claim；
- lease、heartbeat 和 progress；
- cancellation_requested；
- max_attempts；
- retryable failure 分类；
- next_attempt_at；
- dead-letter；
- external_operation_id；
- reconciliation_required。

Handler 必须幂等。UNKNOWN 外部操作不能被普通重试器再次执行。

## 11. API

所有长任务命令返回 202。

- POST /v1/goals/{id}/discovery-rounds
- GET /v1/discovery-rounds/{id}
- GET /v1/discovery-rounds/{id}/hypotheses
- GET /v1/discovery-rounds/{id}/decision
- POST /v1/hypotheses/{id}/requirement-revisions
- GET /v1/requirement-revisions/{id}
- POST /v1/requirement-revisions/{id}/resolution-plans
- POST /v1/resolution-plans/{id}/generation-plans
- POST /v1/generation-plans/{id}/runs
- GET /v1/generation-runs/{id}
- POST /v1/workspace-snapshots/{id}/dependency-resolutions
- POST /v1/workspace-snapshots/{id}/builds
- GET /v1/app-builds/{id}
- POST /v1/app-builds/{id}/release-candidates
- POST /v1/release-candidates/{id}/approval-requests
- POST /v1/release-candidates/{id}/deployments
- POST /v1/deployments/{id}/reconcile
- POST /v1/deployments/{id}/rollback
- POST /v1/goals/{id}/gate-evaluations
- GET /v1/goals/{id}/iteration-decisions

普通调用者不能直接选择 Hypothesis。自动 Decision 使用冻结策略；人工 override 必须单独治理、说明原因并保留原 Decision。

所有命令包含 actor、idempotency_key、expected_version、correlation_id。

## 12. 开发切片

### S0 文档和生产基础

已冻结 ADR、Generation Protocol、Runtime Profile、Dependency/Sandbox 和 Evidence 协议。并行修复 Docker、HTTPS、鉴权、限流、备份和回滚。

### S1 Discovery Kernel

0011、状态机、schema、差异检查、Evidence 引用校验、长任务契约骨架。

### S2 Discovery Orchestration

受限 Evidence Connector、ProductDiscoveryService、冻结 Decision 策略和 API。

### S3 Requirement and Resolution

0012、需求继承、CapabilityResolutionPlan 和固定解析顺序。先复用 P0 Capability/ToolSpec，不新建平行认证体系。

### S4 Generation Kernel

0013、GenerationPlan、FileChangeSet、WorkspaceWriter、Snapshot 和重放。

### S5 Dependency and Build

0014、两阶段依赖协议、SandboxDriver、Worker handlers、VerificationReport。

### S6 Preview Release

0015、ReleaseCandidate、Preview Provider、Permit、HumanTask、UNKNOWN 对账和回滚。

### S7 Feedback Loop

0016、指标绑定、GateEvaluation、IterationDecision 和创建诊断 Work。只实现 CONTINUE、REVISE、STOP。

### S8 Challenge

只提交 AI 从业者验证契约，完成未知产品 Goal 到 Preview 和一次真实数据决策。

## 13. 第一批编码任务

只有以下任务立即 GO：

A1 领域契约：

- DiscoveryRound、RequirementRevision、GenerationRun、Build、Deployment 状态机；
- 合法、非法、终态、版本冲突测试。

A2 迁移 0011 骨架：

- discovery_rounds、product_hypotheses、hypothesis_evidence_refs、hypothesis_decisions；
- upgrade、downgrade、0010 → 0011。

A3 通用 schema：

- ProductHypothesisProposal；
- HypothesisSelection；
- canonical JSON；
- evidence reference validator；
- constraint inheritance。

A4 Generation schema：

- GenerationPlan；
- FileChangeSet；
- WorkspaceSnapshot；
- 输入 digest。

A5 WorkspaceWriter：

- 路径和 symlink 防逃逸；
- 配额；
- hash；
- 临时目录；
- 原子提交；
- 重放。

A6 Port 和 Fake Adapter：

- EvidenceSourceConnector；
- SandboxDriver；
- DeploymentProvider；
- 测试 fake，不执行真实副作用。

A7 Long Task Contract：

- command envelope；
- handler registry；
- lease/heartbeat/progress；
- retry/dead-letter；
- cancel 和 UNKNOWN。

依赖：

- A1、A2、A3、A4、A6、A7 可并行；
- A5 依赖 A4；
- ProductDiscoveryService 依赖 A1、A2、A3、Evidence Connector；
- Generation Orchestrator 依赖 A4、A5、ResolutionPlan；
- Build 依赖 Snapshot、DependencyResolve 和 Sandbox；
- Release 依赖 PASSED Build 和治理。

## 14. 测试门禁

- Ruff；
- Mypy strict；
- Pytest；
- 空库和 0010 升级；
- Domain 无框架依赖；
- Core 不含 Challenge 业务模型；
- Core 与 App 禁止互相 import；
- 路径、symlink 和配额攻击；
- 提示注入来源；
- Secret 泄漏；
- Build 断网；
- Worker 崩溃恢复；
- Deploy UNKNOWN 对账；
- Permit 和审批拒绝；
- 端到端 Goal 到 Preview；
- Observation 到 IterationDecision。

## 15. 启动结论

CONDITIONAL GO 的条件已经通过本规范与配套契约解除：边界、生成协议、Runtime Profile、依赖策略、Evidence 规则和 P1 范围均已冻结。

A1、A2、A3、A4、A6、A7 可以开始编码，A5 在 A4 合并后开始。ProductDiscoveryService 暂不作为第一提交，必须等待底层状态、Evidence 和长任务契约落地。

生产 Preview 仍为 NO-GO，直到 Docker 不可变构建、HTTPS、鉴权、限流、备份和回滚通过。
