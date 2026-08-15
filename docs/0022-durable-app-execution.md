# 0022 持久化 App 自主执行闭环

## 目标

用户确认产品目标后，Core 独立于浏览器完成启动、静态 App 生成、离线检查和 Preview 发布。用户不需要再次发送“继续”。

## 冻结决策

- `CONFIRM_GOAL` 与 `START_GOAL` 是两个独立、可审计命令；Console 可用一个按钮连续提交。
- `POST /v1/goals/{goal_id}/start` 返回 `202`，事务内把 Goal 从 `READY` 转为 `ACTIVE` 并写入 `GoalExecutionRequested`。
- Worker 是 App 生成的执行者；Console 不再同步调用 Preview 生成。
- 执行阶段保存在 Goal metadata，并同步写入 App 对话时间线：`QUEUED`、`PLANNING`、`GENERATING`、`PREVIEW_READY` 或 `FAILED`。
- `CONTINUE` 在 `READY` 时启动，在 `FAILED` 时安全重试，在运行中只返回真实状态。
- Outbox 采用最大尝试次数、指数退避和 `DEAD_LETTER`，健康检查暴露失败与死信数量。
- 0022 使用三秒短轮询，不引入 SSE、通用工作流 DSL 或多 Agent 编排。

## 关键不变量

1. 未冻结 GoalSpec 不得启动。
2. Start 的幂等键相同时不得产生第二个执行事件。
3. 同一 Goal 只有一个 AppPreviewRelease；失败重试复用原 release ID。
4. Preview 内容只有 Worker 可以生成；浏览器关闭不影响执行。
5. 未注册或永久失败事件不得无限占用 Outbox。
6. 每个阶段和失败原因必须可从对话与状态接口恢复。

## API

- `POST /v1/app-projects/{project_id}/confirm`
- `POST /v1/goals/{goal_id}/start`
- `GET /v1/app-projects/{project_id}/status`
- `GET /v1/preview-releases/{release_id}`
- `GET /health/ready`

## 完成标准

- 初次确认消息包含 GoalSpec ID、版本、状态和内容哈希。
- 页面刷新后仍可确认。
- 确认并启动后立即出现持久化执行消息。
- Worker 能生成 Preview，失败后可由“继续”安全重试。
- Outbox 未知事件最终进入死信。
- 两个独立 App 均完成 DRAFT → READY → ACTIVE → PREVIEW_READY。
- 本地测试、格式、类型、迁移、服务器健康和日志验收通过。

## 锁定前状态与确认事件合同

- DRAFT 目标必须由统一 `GoalReadiness` 判定为 `DRAFT_CLARIFYING` 或 `DRAFT_CONFIRMABLE`；前端不得自行推导另一套规则。
- 每次修正产生新版 GoalSpec 后，事务必须以一种可行动事件结束：未就绪写入 `CLARIFICATION_REQUIRED`，就绪写入 `APP_CONFIRMATION_REQUIRED`。
- `APP_CONFIRMATION_REQUIRED` 必须携带最新 GoalSpec ID、版本、哈希、readiness 快照和唯一 `gate_key`；同一 GoalSpec 最多存在一个有效 gate。
- `DRAFT_CONFIRMABLE` 只表示等待 Goal Owner 确认。系统不得自动 confirm 或 start，也不得显示“正在续跑”。
- GoalSpec 更新后旧 gate 自动失效；确认旧哈希返回当前版本与哈希，控制台刷新到最新 gate。
- 页面刷新、SSE 重连或消息重复投递不得恢复旧 gate，也不得产生重复启动。
- 存量 `DRAFT_CONFIRMABLE` 目标可通过幂等 reconciliation 补发确认事件，但不得自动锁定或启动。

新增运行不变量：`draft_confirmable_without_pending_gate=0`、`pending_gate_hash_mismatch=0`、`duplicate_pending_gate_keys=0`。
