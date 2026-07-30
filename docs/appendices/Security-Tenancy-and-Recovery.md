# 附录 3：Security, Tenancy and Recovery

> 状态：CURRENT  
> 配套：`Regent-Technical-Spec.md`、`Regent-PRD.md` §7  
> 日期：2026-07-22（二次复审修订）

## 1. UNTRUSTED_DATA 元数据（强制）

每条不可信内容入库/入模前必须携带：

| 字段 | 说明 |
|---|---|
| `trust_label` | 恒为 `UNTRUSTED_DATA`（除非控制面签发） |
| `source_uri` / `source_agent_id` | 来源 |
| `content_hash` | 规范内容哈希 |
| `parser_version` | 解析器版本 |
| `injection_site` | 注入位置：evidence / tool_output / memory / agent_message / user_paste / app_output |
| `retrieved_at` | 时间 |

规则：只能作为数据；不得成为指令、授权或策略来源；要求改评价器/Permit/成功标准 → fail-closed + 审计。

## 2. Prompt Injection：按阶段强制（不可抽样跳过）

| 阶段 | 强制用例 |
|---|---|
| G0 / P1 | PI-1 间接、PI-2 Tool-output、PI-5 外泄、PI-7 篡改评价器/Permit |
| P2-3 Memory | **PI-3 Memory-delayed**（强制，不得抽样跳过） |
| P2-5 多 Agent | **PI-4 Agent-to-Agent**（强制） |
| 全阶段合并门禁 | 上表对应阶段套件必须绿；编码/混淆变体至少各 1 |

## 3. Memory 闭环（实现级）

```text
Admission → Retrieval → Usage Trace → Impact Graph → Revocation → Revalidation
```

| 主题 | 规则 |
|---|---|
| 准入 Guard | 来源可验证、作用域合法、无循环证据边、冲突策略明确（拒绝/并列 CANDIDATE） |
| 冲突 | 同键冲突 → 保留最高可信或全部 CANDIDATE；不得静默覆盖 VERIFIED |
| 衰减 | 过期 → EXPIRED；置信度随时间衰减公式入版本 |
| 隔离 | org/project/goal 检索边界；Worker 必须带 tenant context |
| 批量撤销 | 按 source / parser_version / 时间窗批量 REVOKED |
| 循环证据 | Impact Graph 建边前检测环；发现则拒绝准入 |
| 图一致性恢复 | 定期校验边两端存在；孤儿边修复任务 |
| 重验证期间下游 | 派生 Decision/候选标 `REVALIDATION_REQUIRED`；禁止用于新的 PASSED Gate |

## 4. 租户隔离（冻结）

首发：`tenant_id` 单例 + `org_id` / `project_id` / `goal_id`。

| 控制点 | 要求 |
|---|---|
| Worker tenant context | 每个 Job 绑定 org；越权查询失败 |
| 组织级唯一键 | `(org_id, natural_key)` 唯一；禁止跨 org 碰撞复用 |
| Artifact 隔离 | 路径前缀含 org；签名 URL 短时、绑定 org |
| 审计防篡改 | 审计追加写；哈希链或 WORM 存储（生产）；应用角色无 UPDATE/DELETE |
| Webhook replay | 签名 + timestamp 窗 + 事件 ID 幂等 |
| SSRF / 元数据地址 | Egress 拒绝 link-local、云元数据 IP、内网默认段；白名单 DNS |
| 跨租户测试 | CI 负例：org A token 读 org B → 401/404 |

## 5. 数据生命周期矩阵

| 数据类 | 可变性 | 保留 | 删除/导出 | 与审计关系 |
|---|---|---|---|---|
| 会话草稿 | 可变 | 90 天 | 可删 | 删后审计留「删除回执」 |
| Observation（产品） | 不可变内容 | 400 天默认 | 导出全文；删除→墓碑+匿名化指标 | 审计保留 |
| Evidence Snapshot | 不可变 Artifact | 400 天 | 同左 | 审计保留哈希 |
| Audit / Permit / EO | 追加不可变 | **≥730 天** | **不可产品删除**；仅法定流程 | 本身即审计 |
| Memory CANDIDATE | 可撤 | 180 天 | 撤销优先于物理删 | 撤销审计 |
| 日志 | 可变滚动 | 90 天 | 滚动删除 | 安全事件提升保留 |

「隐私删除」**不得**擦除不可变审计/证据哈希；对个人标识做tombstone/匿名化，证据完整性保留。

## 6. 恢复目标（冻结，取消草案）

| 指标 | 目标 | 演练 |
|---|---|---|
| RPO（PostgreSQL） | **≤ 15 分钟** | 每季恢复演练 |
| RTO（控制面 API） | **≤ 2 小时** | 每季 |
| Artifact 丢失 | 多副本；丢失可从备份恢复 ≤ 24h | 每年 |
| 区域级灾难 | 首发单区域；跨区为候选 | — |

## 7. anti-Sybil / 重放 / 异常

- Observation：签名、事件 ID 幂等、归因主体；
- 短窗突发 / 指标突变 → 排除并告警；
- Webhook：见 §4。

## 8. AgentEnvelope

权限单调递减；内容 `UNTRUSTED_DATA`；禁止长期凭据；来源 HMAC/签名。

## 9. 模型漂移

记录模型版本、prompt hash、工具 schema、评价器校准版本。  
触发：版本变更或 Eval 非劣效失败 → 阻塞晋级 / 强制复跑（见 Measurement Framework）。

## 10. 签署检查表（文档收口）

文档保持 CONDITIONAL，直至下列全部勾选并由三方复审确认：

- [x] UNTRUSTED_DATA 元数据字段已定义  
- [x] PI 按阶段强制（含 Memory-delayed、A2A）  
- [x] Memory 准入/冲突/衰减/批量撤销/环检测/重验证  
- [x] 租户/Worker context/唯一键/Artifact/审计/Webhook/SSRF/跨租户测试  
- [x] 生命周期矩阵  
- [x] RPO ≤15min / RTO ≤2h 已冻结（本附录与 Tech Spec §17 一致）  
- [ ] 运营演练首次完成并留档（执行项，非文档项）  
- [ ] 三方复审将本附录升为 CURRENT  
