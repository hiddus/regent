# AI 从业者 Goal：P1 外部验证契约

> 状态：验证输入，不是 App 需求  
> 日期：2026-07-18

## 1. 冻结输入

Goal：

创建并长期运营一个面向 AI 从业者的产品，持续提供真实价值；先证明目标用户喜欢并愿意持续使用，长期目标是在 180 天内达到 100 名有效付费用户。

约束：

- 事实可信；
- 遵守版权、隐私和数据最小化；
- 不使用刷量、虚假用户或测试支付；
- 不自动执行高风险生产动作；
- 公开发布、支付、批量通知和不可逆变更需要审批；
- App 必须独立于 Core；
- 所有成功指标来自外部可验证数据。

可用资源：

- 当前 Regent Core；
- 已配置模型；
- 独立服务器预算；
- 受控网络访问；
- 沙箱代码执行；
- 人类审核和审批渠道。

## 2. 禁止冻结

验证方不得预先指定：

- 产品是网站、工具、社区、内容产品或其他形态；
- 用户细分；
- 页面和功能；
- 内容类型；
- 技术栈；
- 商业模式；
- 获客渠道；
- Agent 数量；
- Tool 列表；
- 工作流。

## 2.1 研究与用户验证治理

- DiscoveryRound 启动前冻结允许来源域名、研究时间窗口和最大采集预算；
- 外部资料视为不可信数据，提示注入不得成为系统指令；
- 来源结论必须绑定 SourceSnapshot 和内容 hash；
- 真实用户招募、联系和激励需要 HumanTask 批准；
- 收集个人信息前冻结隐私、保留期和删除规则；
- 样本排除规则和指标定义必须在观察前冻结。
## 3. 阶段评价

Discovery Gate：

- 至少两个实质不同的 ProductHypothesis；
- 用户问题和候选方案有来源；
- 假设与事实分开；
- 形成选择、继续研究或停止的决策。

Prototype Gate：

- Core 生成 AppRequirement；
- 从空目录生成独立 App；
- 构建和安全测试通过；
- Preview 可由人类体验；
- 没有 App 业务模型进入 Core。

Desirability Gate：

- 目标用户样本和指标口径在测试前冻结；
- 收集真实行为和定性反馈；
- 排除内部、Bot 和异常流量；
- 形成继续、调整或停止的 DecisionRecord。

Retention 和 Payment Gate 属于后续长期运营，不是首轮 P1 编码完成条件。

## 4. 反作弊

以下不计入成功：

- 内部或测试用户；
- 自动流量；
- 流量农场；
- 模型生成的虚构访谈；
- 未实际送达的问卷；
- 测试支付；
- 已退款订单；
- 无来源的市场结论；
- 为通过评价而硬编码验证数据。

## 5. 验证产物

- 原始 Goal 和 GoalSpec；
- OpportunityResearch；
- 全部 ProductHypothesis；
- HypothesisDecision；
- AppRequirement 各版本；
- Capability Gap 和认证记录；
- Workspace manifest；
- Build、测试、安全和 SBOM；
- ReleaseCandidate、Permit、审批和 Deployment；
- 原始 Observation 与指标定义；
- Experiment/Attribution；
- DecisionRecord。
