# Regent AAR-1 Foundation 技术实施规范

> 状态：CURRENT / CODING-READY  
> 日期：2026-07-27  
> 产品合同：`Regent-AAR1-PRD.md`  
> 状态真相源：PostgreSQL；Outbox 只负责通知，不另建第二套业务状态  
> 兼容策略：Expand→Dual-write→Backfill→Read-switch→Contract

## 1. 架构决定

1. 沿用模块化单体、PostgreSQL、Outbox、Worker Lease、Permit 和 ExternalOperation。
2. 不立即引入 Temporal/LangGraph 作为业务状态真相、Kafka、Neo4j 或 RL。
3. C/V/R 先硬过滤，U 只比较 FEASIBLE 候选。
4. Organization 是稳定 identity，OrganizationVersion 是不可变状态。
5. A2A Task 是持久业务对象，Outbox 是变更通知；投递为至少一次，消费端幂等，不承诺 exactly-once。
6. MCP 只做协议 Adapter，副作用复用现有 ExternalOperation。

## 2. Digital Constitution 与 Policy

### 2.1 数据模型

```text
constitutions(id, scope_type, scope_id, name, status)
constitution_versions(
  id, constitution_id, version, status, rules_json, content_hash,
  effective_from, effective_until, created_by, approved_by, created_at
)
policy_bindings(
  id, constitution_version_id, subject_type, subject_id,
  goal_id, priority, valid_from, valid_to
)
policy_evaluations(
  id, constitution_version_id, decision_point,
  subject_type, subject_id, action, resource,
  input_snapshot_json, input_hash, outcome,
  matched_rule_ids, obligations_json, reason_codes,
  evaluator_version, correlation_id, causation_id, created_at
)
```

`constitution_versions` 对 `(constitution_id, version)` 和 `(constitution_id, content_hash)` 唯一。PolicyEvaluation 只追加写。

### 2.2 规则与优先级

DSL v1 只允许确定性字段：

```yaml
id: production-release-approval
decision_point: RELEASE
effect: REQUIRE_PERMIT
subject:
  role_in: [release-operator]
action:
  equals: deployment.production
resource:
  risk_tier_gte: HIGH
obligations:
  approver_role: owner
```

效果：`ALLOW | DENY | REQUIRE_PERMIT | REQUIRE_HUMAN`。优先级为 SYSTEM→ORG→PROJECT→GOAL 逐层求交；任意 DENY 胜出。缺版本、缺输入或执行异常均 DENY。

必须覆盖的 decision point：

```text
GOAL_CONFIRM, ORG_CANDIDATE_ADMISSION, ORG_ACTIVATION,
AGENT_CERTIFICATION, AGENT_DEPLOYMENT, A2A_DELEGATION,
MCP_TOOL_DISCOVERY, MCP_TOOL_INVOKE, EXTERNAL_EFFECT_PREPARE,
RELEASE, MEMORY_PROMOTION
```

## 3. Organization Engine

### 3.1 决策管线

```text
Goal/Constraint/Governance/Resource/State Snapshot
→ CandidateGenerator(certified templates only)
→ FeasibilityFilter(C,V,R; UNKNOWN=FAIL)
→ UtilityEvaluator(FEASIBLE only)
→ OrganizationDecision
→ transactional OrganizationVersion activation
```

### 3.2 数据模型

```text
organization_templates(
  id, name, semantic_version, topology_json, status, content_hash
)
organization_decisions(
  id, goal_id, previous_organization_version_id, goal_spec_id,
  constitution_version_id, resource_snapshot_id, state_snapshot_id,
  utility_policy_version, selected_candidate_id, trigger,
  status, decision_json, created_by, created_at
)
organization_candidates(
  id, decision_id, template_id, topology_json, required_resources_json,
  status, generation_method, generator_version
)
organization_candidate_checks(
  id, candidate_id, check_type, result, policy_evaluation_id,
  reason_codes, evidence_refs, snapshot_hash
)
organizations(id, goal_id, current_version_id, status)
organization_versions(
  id, organization_id, version, predecessor_id, decision_id,
  topology_json, status, activated_at, retired_at
)
```

`organizations.goal_id` 保持唯一；可变拓扑移入 Version。激活必须在单事务中锁定 Organization、复核快照和 Policy、替换 ACTIVE Version、更新 current_version_id，并写 Audit + Outbox。

### 3.3 Utility v1

```text
predicted_utility =
  Σ(weight_j × normalized_component_j)
  - uncertainty_penalty
```

