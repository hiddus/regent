# Regent P1 Core 详细开发计划

> 状态：已被技术架构审查废止。最终编码基线为 p1-core-final-technical-spec.md。


> 状态：唯一有效 P1 编码执行基线  
> 日期：2026-07-18  
> 产品基线：P1 Core 功能需求

## 1. 架构决策

Core 继续采用模块化单体、PostgreSQL、FastAPI、SQLAlchemy、Alembic、Outbox、Durable Timer 和 Worker。默认强单 Agent。P1 不把任何 AI 行业 App 方案写入 Core。

业务 App 只能作为 Core 生成的独立 Workspace 存在。Core 与 App 的允许交互：

- Core 向 Workspace 写入生成制品；
- App 通过公共 API 获取批准的配置；
- App 向 Core 提交签名 Observation；
- 部署驱动使用 Release Artifact；
- 双方不得互相 import 源码包；
- 数据库、迁移、Secret 和运行进程独立。

## 2. 通用领域与持久化

### 2.1 ProductHypothesisRecord

字段：

- id、goal_id、round、candidate_key；
- status：PROPOSED、SELECTED、REJECTED、INVALIDATED；
- hypothesis_json；
- evidence_refs；
- score_json；
- content_hash；
- generated_by；
- created_at、decided_at。

唯一约束：goal_id、round、candidate_key。

### 2.2 HypothesisDecisionRecord

字段：

- id、goal_id、round；
- decision：SELECT、RESEARCH_MORE、STOP；
- selected_hypothesis_id；
- rationale；
- evidence_digest；
- decision_policy_version；
- created_at。

每个 Goal 每轮只有一个 Decision。

### 2.3 AppRequirementRecord

字段：

- id、goal_id、hypothesis_id、version；
- status：DRAFT、VALIDATED、SUPERSEDED；
- requirement_json；
- content_hash；
- generator_ref；
- created_at。

唯一约束：goal_id、version。旧版本不可修改。

### 2.4 AppWorkspace

字段：

- id、requirement_id、attempt；
- status：REQUESTED、GENERATING、READY、FAILED、ARCHIVED；
- workspace_path；
- template_ref；
- source_manifest_uri；
- source_hash；
- failure_class；
- created_at、updated_at。

同一 Requirement 同时最多一个活动 Workspace。

### 2.5 AppBuild

字段：

- id、workspace_id、build_number；
- status：QUEUED、RUNNING、PASSED、FAILED、UNKNOWN、RECONCILED；
- source_hash、builder_ref；
- artifact_uri、artifact_hash；
- sbom_uri、test_summary、security_summary；
- limits；
- idempotency_key；
- started_at、finished_at。

### 2.6 ReleaseCandidate

字段：

- id、build_id、version；
- status：DRAFT、READY、APPROVED、REJECTED、DEPLOYED、ROLLED_BACK；
- artifact_uri、artifact_hash；
- release_notes；
- approval_human_task_id；
- created_at、approved_at。

### 2.7 Deployment

字段：

- id、release_candidate_id；
- environment：PREVIEW、PRODUCTION；
- status：REQUESTED、DEPLOYING、SUCCEEDED、FAILED、UNKNOWN、RECONCILED、SUPERSEDED、ROLLED_BACK；
- permit_id、idempotency_key；
- provider_ref、external_deployment_id、url；
- previous_deployment_id；
- started_at、finished_at。

### 2.8 CapabilityBuild 和 CapabilityPromotion

CapabilityBuild 保存需求、源码 Artifact、沙箱限制、测试、安全结果和状态。CapabilityPromotion 保存从 CANDIDATE 到 GOAL_CERTIFIED、VERIFIED 或 REVOKED 的证据和决定。

### 2.9 HandoffRecord

保存发送 Actor、接收 Actor、输入版本、输出 Artifact、验收结果、风险、未解决问题、预算和 Evidence。

产品业务字段只存在于 hypothesis_json、requirement_json 和独立 App，不增加 Core 业务列。

## 3. 通用 schema

### ProductHypothesisProposal

- candidate_key；
- target_users_hypothesis；
- problem_hypothesis；
- value_proposition；
- candidate_solution；
- minimum_validation；
- success_signals；
- failure_signals；
- required_capabilities；
- assumptions；
- unknowns；
- risks；
- estimated_cost；
- estimated_time；
- reversibility；
- evidence_refs。

### HypothesisSelection

- decision；
- selected_candidate_key，可为空；
- comparison；
- rationale；
- missing_evidence；
- next_validation；
- policy_version。

### AppRequirementProposal

- product_outcome；
- target_users；
- problem_statement；
- value_proposition；
- user_journeys；
- functional_requirements；
- non_functional_requirements；
- data_requirements；
- external_integrations；
- success_metrics；
- event_definitions；
- risks；
- governance_requirements；
- release_gates；
- assumptions；
- open_questions；
- source_evidence。

