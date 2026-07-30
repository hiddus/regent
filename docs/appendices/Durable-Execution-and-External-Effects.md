# 附录 2：Durable Execution and External Effects

> 状态：CURRENT  
> 配套：`Regent-Technical-Spec.md` §8–§10  
> 日期：2026-07-22（二次复审修订）  
> **实现阶段：P1/G0（先于 Scheduler）** — 最小 ExternalOperation + Permit + 幂等 + 恢复必须在 Graduation G8 前合入

## 1. 目标

在进程崩溃、重复投递、网络分区与「请求已发送但响应丢失」窗口下，保证：

- 外部副作用**至多一次**有意义执行（或可对账到等价结果）；
- 本地事实与 Provider 真相最终一致；
- 一切尝试可审计。

## 2. 事务边界与原子派发权

数据库提交与外部网络调用**无法**在同一原子事务中完成。因此：

```text
CONSUMED ≠「供应商已收到请求」
CONSUMED  =「唯一派发权已在本地持久化」
```

### 2.1 正确顺序（冻结）

```text
[DB 事务 — 禁止网络 I/O]
  1. 校验 Worker Lease、Permit CLAIMED、operation_key 未冲突
  2. ExternalOperation: PREPARED → DISPATCHING
  3. Permit: CLAIMED → CONSUMED（同一事务）
  4. 固化 dispatch_generation（单调整数，同 operation_key 不变）
  5. 写 Audit +（如需）Outbox
[提交]

[事务外]
  6. 使用同一 operation_key / request_digest / fencing 上下文执行 Provider I/O
  7. 短事务记录 SUCCEEDED | FAILED_TERMINAL | UNKNOWN

[若崩溃在步骤 6 前后]
  - 已 CONSUMED + DISPATCHING → 不得新键重放；仅 query / 对账 / 同 key 安全重试（见能力矩阵）
```

重试必须复用相同 `operation_key`（及相同 `request_digest`，除非业务明确允许补偿操作——补偿用新 key + 新 Permit）。

## 3. Outbox 语义

- Dispatcher 领取需 Worker Lease；
- 成功标记完成；失败退避；超限 Dead Letter；
- Dead Letter 重放保持原业务 `operation_key` / 幂等键；
- 重放授权：操作者、原因、审计。

## 4. ExternalOperation 生命周期

```text
PREPARED
→ DISPATCHING
→ SUCCEEDED | FAILED_TERMINAL | UNKNOWN
UNKNOWN → RECONCILING
→ SUCCEEDED | FAILED_TERMINAL | MANUAL_REVIEW
```

### 4.1 必填字段

| 字段 | 用途 |
|---|---|
| operation_key | 全局唯一业务操作键（重试不变） |
| provider | 目标系统 |
| request_digest | 请求体规范哈希 |
| permit_id | 1:1 Permit |
| local_fencing_token | claim 时本地颁发 |
| dispatch_generation | 派发世代（与 CONSUMED 同事务固化） |
| worker_lease_token | 提交者 |
| external_id | Provider 返回 ID（可空直至对账） |
| reconciled_at / result_summary | 对账 |
| correlation_id / causation_id | 追踪 |

### 4.2 关键故障窗口

| 现象 | 本地动作 |
|---|---|
| 提交前崩溃 | 保持 PREPARED 或未 CONSUMED；可同事务重试进入 DISPATCHING |
| 提交后、I/O 前崩溃 | DISPATCHING+CONSUMED；同 operation_key 恢复后继续 I/O 或对账 |
| I/O 后响应丢失 | UNKNOWN；禁止新 key |
| Provider 明确失败 | FAILED_TERMINAL |
| Provider 成功 | SUCCEEDED |
| Query 不确定 | RECONCILING 或 MANUAL_REVIEW |

## 5. Permit 与本地 fencing

