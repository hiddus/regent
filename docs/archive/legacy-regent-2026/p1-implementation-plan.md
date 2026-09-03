# Regent P1 详细开发计划

> 状态：已废止。当前基线为 p1-core-capability-requirements.md、p1-core-implementation-plan.md 和 p1-ai-practitioner-validation-contract.md。


> 状态：编码执行基线  
> 日期：2026-07-17  
> 产品基线：P1 AI 工程实战网站需求

## 1. 技术决策

Core 保持 FastAPI、SQLAlchemy、Alembic、PostgreSQL、Outbox 和 Worker 的模块化单体。P1 不引入微服务、Kafka、Kubernetes、图数据库或通用 Agent DSL。

目标 App 路径为 apps/ai-practitioner-web。首版使用独立 FastAPI、Jinja2、原生 JavaScript/CSS 和独立 PostgreSQL，独立迁移和 Dockerfile。App 不引用 regent 包，只通过 HTTP、Artifact 和签名 Observation 与 Core 交互。

Core 只认识通用 ProductSpec、Workspace、Build、Release、Deployment，不认识 Card、Visitor、Topic、EmailInterest 或 Subscription。

## 2. Core 新对象

ProductSpec：

- id、goal_id、version、status；
- app_id、app_kind；
- spec_json、spec_hash；
- generator_ref、created_at；
- 状态 DRAFT → VALIDATED → SUPERSEDED。

ProductWorkspace：

- id、product_spec_id、status；
- workspace_path、template_ref、source_hash；
- created_at、updated_at；
- 状态 REQUESTED → GENERATING → READY；
- GENERATING → FAILED；READY → ARCHIVED；FAILED → REQUESTED。

AppBuild：

- id、workspace_id、build_number、status；
- source_hash、builder_ref；
- artifact_uri、artifact_hash；
- test_summary、security_summary；
- started_at、finished_at；
- 状态 QUEUED → RUNNING → PASSED、FAILED 或 UNKNOWN；
- UNKNOWN → RECONCILED；
- 同一 Workspace 最多一个活动 Build。

ReleaseCandidate：

- id、build_id、version、status；
- artifact_uri、artifact_hash、release_notes；
- approval_human_task_id；
- 状态 DRAFT → READY → APPROVED 或 REJECTED；
- APPROVED → DEPLOYED；DEPLOYED → ROLLED_BACK。

Deployment：

- id、release_candidate_id、environment、status；
- permit_id、idempotency_key；
- provider_ref、external_deployment_id、url；
- previous_deployment_id、started_at、finished_at；
- 环境 PREVIEW 或 PRODUCTION；
- 状态 REQUESTED → DEPLOYING → SUCCEEDED、FAILED 或 UNKNOWN；
- SUCCEEDED → SUPERSEDED 或 ROLLED_BACK；
- UNKNOWN → RECONCILED。

## 3. Core API

- POST /v1/goals/{goal_id}/product-specs
- GET /v1/product-specs/{id}
- POST /v1/product-specs/{id}/workspaces
- GET /v1/workspaces/{id}
- POST /v1/workspaces/{id}/builds
- GET /v1/builds/{id}
- POST /v1/builds/{id}/release-candidates
- POST /v1/release-candidates/{id}/request-approval
- POST /v1/release-candidates/{id}/approve
- POST /v1/release-candidates/{id}/reject
- POST /v1/release-candidates/{id}/deploy-preview
- POST /v1/release-candidates/{id}/deploy-production
- POST /v1/deployments/{id}/rollback
- POST /v1/deployments/{id}/reconcile
- GET /v1/deployments/{id}

写接口必须接收 actor 和稳定幂等键。Build、Deploy、Rollback 由 Worker 执行，API 只创建状态和任务。

## 4. 驱动接口

WorkspaceGenerator：根据冻结规格生成工作区。  
BuildExecutor：在资源和网络边界内构建。  
DeploymentProvider：部署、查询和回滚。  
SourceConnector：在 Permit 约束下读取来源并返回 Evidence。

首个实现：

- FilesystemWorkspaceGenerator；
- DockerBuildExecutor；
- LocalPreviewDeploymentProvider，随后增加服务器 Docker Provider；
- AllowlistedHttpSourceConnector。

驱动不能读取未绑定 Permit 的 Secret。

## 5. 迁移计划

- 0011_product_specs_workspaces；
- 0012_app_builds_release_candidates；
- 0013_deployments；
- 0014_capability_builds_promotions；
- App 自己的 0001_cards_feedback_events。

每个迁移有 upgrade、downgrade、约束 DDL 测试和生产 dry-run。App 业务表不能进入 Core 数据库。

## 6. 开发切片

### P1-S0 生产构建修复

任务：

- 修复或迁移损坏的 Docker 构建环境；
- Dockerfile 依赖层与源码层分离；
- 镜像标签和摘要；
- Preview 网络与数据卷；
- 数据库备份与恢复演练；
- HTTPS、反向代理、请求大小和速率限制；
- 停止源码挂载作为正式发布。

验收：

- 两次干净构建成功；
- 健康检查通过；
- 能回滚前一镜像；
- 同机其他应用不受影响。

### P1-S1 ProductSpec

- 模型、迁移、状态转换、审计；
- ProductSpecProposal schema；
- 从 Goal 生成并冻结规格；
- 保存模型、token、输入版本和 hash；
- 幂等重放。