维度为 success、cost、latency、human burden、residual operational risk、explainability。安全、权限、预算硬边界不进入软评分。

必须保存 `utility_policy_version`、原始量纲、归一化函数/上下界、非负且和为 1 的权重、缺失值策略、预测区间和训练数据版本。`predicted_utility` 与运行后的 `realized_utility` 不得互相覆盖。

数据不足时使用明确标记的 `HeuristicUtilityV1`，默认选择单 Agent。平分顺序：更低预测成本→更少 Agent→template ID 字典序。

## 4. Agent Manifest、Lifecycle 与 Relationship

### 4.1 Manifest v1

```json
{
  "schema_version": "agent-manifest/v1",
  "identity": {"name": "qa", "version": 1, "role": "reviewer"},
  "scope": {"goal_id": "...", "organization_version_id": "..."},
  "capabilities": [{"name": "delivery-review", "version": "1"}],
  "tools": [{"tool_ref": "...", "allowed_actions": ["read"]}],
  "permissions": {"allow": [], "require_permit": [], "deny": []},
  "memory_scopes": ["RUN", "GOAL"],
  "kpis": [],
  "runtime": {
    "runtime_profile_id": "...",
    "model_ref": "...",
    "max_turns": 30,
    "max_tokens": 100000,
    "max_wall_seconds": 3600
  },
  "delegation": {"max_depth": 1},
  "constitution_version_id": "..."
}
```

新增 `agent_spec_versions`、`agent_deployments`、`agent_lifecycle_events`、`agent_relationships`。Manifest 使用 canonical JSON SHA-256。

Relationship v1 为封闭枚举：

```text
SUPERVISES, DELEGATES_TO, DEPENDS_ON, REVIEWS,
APPROVES, ESCALATES_TO, SHARES_MEMORY_WITH
```

不变量：Suspended/Retired Deployment 不接新任务；Producer 与最终 Reviewer/Approver 不同；关系属于同一 OrganizationVersion；APPROVES 不授予执行能力。

## 5. Durable A2A 与 Envelope

### 5.1 AgentTask

```text
agent_tasks(
  id, protocol_version, goal_id, work_id, organization_version_id,
  source_deployment_id, target_deployment_id, parent_task_id, task_type,
  capability_scope, permit_refs, payload_ref, payload_digest,
  idempotency_key, correlation_id, causation_id,
  status, attempt, max_attempts, not_before, deadline_at,
  lease_owner, lease_token, lease_expires_at,
  result_ref, error_code, created_at, updated_at
)
UNIQUE(target_deployment_id, idempotency_key)
```

状态：

```text
CREATED → OFFERED → ACCEPTED → RUNNING → SUCCEEDED
                                  ├→ FAILED_RETRYABLE → OFFERED
                                  ├→ FAILED_TERMINAL
                                  ├→ UNKNOWN → RECONCILING/MANUAL_REVIEW
                                  ├→ TIMED_OUT
                                  └→ CANCELLED
```

命令：`offer_task`、`claim_task`、`heartbeat`、`start_task`、`complete_task`、`fail_task`、`cancel_task`、`reconcile_task`。所有状态更新必须校验 lease token/fencing。Lease 过期可重新 claim；远端已调用但结果不明时不得盲重发。

### 5.2 Envelope v1

字段：

```text
schema_version, message_id, issued_at, expires_at, nonce,
goal_id, organization_version_id,
source_deployment_id, target_deployment_id,
capability_scope, permit_refs,
payload_ref, payload_digest, idempotency_key,
correlation_id, causation_id, signing_key_id, signature
```

序列化采用 RFC 8785 风格 canonical JSON，摘要 SHA-256，同集群签名 HMAC-SHA256。必须拒绝篡改、过期、nonce 重放、scope 扩大和未知 key id。

## 6. Governed MCP

```text
Discovery
→ Server/Tool certification and schema hash
→ PolicyEvaluation
→ Capability scope
→ risk classification
→ Permit/HumanTask when required
→ read-only audited invoke
  or side-effect ExternalOperation prepare/dispatch/reconcile
```

新增 `mcp_servers`、`mcp_tool_bindings`、`mcp_invocations`。Server 状态为 DISCOVERED/CERTIFIED/SUSPENDED/REVOKED；副作用分类为 NONE/REVERSIBLE/IRREVERSIBLE。Secret 只存 Broker 引用。工具输出始终为 UNTRUSTED_DATA。

## 7. 重组、防振荡与 KPI

