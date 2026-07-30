# Regent AAR-1 Foundation 产品需求文档

> 状态：CURRENT  
> 日期：2026-07-27  
> 定义源：`docs/definitions/REGENT-DEFINITION-1.0.txt`（不修改、不复制）  
> 继承：`Regent-PRD-v2.md`；冲突时本文件仅对 AAR-1 Foundation 范围优先  
> 技术合同：`Regent-AAR1-Technical-Spec.md`  
> 测量合同：`Regent-Measurement-Decision-Framework.md`  
> 编码门禁：`docs/AAR1CodingReadinessDecisionRecord.json`

## 1. 产品决定

AAR-1 将 Regent 从“可治理单 Agent 内核 + 原型多 Agent”升级为“可审计、可恢复、可版本化、可实验的组织运行基础”。

本阶段拆分两个门禁：

- `BUILD_ALLOWED`：允许建设宪章、策略、组织版本、Agent 生命周期、持久消息和受治理 MCP 基础设施。
- `ROLLOUT_ALLOWED`：允许自适应组织成为生产或默认策略。

本文件批准 `BUILD_ALLOWED`，不批准 `ROLLOUT_ALLOWED`。后者必须由 P2-4 正净收益 `DecisionRecord` 单独激活。当前默认组织仍为强单 Agent。

## 2. 用户与核心任务

| 用户 | 必须完成的任务 |
|---|---|
| Goal Owner | 查看 GoalSpec、适用宪章、约束、候选组织、选择理由和需审批事项；可冻结、暂停、恢复、取消 |
| Operator | 查看当前组织版本、Agent 状态、消息积压、资源与阻塞原因；可暂停、人工接管、回滚 |
| Approver | 基于动作、风险、作用域、幂等键、规则和证据授予一次性批准；子 Agent 不得扩大授权 |
| Auditor | 重放输入快照→政策判定→候选过滤→评分→组织→委托→外部效果→验收的因果链 |
| Platform Admin | 注册、认证、暂停、撤销 Agent、Tool、MCP Server 和 Runtime Profile |
| Evaluator | 使用冻结任务集盲评强单 Agent 与固定多 Agent；生成者不能评价自身产物 |

## 3. 必须范围

### 3.1 Foundation

1. 版本化 Digital Constitution 与确定性 Policy Evaluation。
2. OrganizationCandidate、FeasibilityCheck、Decision、Version 与回滚。
3. 绑定 Goal/Constraint/Governance/Resource/State 快照。
4. Agent Manifest、Spec Version、Deployment 生命周期和时态 Relationship。
5. Durable AgentTask/Envelope；至少一次投递、幂等消费、Lease、重试、Dead Letter、UNKNOWN 对账。
6. MCP 作为受治理 Adapter；副作用复用 Permit + ExternalOperation。
7. 全链路不可变审计和可重放决策。

### 3.2 Validation

首版只提供两个认证模板：

- `single-agent-v1`
- `pm-dev-independent-qa-v1`

必须验证重启恢复、重复投递、权限只减不增、职责分离、Permit、UNKNOWN 对账、组织回滚，以及 P2-4 对组织指标的消费能力。

## 4. 功能需求