1. GRANTED → claim → CLAIMED + `local_fencing_token`。
2. ExternalOperation PREPARED 与 Permit 1:1 绑定。
3. **同一 DB 事务**：PREPARED→DISPATCHING 与 CLAIMED→CONSUMED，并固化 `dispatch_generation`。
4. 事务提交后才允许外部 I/O。
5. 响应成败只更新 ExternalOperation，不反消费 Permit。
6. 需要新的不可逆尝试（非同 key 重试）→ 新 Permit + 新 operation_key。

### 5.1 竞态

| 竞态 | 裁决 |
|---|---|
| 撤销 vs 派发 | 事务内看 Permit 仍 CLAIMED；已 CONSUMED 则只能对账 |
| CLAIMED 过期 | 不得进入 DISPATCHING；若已 CONSUMED 则对账 |
| 旧 Worker Lease | **本地 fencing**：拒绝旧 Worker 继续控制；不假设第三方识别 token |
| 双 Worker | operation_key 唯一约束 + 本地 fencing |

## 6. Fencing 能力边界（勿夸大）

| 机制 | 作用 | 非作用 |
|---|---|---|
| **本地 fencing** | 防止旧 Worker / 过期 Lease 继续拥有控制权 | 不保证第三方拒绝请求 |
| **Provider idempotency** | 同 operation_key 重放不产生第二次有意义效果 | 依赖 Provider 支持 |
| **Provider native fencing** | 可选；Provider 识别世代/令牌 | 多数 SaaS **不具备** |
| **Regent-controlled egress gateway** | 需要强撤权、统一注入幂等头、阻断旧 fencing 时的基础设施 | G0 可对受控 Provider 强制；外网直连则能力降级 |

## 7. Provider capability matrix（冻结字段）

每个 Provider 注册必须声明：

| Capability | 含义 | 缺省时 |
|---|---|---|
| `IDEMPOTENT_REPLAY` | 同 operation_key 安全重放 | 禁止不可逆自动派发 |
| `QUERY_BY_OPERATION_KEY` | 按 operation_key 查询 | 须有其他 query 或禁不可逆 |
| `QUERY_BY_EXTERNAL_ID` | 按 external_id 查询 | 可选 |
| `NATIVE_FENCING` | 识别 fencing/世代 | 可选；无则依赖本地+幂等 |
| `CANCEL_BEFORE_COMMIT` | 提交前可取消 | 可选 |

**准入**：自动不可逆副作用要求 `IDEMPOTENT_REPLAY` **或**（`QUERY_BY_OPERATION_KEY`|`QUERY_BY_EXTERNAL_ID`）。否则仅干跑/可逆/MANUAL_REVIEW。

## 8. 补偿与回滚

- Rollback 是新 ExternalOperation + 新 Permit + 新 operation_key；
- 引用原 external_id / digest；
- 不得改写原 SUCCEEDED 历史。

## 9. G0 测试剧本（Graduation G8 门禁）

1. 重复 Outbox / 同 operation_key → 无双副作用。  
2. 事务提交后、I/O 前杀 Worker → 恢复后同 key 完成或对账。  
3. I/O 后丢响应 → UNKNOWN → RECONCILING。  
4. 旧 Lease fencing → 本地拒绝。  
5. 无 IDEMPOTENT_REPLAY 且无 query 的 Provider → fail-closed。  
6. Dead Letter 重放 → 同 operation_key 幂等。

## 10. 与代码差距

当前有 Permit claim/consume 与部分 UNKNOWN 叙述，缺少一等公民 ExternalOperation、`operation_key`、`dispatch_generation` 与上述原子顺序。  
**本附录授权在 P1/G0 实现最小闭环**；不授权提前开工 Scheduler。

## 11. 签署检查表

- [x] CONSUMED = 派发权持久化；同事务 DISPATCHING+CONSUMED
- [x] fencing 分层 + Provider capability matrix
- [x] G0 先于 Scheduler；测试剧本列出
- [ ] G0 代码合入并跑通故障注入
- [ ] 三方复审升 CURRENT