触发器仅限：CAPABILITY_GAP、RESOURCE_CHANGE、POLICY_CHANGE、ATTRIBUTABLE_FAILURE、KPI_DEVIATION、MANUAL。默认连续 2 次可归因失败才触发；切换最小驻留 30 分钟、冷却 15 分钟、每 Goal 自动重组最多 3 次，并要求：

```text
predicted_utility(new) - predicted_utility(current) > epsilon
```

默认 `epsilon=0.05`，切换成本计入评分。同一原因连续两次重组仍失败则停止自动扩张并创建 HumanTask。

KPI 状态：

```text
INSUFFICIENT_DATA → BASELINE → IMPROVING/STABLE/DEGRADING
→ TARGET_SUSTAINED
```

`TARGET_SUSTAINED` 默认要求连续 4 个完整业务窗口达标且 95% 区间不越界；退化后可退出。数据源、公式、单位、窗口、分母、迟到策略、季节分层和 owner 必须版本化。

## 8. 兼容与迁移批次

### M1 Expand

新增 Constitution/Policy、Organization Version/Decision/Candidate/Snapshot、Agent SpecVersion/Deployment/Relationship、AgentTask、MCP 表；为现有 Organization 回填 Version 1。迁移可重复执行并提供 count/hash 校验。

### M2 Dual-write + Shadow

现有 `organize()` 双写；Policy 与新 Organization Engine 只影子判定。要求双写差异为 0、影子错误全部有解释。

### M3 Read-switch

Receipt 追加 `organization_version_id`、`decision_id`、`constitution_version_id`；Orchestrator 从 Version/Deployment 读取；旧 AgentMesh facade 转发 Durable Service。

### M4 Enforce

Constitution/Policy fail-closed；组织激活强制 C/V/R checks；副作用 MCP 强制 Permit + ExternalOperation。Durable A2A 故障注入通过后关闭生产内存路径。

### M5 Contract

停止写旧可变字段，移除旧内存 Task store 和旧状态适配。Contract 必须独立发布、可回滚，不与 M1 合并。

## 9. API 与错误合同

新增 `/v2`：

```text
POST /v2/constitutions/{id}/versions
POST /v2/policy-evaluations
POST /v2/organizations/{goal_id}/decisions
POST /v2/organizations/{id}/versions/{version}/activate
POST /v2/organizations/{id}/rollback
POST /v2/agent-tasks
POST /v2/agent-tasks/{id}/claim|heartbeat|complete|fail|reconcile
GET  /v2/agent-tasks/{id}
POST /v2/mcp/servers/{id}/certify
POST /v2/mcp/tools/{id}/invoke
```

写接口强制 `Idempotency-Key`。错误至少包括：

```text
NO_ACTIVE_CONSTITUTION, POLICY_DENIED, POLICY_EVALUATION_FAILED,
NO_FEASIBLE_ORGANIZATION, STALE_ORGANIZATION_VERSION,
INVALID_AGENT_LIFECYCLE_TRANSITION, CAPABILITY_SCOPE_ESCALATION,
STALE_LEASE, ENVELOPE_TAMPERED, ENVELOPE_EXPIRED,
ENVELOPE_REPLAYED, MCP_SERVER_NOT_CERTIFIED, EXTERNAL_EFFECT_UNKNOWN
```

## 10. 测试与完成定义

必须通过：

1. Constitution 规则优先级、版本、撤销、时间窗口、fail-closed；
2. 高效用但 C/V/R 不可行候选被淘汰，UNKNOWN 不放行；
3. 相同输入确定重放、tie-break 稳定；
4. OrganizationVersion 并发激活仅一个 ACTIVE，rollback 不改历史；
5. Agent 生命周期非法迁移、职责分离、scope 子集属性测试；
6. Envelope 跨进程摘要一致，篡改/过期/replay 拒绝；
7. A2A 在 offer/claim/start/dispatch/complete 六个崩溃窗口恢复；
8. Lease 过期和 stale worker fencing；
9. MCP schema 漂移、Server 撤销、恶意 Tool output；
10. 副作用重复请求、超时、UNKNOWN、对账和人工接管；
11. Expand/Dual-write/Backfill/Read-switch/Rollback 迁移测试；
12. 跨 org/project/goal 越权 100% 拒绝；
13. P2-4 使用 task-level 配对分析评测 single/fixed-multi；
14. 定义冻结、架构边界、Ruff、mypy、Pytest、Alembic 全绿。

任何模拟 A2A/MCP、源码字符串检查、类存在或 LLM 自评均不能满足上述行为验收。