这些是通用产品开发概念，不包含具体行业字段。

## 4. 服务接口

ProductDiscoveryService：

- 接收 Goal、GoalSpec、Evidence 和预算；
- 生成至少两个候选；
- 校验候选实质差异；
- 保存所有候选；
- 根据冻结策略选择、继续研究或停止。

AppRequirementService：

- 只接受 SELECTED hypothesis；
- 生成、校验和 hash；
- 重放相同幂等键返回同一版本；
- 需求变更创建新版本并触发依赖检查。

WorkspaceGenerator：

- 输入冻结 AppRequirement 和能力清单；
- 输出独立 Workspace 与 source manifest；
- 不允许访问 App workspace root 之外路径。

BuildExecutor：

- 只处理冻结 source hash；
- 输出不可变 Artifact、SBOM、测试和安全 Evidence。

DeploymentProvider：

- 根据 Release Artifact 部署、查询、回滚；
- 不接收源码目录；
- 所有调用绑定 Permit 和幂等键。

CapabilityBuilder：

- 只构建低风险候选能力；
- 默认断网和无 Secret；
- 隐藏测试对 Builder 不可见。

ExperimentCoordinator：

- 创建通用产品实验；
- 保存分配、版本、指标定义和归因；
- 不理解具体业务指标含义。

## 5. API

Discovery：

- POST /v1/goals/{goal_id}/product-discovery-rounds
- GET /v1/goals/{goal_id}/product-hypotheses
- GET /v1/product-hypotheses/{id}
- POST /v1/goals/{goal_id}/hypothesis-decisions
- GET /v1/goals/{goal_id}/hypothesis-decisions/{round}

Requirements：

- POST /v1/product-hypotheses/{id}/app-requirements
- GET /v1/app-requirements/{id}
- GET /v1/goals/{goal_id}/app-requirements

Delivery：

- POST /v1/app-requirements/{id}/workspaces
- GET /v1/app-workspaces/{id}
- POST /v1/app-workspaces/{id}/builds
- GET /v1/app-builds/{id}
- POST /v1/app-builds/{id}/release-candidates
- POST /v1/release-candidates/{id}/request-approval
- POST /v1/release-candidates/{id}/deploy
- POST /v1/deployments/{id}/reconcile
- POST /v1/deployments/{id}/rollback

Capability：

- POST /v1/capability-requirements/{id}/builds
- GET /v1/capability-builds/{id}
- POST /v1/capabilities/{id}/promotions
- POST /v1/capabilities/{id}/revoke

所有写接口接收 actor、idempotency_key 和 expected_version。外部副作用 API 只排队，不在请求线程执行。

## 6. 迁移顺序

- 0011_product_hypotheses_decisions；
- 0012_app_requirement_records；
- 0013_app_workspaces_builds；
- 0014_release_candidates_deployments；
- 0015_capability_builds_promotions；
- 0016_handoffs_product_experiments。

每个迁移具备 upgrade、downgrade、约束 DDL 测试、空库升级和从 0010 升级测试。

## 7. 开发切片

### P1-S0 生产工程恢复

- 修复或迁移损坏的 Docker 数据根；
- 依赖层与源码层分离；
- 两次干净构建；
- 镜像摘要、回滚和备份；
- HTTPS、鉴权、限流；
- 停止源码挂载正式运行。

本地 Core 编码可与 S0 并行，生产 Preview 必须等待 S0。

### P1-S1 Product Discovery

- 迁移 0011；
- ProductHypothesisProposal 与 HypothesisSelection；
- 差异检查器，拒绝仅换措辞的候选；
- ProductDiscoveryService；
- 冻结选择策略；
- API、Audit、Outbox、Artifact、Evidence；
- 模型输出纠正重试；
- 预算和 STOP 路径。

验收：

- 任意通用 Goal 至少产生两个实质候选；
- 没有证据的陈述标记为假设；
- 选择引用 Evidence；
- 相同幂等键不重复；
- 测试中不得出现 AI 卡片网站硬编码。

### P1-S2 App Requirement Generation

- 迁移 0012；
- 通用 AppRequirementProposal；
- 需求校验和 canonical hash；
- 版本、SUPERSEDED 和依赖阻塞；
- API 与 Evidence。

验收：

- 只有 SELECTED hypothesis 可生成；
- 原始 Goal 和硬约束保持；
- 新版本不覆盖旧版本；
- schema 不包含任何首个 Challenge 专属字段。

### P1-S3 Capability Planning and Growth

- 通用 CapabilityRequirement 推导；
- Gap 分类；
- 复用、配置、组合、构建决策；
- CapabilityBuild 模型；
- 沙箱接口；
- 公开、隐藏、安全测试；
- Goal 认证、跨 Goal 晋级和撤销。

验收：

- 至少一个非 EVT 固定样例能力可被构建认证；
- 未认证能力不能进入 App Build；
- Builder 看不到隐藏测试和生产 Secret。