| ID | 需求 |
|---|---|
| FR-AAR-001 | Goal freeze 时固定 `constitution_version_id`；版本不可原地修改，新版本不自动改变在途 Goal |
| FR-AAR-002 | 组织准入、委托、工具调用和外部效果均产生 `PolicyEvaluation`：ALLOW、DENY、REQUIRE_PERMIT 或 REQUIRE_HUMAN；DENY fail-closed |
| FR-AAR-003 | 决策保存全部候选、逐项 C/V/R 判定、资源/状态快照、预测效用分项、区间和选择理由 |
| FR-AAR-004 | Foundation 仅允许认证模板生成可执行候选；LLM 只能产生待验证草案 |
| FR-AAR-005 | 组织变化新建 `OrganizationVersion`；历史不可覆盖；回滚也是新 Decision |
| FR-AAR-006 | Agent Manifest 固定 identity、role、capabilities、tools、permissions、memory、KPI、runtime、delegation 和 constitution |
| FR-AAR-007 | Agent Spec：DRAFT→CERTIFIED→SUPERSEDED/REVOKED；Deployment：PENDING→DEPLOYED→OPERATING，可 SUSPENDED/UPGRADING/RETIRED/FAILED |
| FR-AAR-008 | Relationship v1 仅支持 SUPERVISES、DELEGATES_TO、DEPENDS_ON、REVIEWS、APPROVES、ESCALATES_TO、SHARES_MEMORY_WITH |
| FR-AAR-009 | AgentTask 持久化，包含 correlation、causation、idempotency、deadline、attempt、scope、permit 和 payload digest；重启可续 |
| FR-AAR-010 | 子 Agent 有效权限 = 父有效权限 ∩ OrganizationVersion 授权 ∩ GoalSpec/Constitution 允许 ∩ 当前任务所需权限 |
| FR-AAR-011 | 真实 MCP 调用统一经过 Policy、Scope、Permit、ExternalOperation 和 Audit；内存模拟不得作为生产验收 |
| FR-AAR-012 | Artifact 的 Producer 与最终 Reviewer/Approver 不得是同一 AgentDeployment；否则不能 ACCEPT/PUBLISH |
| FR-AAR-013 | 重规划只由冻结触发器产生：资源/政策变化、能力失效、连续可归因失败、KPI 偏离、人工指令 |
| FR-AAR-014 | 无可行候选时返回 `NO_FEASIBLE_ORGANIZATION`，进入 BLOCKED 或经证明后 EXHAUSTED，并保存 infeasibility report |
| FR-AAR-015 | 同时保存 `predicted_utility` 与 `realized_utility`；权重、归一化、缺失值、区间和数据版本均可追溯 |

## 5. 非功能需求

| ID | 要求 |
|---|---|
| NFR-01 | 相同快照、规则、模板和评分器版本必须产生相同过滤与选择；随机探索必须保存 seed/propensity |
| NFR-02 | 在领取、调用前后、提交前后杀 Worker，最终必须收敛且无重复有意义副作用 |
| NFR-03 | UNKNOWN 15 分钟内进入 RECONCILING 或 MANUAL_REVIEW |
| NFR-04 | Decision、Policy、Task、Permit、ExternalOperation 均有不可变 ID、actor、hash 和因果引用；审计缺口直接 Gate fail |
| NFR-05 | 首版强制 org/project/goal scope；跨 scope 默认拒绝；外部输入均为 UNTRUSTED_DATA |
| NFR-06 | 组织决策 p95≤2s（不含 LLM 解释）；消息入队确认 p95≤500ms；10,000 条待处理消息重启恢复≤5min |
| NFR-07 | 积压、重试、死信、Policy deny、预算、组织版本和回滚均可观测、可审计操作 |
| NFR-08 | 采用 Expand→Dual-write→Backfill→Read-switch→Contract；旧 Run/Decision 永远可读 |

容量基线：单部署 20 个并发 Goal、每 Goal 最多 8 个 OPERATING Agent、每 Agent 最大委托深度 3、消息 payload 最大 256 KiB（更大内容使用 Artifact 引用）、持续 50 msg/s、突发 200 msg/s。

## 6. 产品与安全决策

