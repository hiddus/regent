# AAR-1 Foundation 编码计划

> 状态：ACTIVE / BUILD_ALLOWED（M1–M6 Contract PASSED；Rollout 关闭）  
> 日期：2026-07-27  
> 准入：`AAR1CodingReadinessDecisionRecord.json`  
> Rollout：NOT ALLOWED，直至 P2-4 Eval DecisionRecord

## 批次

| 批次 | 依赖 | 交付 | 完成 Gate |
|---|---|---|---|
| `aar1-m1-expand` | 当前 Alembic head | Constitution/Policy、Organization Version/Decision、Agent SpecVersion/Deployment/Relationship、AgentTask、MCP 表；Version 1 回填 | 可逆迁移、count/hash 一致 |
| `aar1-m2-dual-write-shadow` | M1 | 旧/新组织双写；Policy 与新选择器影子运行 | 双写差异 0；影子错误全解释 |
| `aar1-m3-read-switch` | M2 | Orchestrator 读 Version/Deployment；Durable AgentTask；只读 MCP 后接副作用 MCP | 六崩溃点恢复、scope 与职责隔离 |
| `aar1-m4-enforce` | M3 故障 Gate | Policy fail-closed、C/V/R 强制准入、副作用复用 ExternalOperation | PRD F1–F4 全绿 |
| `aar1-m5-contract` | M4 + 单独 DecisionRecord（2026-07-27 用户授权） | 停旧写；OrganizationVersion 为唯一写真相；`current_version_id` NOT NULL | 无 legacy dual-write；迁移可回滚 |
| `aar1-m6-memory-path-contract` | M5（用户命名；Coding Plan 归入 Contract） | 移除生产内存 A2A / 旧状态适配；仅 Durable AgentTask | 默认 phase=`contract` 下内存路径关闭 |

## M5 / M6 Contract 工作分解

1. 新增独立 Alembic revision（不可并入 M1 Expand）：回填缺失 Version、`current_version_id` NOT NULL + FK；downgrade 可逆。
2. `aar1_phase=contract`：`organize()` 以 OrganizationEngine 为主写路径；停止 dual-write shadow / fail-open legacy。
3. Organization 行上的 `strategy`/`rationale` 仅作 Version 投影，不再作为可变真相源独立改写。
4. AgentMesh 在 `contract` 关闭内存 A2A（含 `route_with_envelope`）；显式 `use_memory=True` 仅保留给单测。
5. 行为测试：停旧写、内存路径拒绝、迁移链、可回滚。

## M1 工作分解

1. 新 Alembic revision 只做 Expand。
2. 建立确定性 Policy Engine、DSL schema 和 decision point。
3. 建立 Organization snapshots/candidates/checks/decisions/versions。
4. 建立 Agent SpecVersion/Deployment/Lifecycle/Relationship。
5. 建立 AgentTask、Envelope nonce/key registry 和状态转换服务。
6. 建立 MCP Server/Binding/Invocation；只保存 Secret ref。
7. 回填当前 OrganizationVersion v1，输出可重复 count/hash 报告。

## 每批通用完成定义

- 数据库约束、服务、API、Worker、Projection、Audit 和运维入口齐全；
- 正常、拒绝、UNKNOWN、重试、Dead Letter、恢复和跨 scope 行为测试；
- 迁移 upgrade/downgrade 或等价回退演练；
- Ruff、mypy、Pytest、Alembic、定义冻结和架构边界全绿；
- 不以模拟 A2A/MCP、源码字符串或 LLM 自评替代行为验收；
- 本批 DecisionRecord 明确是否允许进入下一批。

## 禁止

- 将 Foundation 编码授权解释为多 Agent 默认上线；
- M1 同时删除旧字段或旧路径；
- Policy shadow 阶段悄然改变生产行为；
- A2A 宣称 exactly-once；
- MCP 副作用绕过 Permit/ExternalOperation；
- 在稳定数据和反事实日志前引入 RL；
- 在无 M5 DecisionRecord 时提前 Contract（已于 2026-07-27 授权后解除）。

