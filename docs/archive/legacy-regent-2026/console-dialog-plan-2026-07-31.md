# 控制台对话框改造计划（2026-07-31）

**PRD**：[console-dialog-prd-2026-07-31.md](./console-dialog-prd-2026-07-31.md)  
**DecisionNote**：[decision-note-console-dialog-2026-07-31.md](./decision-note-console-dialog-2026-07-31.md)

## 批次依赖

```
CON-0 → CON-1 ∥ CON-2 → CON-3 → CON-4 → CON-5
```

## CON-0 合同与数据模型

- 新增 `core/src/regent/application/confirmation.py`
- `ConfirmationRequest`、`DecisionPreference`、`RiskLevel`、`TimeoutDefault`
- `resolve_default(preference, request) -> allow|deny|cancel`
- 单测：三画像 × 三风险矩阵 + safety_invariant

## CON-1 决策偏好与规则引擎

- 新增 `core/src/regent/application/decision_policy.py`
- Settings：`decision_preference`（默认 `balanced`）、`decision_allow_actions` / `decision_deny_actions`（逗号分隔）
- 最小动作集：`goal_confirm`、`release_approval`、`quality_approval`、`delivery_gap_intervene`、`external_effect`
- 优先级：deny > 画像 > allow > ask；安全不变量最高
- 单测：规则覆盖画像

## CON-2 倒计时 / 超时默认

- `CountdownConfirmation`：异步原语；`timeout_seconds==0` 不自动决策（安全项）
- Web：服务端 `HumanTask.due_at` + `default_on_timeout`；超时应用默认决策并 emit 闸门事件（避免仅 `TIMED_OUT` 死等）
- 配置：`confirmation_timeout_seconds`（默认 300；RELEASE_APPROVAL 可沿用较长 due）
- 单测：超时默认、取消、safety 不超时

## CON-3 对话框重写（React Console + Core metadata）

- 重写 `TaskCard` / 增强确认卡：规则、风险、双向后果、倒计时；detail 折叠
- Core 在 `HUMAN_TASK_REQUIRED` / 关键 halt 路径附加 `confirmation` 元数据
- `APP_CONFIRMATION_REQUIRED` 保留理解摘要，并挂轻量 confirmation 字段（若有）
- **最小可验收切片**：TaskCard + HUMAN_TASK_REQUIRED metadata + 倒计时 UI；完整 ProgressNode 文案清洗可后续迭代

## CON-4 安全不变量包装

- `safety_invariant_request(...)` 工厂
- 四处门拒绝路径携带 `ConfirmationRequest(safety_invariant=True)`（`DomainError.details`）
- API `DomainError` handler 透出 `confirmation`（若有）
- 不削弱抛错 / fail-closed 语义

## CON-5 门禁与回滚

- `ops/console_confirm_gate.py`：禁止裸 `input(` / `Confirm.ask` / 无超时 confirm 模式
- 回滚：`REGENT_DECISION_PREFERENCE=balanced`，清空 allow/deny；无需 DB 迁移
- 相关 pytest；前端 build；`sync_local_to_server` + `deploy_console`

## 回滚说明

| 旋钮 | 恢复旧行为 |
|---|---|
| `REGENT_DECISION_PREFERENCE` | `balanced` |
| `REGENT_DECISION_ALLOW_ACTIONS` / `DENY` | 空 |
| `REGENT_CONFIRMATION_TIMEOUT_SECONDS` | `300`（或运维约定值） |
| 语义对齐 | 保持默认关（与死重修剪一致） |