| 决策 | 冻结结论 |
|---|---|
| 规范源 | 继续使用 `REGENT-DEFINITION-1.0`；AAR-1 不改变产品定义，因此不新建 Definition 2.0 |
| 宪章优先级 | SYSTEM→ORG→PROJECT→GOAL 逐层收窄；任意层 DENY 绝对优先；低层不得放宽高层规则 |
| 租户模型 | AAR-1 使用 `org_id/project_id/goal_id`；现有 `org_key` 仅为资源账簿键，不代表安全身份 |
| 组织身份 | 每 Goal 一个稳定 Organization identity；所有变化进入不可变 Version |
| 候选空间 | 只允许认证模板；动态自由拓扑延后至 ROLLOUT Gate |
| 在途语义 | 已 DISPATCHED Run 在旧版本安全完成；新 Work 绑定新版本；合并需 compatibility check |
| 宪章更新 | 不热改在途 Goal；迁移需 Owner 批准并产生新 GoalSpec/OrganizationVersion |
| A2A 范围 | 首版支持同集群跨 Worker 的持久消息；公网/跨组织 federation 延后 |
| MCP 范围 | 首批必须包含一个只读工具和一个副作用工具；Server/Tool 必须 allowlist + certification |
| Agent identity | AgentSpec 是稳定定义身份；AgentSpecVersion 是不可变定义；AgentDeployment 是运行身份 |
| 签名 | 同集群 Envelope v1 使用轮换 HMAC-SHA256；跨组织升级为 Ed25519 |
| 保留 | 审计/Decision 长期保留；Task payload 30 天后仅留 digest 与 Artifact ref；敏感数据按删除请求做加密擦除并保留删除审计 |
| Retire | Deployment RETIRED 不可恢复；恢复须创建新 Deployment |
| Rollback | Critical 事件立即回滚；统计退化须由冻结 Gate 触发；Owner 可执行有审计的紧急强制切换 |

## 7. 科学表述边界

- `argmax` 仅表示在本次已生成且通过硬约束的有限候选集 `F_t` 上，按冻结评分器选择最高预测效用者：

```text
O_hat = arg max_{O ∈ F_t} U_hat(O | X_t)
F_t = {O | C(O)=PASS ∧ V(O)=PASS ∧ R(O)=PASS}
```

- 当前启发式分数不是校准成功概率，不得宣称全局最优。
- “长期收敛”在验收中表示 KPI 在连续完整窗口达到并维持目标区间，不宣称数学极限收敛。
- 统计推断单位是 `task_id` 或业务 Goal；同任务重复运行只估计噪声，不能虚增样本量。

## 8. 验收门禁

| Gate | 验收 |
|---|---|
| D0 文档统一 | 权威索引、状态、优先级、定义 hash、BUILD/ROLLOUT 门禁无冲突 |
| D1 合同冻结 | Schema、状态机、错误、事件、幂等、权限、效用、模板、迁移和回滚均有唯一答案 |
| F1 Constitution | 规则允许/拒绝/需审批均可重放；Prompt/Tool output 不能覆盖政策 |
| F2 Durable Hive | PM→Dev→独立 QA；至少六个崩溃点恢复；重复投递无第二副作用；Dead Letter 可恢复 |
| F3 Organization | 至少两个候选；高效用但 C/V/R 失败者被淘汰；回滚产生新版本 |
| F4 Security | 权限扩大与跨 Goal 访问 100% 拒绝；所有副作用有 PolicyEvaluation，需 Permit 动作有一次性 Permit |
| E1 Eval Readiness | 同模型、预算、任务和验收条件下盲评 single/fixed-multi，生成正式 Eval DecisionRecord |
| R1 Rollout | 仅 Measurement Framework 全绿才开放 P2-5；否则保持 single-agent 默认 |

## 9. 非目标

- 自由生成任意组织、强化学习或自动修改效用权重；
- 自动修改 Constitution、无审批生产发布；
- Agent Marketplace、跨企业公网 A2A、设备 Agent 网络；
- 为架构完整性强制引入 Temporal、Kafka、Neo4j、Ray、Dapr 或 Kubernetes；
- 把类/表/API 存在、模拟 A2A/MCP、投票、LLM 自评或内部流量当作验收；
- 承诺多 Agent 必然优于单 Agent。