验收：普通 Goal 产生有效规格，业务字段不进入 Core 强类型模型，非法转换有稳定错误码。

### P1-S2 Workspace

- WorkspaceGenerator；
- 安全路径和目录穿越拒绝；
- 独立项目骨架；
- 依赖锁、Dockerfile、迁移、健康检查和测试；
- source manifest 与 hash；
- 失败重试与幂等。

验收：从空目录生成 App，Core 不导入 App，删除后能从冻结输入重建。

### P1-S3 沙箱 Build

- Build 状态机、队列和 Worker handler；
- 非 root、默认断网、只读输入、独立输出；
- CPU、内存、磁盘、超时限制；
- 格式、类型、单元、集成、架构测试；
- Artifact、SBOM、测试和安全摘要；
- UNKNOWN 对账。

验收：不合格项目不能生成 Release，构建不能读取生产 Secret，Worker 中断可恢复或对账。

### P1-S4 Release、Preview、回滚

- ReleaseCandidate 和 Deployment 状态机；
- PASSED Build 创建不可变候选；
- HumanTask 审批；
- Preview Provider；
- Production 默认禁用；
- Permit、幂等、UNKNOWN 对账；
- 前版本指针与回滚。

验收：未审批不能生产发布；重复请求只产生一次部署；Preview 可访问；回滚恢复前一摘要。

### P1-S5 网站 MVP

- 独立 App 项目；
- App 自有 Card、FeedbackEvent、InterestLead；
- 首页、列表、详情和辅助页面；
- 筛选、分页、永久链接、SEO；
- 反馈、纠错和邮箱意向；
- 20 张审核内容 fixture；
- 无障碍、移动端、错误页；
- App 健康接口和迁移。

验收：无需登录完成主要体验；业务表不进入 Core；App 脱离 Core 仍能展示已发布内容。

### P1-S6 来源与认证

- 来源白名单和 SourceConnector；
- Permit 绑定域名、方法、配额和数据范围；
- 保存 URL、采集时间、摘要 hash；
- 内容候选生成和人工审核；
- 卡片过期、撤回；
- 禁止自动公开发布。

验收：非白名单拒绝；不可用来源不伪造；未审核不公开；撤回链接展示撤回原因。

### P1-S7 指标回流

- 冻结事件与有效阅读 v1；
- 事件 UUID、防重放、Bot 和内部标记；
- 最少必要数据；
- 服务端签名 Observation；
- Gate 0 报告；
- 低指标只产生诊断建议。

验收：重放不重复；内部与 Bot 不计入；卡片版本、原始事件、聚合值和定义一致。

### P1-S8 用户验证

- 招募与访谈脚本；
- Gate 1 数据；
- 质性反馈编码；
- 继续、调整或停止决策；
- 下一轮只改变一个主要假设。

验收：样本和口径冻结，DecisionRecord 引用可复核 Evidence，不以浏览量替代喜爱度。

## 7. 测试矩阵

- 单元：状态、hash、路径、schema、指标；
- 集成：PostgreSQL、迁移、Outbox、Worker、Artifact；
- 架构：Core 不导入 App，App 不导入 Core；
- API：幂等、版本冲突、权限拒绝、错误码；
- 安全：目录穿越、Secret 泄漏、网络拒绝、资源上限；
- 恢复：Worker 崩溃、Build UNKNOWN、Deploy UNKNOWN；
- 浏览器：桌面、移动端、无 JavaScript 基本浏览；
- 内容：字段、来源、过期、撤回；
- 指标：Bot、内部、重放和版本；
- 发布：未审批拒绝、重复发布、回滚。

全局门禁：Ruff、Mypy strict、Pytest、迁移到 head、架构边界、容器构建、Preview smoke、安全拒绝和恢复测试。

## 8. 任务依赖

S0 → S1 ProductSpec → S2 Workspace → S3 Build → S4 Preview/Release。

S4 之后并行推进 S5 网站、S6 来源、S7 指标；最后进入 S8 用户验证。

S5 页面设计可提前准备，但正式验收必须通过 App Factory 从空目录生成。

## 9. 第一批可编码任务

T1 迁移 0011：

- 修改 models、migration、model tests；
- 建立 ProductSpec、ProductWorkspace 表和约束；
- 验收 DDL、upgrade、downgrade、元数据测试。

T2 领域状态：

- 修改 domain、contracts、domain tests；
- ProductSpec/Workspace 状态、命令、错误码；
- 验收合法、非法和版本冲突。

T3 ProductSpecService：

- Goal 转 ProductSpec；
- 模型结构重试、hash、幂等；
- 固定输入产生合法冻结规格。

T4 ProductSpec API：

- 创建、读取；
- 验收 201、200、404、409 和模型错误。

T5 Workspace 安全路径：

- 新 workspace infrastructure；
- 允许根、路径规范化、原子创建、manifest hash；
- 目录穿越和符号链接逃逸必须拒绝。

T1 与 T2 可并行；T3 依赖 T1/T2；T4 依赖 T3；T5 可与 T1/T2 并行。

## 10. 编码启动门槛

- 用户、首发主题和三个页面冻结；
- 首版无登录、无支付、无自动发布；
- App 技术栈和独立数据库冻结；
- Core 新对象仅为通用基础设施；
- S0 Docker 修复是首要生产任务；
- 发布与来源访问使用 Permit；
- Gate 0、Gate 1 指标冻结；
- 首批任务允许目录与验收明确；
- 动态组织不恢复为默认路径。
