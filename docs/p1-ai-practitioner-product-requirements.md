# Regent P1 产品需求：AI 工程实战网站

> 状态：已废止。当前基线为 p1-core-capability-requirements.md、p1-core-implementation-plan.md 和 p1-ai-practitioner-validation-contract.md。


> 状态：编码基线  
> 日期：2026-07-17  
> 产品目标：验证 Regent 能否从普通 Goal 创建、发布并运营一个目标用户喜欢且愿意回访的独立网站。

## 1. 冻结决策

P1 采用“强单 Agent + 受治理能力工厂”，不依赖动态组织。首个产品是面向中文 AI 应用工程师、独立开发者和 1–10 人 AI 产品团队的“AI 工程实战卡片”网站。

首发主题：AI Agent 工程与 AI 应用落地。首版不做泛 AI 新闻、社区、招聘、广告和真实支付。

100 名有效付费用户是长期 Goal A，不是 P1 完成条件。P1 先验证用户是否愿意阅读、是否认为内容有帮助、是否愿意再次回来。

## 2. 用户与价值

目标用户：

- 开发 AI 应用的中文工程师；
- 选择模型、框架或工程方案的小团队负责人；
- 需要可复现实践而非新闻摘要的独立开发者。

价值主张：用户用 3–8 分钟读完一张有来源、有环境、有步骤、有结果和边界的卡片，并决定是否值得在项目中尝试。

## 3. 首版体验

三个核心页面：

1. 首页：价值主张、精选卡片、主题入口、更新订阅；
2. 卡片列表：主题、模型、工具、难度、更新时间筛选；
3. 卡片详情：结论、场景、环境、步骤、证据、限制、来源、反馈。

辅助页面：关于、内容方法、隐私、来源与纠错政策、错误页。

无需登录即可浏览、筛选、分页、分享、反馈、提交纠错和自愿留下更新邮箱。

首版明确不做：账号体系、收藏、个性化、评论、私信、UGC、真实收费、自动营销、自动公开发布、用户环境命令执行、动态多 Agent 展示和未授权抓取。

## 4. 卡片契约

字段：

- slug、title、one_line_conclusion；
- target_audience、problem；
- applicable_scenarios、not_applicable_scenarios；
- environment：模型、框架、运行时和版本；
- steps、result_summary；
- evidence_refs、source_refs；
- limitations、risk_level、difficulty、estimated_minutes；
- topic_tags；
- verification_status、verified_at、expires_at；
- content_version、reviewed_by、published_at。

状态：

DRAFT → REVIEW_REQUIRED → APPROVED → PUBLISHED  
PUBLISHED → STALE 或 RETRACTED  
STALE → REVIEW_REQUIRED

规则：

- 没有来源和验证时间不能发布；
- 代码或命令必须说明环境与风险；
- 来源撤回、结论失效或严重纠错时必须撤回；
- 发布、撤回均是受治理副作用；
- 首版 20–30 张卡片允许人工提供来源和审核，Regent 辅助生成。

## 5. 产品指标

事件：page_view、card_impression、card_open、evidence_expand、effective_read、helpful_vote、not_helpful_vote、practiced_vote、share_click、email_interest_submit、correction_submit。

事件必须包含事件 ID、会话 ID、卡片版本、来源页面、时间、内部标记、Bot 标记和指标定义版本。

有效阅读 v1：

- 打开详情；
- 停留至少 45 秒；
- 滚动达到 60%，或展开至少一项证据；
- 排除内部、Bot、重放和异常流量。

Gate 0：

- 网站公开预览可用；
- 至少 20 张审核卡片；
- 三个核心页面通过桌面与移动端测试；
- 反馈、订阅意向和指标链路可用；
- 严重安全、隐私和版权事件为 0。

Gate 1：

- 至少 50 名符合画像的外部访客；
- 至少 30% 产生有效阅读；
- 至少 15 人反馈或留下更新意向；
- 至少 10 次“有帮助”或“我实践过”；
- 至少 10 次结构化访谈；
- 形成继续、调整或停止的 DecisionRecord。

Gate 2 持续使用和后续付费不属于首版编码门槛。

## 6. Core 生成证明

必须证明：

1. 普通 Goal 生成版本化 ProductSpec；
2. 从空目标目录创建独立 Workspace；
3. 生成代码、迁移、测试和部署清单；
4. 隔离构建并执行测试；
5. 生成可审阅 ReleaseCandidate；
6. 人工批准后部署 Preview；
7. 正式发布需要新的 Permit 和 HumanTask；
8. 用户事件作为签名 Observation 回流；
9. Core 不包含卡片、访客或订阅业务模型；
10. 删除生成目录后可由冻结输入重新生成等价项目。

允许复用通用网站模板、UI 组件和构建工具；禁止在 Core 内为该 App 写隐藏业务分支。

## 7. 安全与治理

- 构建默认断网，依赖使用固定源和锁文件；
- 构建容器非 root、无生产 Secret、资源受限；
- Preview 和 Production 凭证隔离；
- 发布、回滚、内容撤回使用 Permit；
- 正式发布必须人工批准；
- 外部来源必须白名单并保存 URL、时间和摘要哈希；
- 邮箱只能用于明确同意的更新；
- 日志不能记录模型 Key、部署凭证或完整邮箱。

## 8. P1 完成定义

1. App Factory 能从 Goal 创建独立 Workspace；
2. Build、ReleaseCandidate、Preview Deployment 可追溯；
3. 网站由该流程从空目录生成；
4. 至少 20 张审核卡片和三个核心页面；
5. 指标签名、幂等并回流 Observation；
6. 发布与回滚经过 Permit 和审批；
7. 自动、边界和安全测试通过；
8. Gate 0 通过；
9. Gate 1 完成并产生 DecisionRecord。