### P1-S4 Workspace Generation

- 迁移 0013 的 Workspace 部分；
- WorkspaceGenerator；
- 允许根和安全路径；
- 临时目录生成后原子改名；
- 通用 starter profile 选择；
- 独立依赖、数据库、迁移、测试、Dockerfile；
- source manifest 和 hash；
- 失败恢复和幂等。

验收：

- 多种不同 AppRequirement 可生成不同 Workspace；
- Core 不含 Challenge 分支；
- 目录穿越、符号链接逃逸和部分写入被测试；
- 删除后可从冻结输入重建。

### P1-S5 Sandboxed Build

- AppBuild 模型和迁移；
- Worker handler；
- 非 root、断网、资源限制；
- 依赖锁、SBOM、测试、安全扫描；
- Artifact 和 Evidence；
- 崩溃恢复、UNKNOWN 对账。

验收：

- PASSED、FAILED、UNKNOWN 路径；
- 失败不能创建 Release；
- 构建不读取生产 Secret；
- 同一 source hash 的重放可复用已验证结果。

### P1-S6 Release and Deployment

- 迁移 0014；
- ReleaseCandidate、Deployment 状态；
- Preview Provider；
- HumanTask 和 Permit；
- 幂等、查询、UNKNOWN 对账、回滚；
- Production 默认关闭。

验收：

- 未审批不能生产发布；
- 相同幂等键只有一个外部部署；
- 回滚恢复前一 Artifact；
- 历史发布不可修改。

### P1-S7 Observation, Experiment and Replan

- Observation 绑定 requirement、release 和 metric definition；
- 通用 Gate evaluator；
- Champion/Challenger；
- attribution；
- 诊断 Work 和需求新版本；
- Handoff 和长期记忆；
- 经验过期、晋级、撤销。

验收：

- Core 不理解具体指标名称；
- 内部、Bot 和重放不进入结果；
- 低指标不能自动部署新版本；
- 每轮最多改变一个主要假设。

### P1-S8 AI 从业者 Challenge

只提交外部验证契约。

Core 必须：

- 自主研究和生成候选；
- 自主生成 AppRequirement；
- 推导能力；
- 从空目录生成 App；
- 构建并部署 Preview；
- 收集真实反馈；
- 形成 DecisionRecord。

验收方不得在 Core 内补写具体产品需求或业务流程。

## 8. 测试矩阵

领域：

- 合法、非法、并发和终态转换；
- hypothesis round 和 requirement version；
- Build、Deployment UNKNOWN。

模型：

- schema 缺失纠正；
- 候选重复拒绝；
- 假设与证据混淆拒绝；
- STOP 和 RESEARCH_MORE。

架构：

- Core 不包含首个 App 业务术语模型；
- Core 不 import apps；
- App 不 import regent；
- App 和 Core 数据库隔离。

安全：

- 路径逃逸；
- 构建网络拒绝；
- Secret 泄漏；
- 资源超限；
- 未审批发布；
- 非白名单来源。

恢复：

- Workspace 部分生成；
- Worker 构建崩溃；
- Deploy 超时；
- Outbox 重放；
- Capability 撤销。

端到端：

Goal → Hypotheses → Decision → Requirement → Capability → Workspace → Build → Release → Preview → Observation → Replan。

## 9. 首批可直接编码任务

T1 迁移 0011：

- ProductHypothesisRecord；
- HypothesisDecisionRecord；
- 唯一约束、索引和 downgrade；
- 模型和迁移测试。

T2 通用 schema：

- ProductHypothesisProposal；
- HypothesisSelection；
- canonical serialization；
- 不包含 Challenge 专属字段。

T3 候选差异检查器：

- 用户、问题、价值或验证方式至少一项实质不同；
- 仅换名称或措辞视为重复；
- 失败返回稳定错误。

T4 Product Discovery 状态与服务：

- round、幂等、预算、Audit、Outbox；
- 生成、保存、选择、RESEARCH_MORE、STOP；
- 模型失败恢复。

T5 Discovery API：

- 创建 round；
- 列出候选；
- 读取 Decision；
- 201、200、404、409、502 契约。

T6 Workspace 安全基元：

- workspace root；
- 路径规范化；
- 临时目录和原子提交；
- manifest hash；
- 路径与符号链接安全测试。

依赖：

- T1、T2、T3、T6 可并行；
- T4 依赖 T1、T2、T3；
- T5 依赖 T4。

## 10. 编码启动门槛

已冻结：

- P1 只实现 Core 通用能力；
- AI 从业者仅是验证 Goal；
- 不预设产品、页面、技术栈或商业模式；
- 默认强单 Agent；
- App 业务模型不进入 Core；
- Product Discovery 必须先于 AppRequirement；
- 生产 Preview 等待 S0；
- 首批任务和验收已明确。

结论：T1、T2、T3、T6 可以立即启动编码。
