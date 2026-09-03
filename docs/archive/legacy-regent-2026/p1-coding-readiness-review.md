# Regent P1 编码启动审查

> 状态：已废止。当前基线为 p1-core-capability-requirements.md、p1-core-implementation-plan.md 和 p1-ai-practitioner-validation-contract.md。


> 日期：2026-07-17  
> 结论：Core 首批任务可以启动编码；生产 Preview 发布暂为 NO-GO，直到 P1-S0 完成。

## 1. 产品审查

结论：GO。

已冻结：

- 用户：中文 AI 应用工程师、独立开发者、1–10 人 AI 产品团队；
- 主题：AI Agent 工程与 AI 应用落地；
- 价值：有来源、可复现、有边界的短篇实战卡片；
- 页面：首页、列表、详情；
- 首版：无需登录、无支付、无社区、无自动发布；
- Gate：先可体验，再喜爱度，再持续使用，最后才是付费。

不阻塞编码的外部事项：

- 首批 50 名用户招募渠道；
- 10 名访谈对象；
- 正式域名；
- 首批来源白名单最终列表。

这些必须在 Gate 0 前完成，但不影响 Core T1–T5 开发。

## 2. 架构审查

结论：GO，附边界约束。

通过项：

- 保持模块化单体；
- App 使用独立工程、依赖、数据库、迁移和部署；
- Core 只增加 ProductSpec、Workspace、Build、Release、Deployment 通用对象；
- Card、Feedback、EmailInterest 仅存在于 App；
- App 不导入 Core，Core 不导入 App；
- 默认强单 Agent；
- Build 和 Deployment 使用 Worker、Permit、Artifact、Evidence 和 Audit。

必须新增的架构测试：

1. Core 源码不得出现 Card、Topic、Visitor、Subscription 业务模型；
2. apps/ai-practitioner-web 不得 import regent；
3. Core Workspace 路径只能位于配置的允许根；
4. App 数据库 URL 与 Core 数据库 URL 不同；
5. Build 和 Deploy API 不直接执行外部副作用；
6. Production Deployment 没有 HumanTask 和 Permit 时必须拒绝。

## 3. 技术审查

结论：

- T1、T2、T3、T4、T5：GO；
- 沙箱构建本地实现：GO；
- 生产 Preview：NO-GO，等待 S0；
- 正式公开发布：NO-GO，等待 HTTPS、鉴权、限流、备份和回滚。

已知技术风险：

### R1 Docker 存储损坏

当前生产通过源码只读挂载运行，无法证明不可变镜像交付。

处置：S0 首先迁移或修复 Docker 数据根，在不影响同机其他应用的前提下完成两次干净构建和一次回滚演练。

### R2 Core API 公开且无鉴权

当前 8000 端口可被公网扫描。

处置：Preview 前增加反向代理、HTTPS、管理员鉴权、速率限制和来源限制。Build、Deploy、Permit、HumanTask 管理接口不得匿名访问。

### R3 普通模型执行尚未统一创建 Permit

当前模型执行使用 Run 状态表达许可阶段，真实外部工具必须走 ExecutionPermit 实体。

处置：P1 Builder、SourceConnector、DeploymentProvider 强制使用 Permit；不得复用普通模型执行捷径。

### R4 UNKNOWN 语义必须区分

模型格式失败可安全创建新 Run；外部副作用 UNKNOWN 不能直接重试。

处置：Build/Deploy UNKNOWN 只允许 reconcile，得到权威结果后才能决定新 Run 或回滚。

### R5 内容质量依赖人工审核

首版允许人工审核，不把全自动内容生成作为 Gate 0 前提。

处置：冻结审核契约、责任人和撤回路径；自动发布保持关闭。

## 4. 第一批编码分支范围

### T1 数据模型与迁移

文件范围：

- core/src/regent/infrastructure/models.py
- core/migrations/versions/20260717_0011_product_specs_workspaces.py
- tests/unit/infrastructure/test_models.py
- 新迁移测试

禁止修改 API 和业务 App。

### T2 领域状态

文件范围：

- core/src/regent/domain/product_delivery.py
- core/src/regent/domain/errors.py
- docs/contracts/product-delivery-transitions.md
- tests/unit/domain/test_product_delivery.py

禁止依赖 FastAPI、SQLAlchemy 或 App 业务对象。

### T3 ProductSpec 服务

文件范围：

- core/src/regent/application/product_spec_service.py
- 结构化 schema
- tests/unit/application/test_product_spec_service.py

必须使用现有模型结构纠正重试、canonical JSON hash 和幂等键。

### T4 API

文件范围：

- core/src/regent/api/product_delivery.py
- core/src/regent/api/main.py
- API 测试

只创建和读取状态，不执行文件系统或部署副作用。

### T5 Workspace 安全模块

文件范围：

- core/src/regent/infrastructure/workspaces.py
- tests/unit/infrastructure/test_workspaces.py

必须覆盖绝对路径、父目录跳转、符号链接逃逸、重复创建、部分写入和 manifest hash。

## 5. Definition of Done

每个任务必须：

- 有失败优先测试；
- 有状态和幂等说明；
- 不泄漏 Secret；
- Ruff、Mypy strict、Pytest 通过；
- 迁移有 upgrade、downgrade 和约束检查；
- 新状态写 Audit 与 Outbox；
- 新 Artifact 有 SHA-256 和 provenance；
- 文档与实现一致；
- 不改变 P0 已冻结实验记录和 DecisionRecord。

## 6. 启动结论

可以立即开始：

1. T1 ProductSpec/Workspace 数据模型和迁移；
2. T2 领域状态与转换契约；
3. T5 Workspace 安全路径模块。

T1 与 T2 合并后开始 T3，T3 后开始 T4。

编码过程中不需要再次决定产品定位、首版页面、付费范围、技术栈或 Core/App 边界。生产部署仍需通过 S0 门禁。
