# 旧业务退役清单（批次 1）

日期：2026-09-16。对应提案 `regent-decoupling-modularization-editor-proposal-2026-09-16.md`。  
本清单记录**当前代码**中的运行面归属，供后续停新增、排空、删除对照。不修改历史 Alembic；删表另开批次。

## 1. 运行模式

| 模式 | 含义 | 默认 |
|---|---|---|
| `combined` | 现网兼容：小说 + 旧 Generated App 同进程 | **是**（迁移期） |
| `novel` | 只装配小说 API／Worker；不导入、不初始化旧业务 | 迁移目标 |
| `legacy` | 只装配旧业务，用于排空 | 临时 |

配置：`REGENT_SERVICE_MODE`（见 `regent.config.Settings.service_mode`）。

## 2. API 表面

| 入口 | 归属 | 说明 |
|---|---|---|
| `/v1/novel/*` | 小说 | `novel.api.novel`；novel-web 唯一调用面 |
| `/health/live` | 共用 | 进程存活 |
| `/health/ready`、`/v1/health`、`/v1/doctor` | 共用（实现需分模式） | novel 模式不得依赖 goals/runs/delivery 表健康 |
| `/v1/ops/environment/heal*` | 共用偏运维 | 主机观测；预览 reap 属旧资源清理 |
| `/`、`/app/static` | 小说前端 | novel 模式缺静态时明确不可用，不退回 `/console` |
| `/console` | 旧控制台 | legacy／combined |
| `/preview/*`（含 runtime 反向代理） | **旧** | 现写在 `api/main.py` 内，须迁出；非仅删 `app_previews` 路由 |
| `goals`、`app_*`、`product_creation`、`experiments`、`eval_runs`、`public_deploy`、`conversations`、`works`（Regent works）、`tools`、`observations`、`side_effects`、`self_improvement`、`harness_evolution`、`feedback`、`scheduler`、`runtime_profiles`、`baselines`、`aar1_v2`、`events`、`governance`、`memories`、`human_tasks`、`uploads`、`webhooks`、`reports` | **旧或待核实** | 首轮按旧产品装配；uploads／webhooks／human_tasks 若证实被小说使用再升格为共用 |

**小说前端调用方**：`apps/novel-web` 仅 `/v1/novel/**`。

## 3. API 启动初始化（lifespan）

| 初始化 | 归属 |
|---|---|
| DB engine／session factory | 共用 |
| `novel.production.configure_session_factory` | 小说 |
| `RuntimeProfileService.seed_bootstrap` | 旧 |
| `ensure_delivery_review_capability` | 旧 |
| `ensure_product_surface_capability` | 旧 |
| `ensure_environment_heal_capability` | 共用偏运维 |
| API host_guard 循环（含 preview prune/reap） | 共用观测 + **旧预览清理副作用** |

## 4. Worker 循环任务

| 任务 | 归属 |
|---|---|
| Worker 租约／心跳 | 共用 |
| `recover_novel_calls`／`sweep_expired_decisions`／`advance_background_run` | 小说 |
| `OutboxDispatcher` + `ExecutionOrchestrator`／P1 handlers | 旧 |
| `EventEngine` 注册 P1 | 旧 |
| `DurableTimerService.dispatch_due` → `TimerFired` | **旧主导**（名字通用，payload 多为 goal；novel 裁决走 DB sweep） |
| `reclaim_stale_created_runs` | 旧（`runs` 表） |
| `SchedulerService` | 旧经营 |
| `ReconciliationWorker` + delivery progress／zombie reclaim | 旧交付 |
| `PrivacyService.anonymize_expired` | 待核实（先随 combined／legacy） |
| `tick_host_resource_guard` | 共用＋旧预览 |
| `tick_behavior_monitoring` | 旧 |
| `PermitService`／`HumanTaskService` 到期 | 旧链 |

## 5. Outbox 事件归属（领取过滤）

领取过滤在 SQL `claim` 阶段完成（`event_type IN (...)`）；`combined` 不过滤。

| 类别 | 事件 | 领取方 |
|---|---|---|
| 旧 P1 主链 | `GoalExecutionRequested` … `ReleaseApprovalCompleted`、`DeliveryGapHumanApproved`、`DeliveryStateChanged` 等（见 `execution_events` / `get_p1_event_handlers`） | legacy |
| 旧可观测 | `GoalStateChanged`、`GoalSpecFrozen`、`WorkStateChanged`、`RunStateChanged`、`DeliveryStateChanged` | legacy |
| 计时器 | `TimerFired` | **仅 legacy**（按现网用法；无法从名字判定小说用途） |
| 小说 Outbox | （当前章推进不依赖 Outbox 领取） | novel 订阅集为空 → **不领取** |
| 未知类型 | 任意未列入清单 | **不领取、不标成功**；进待归类清单 |

后续若需共用事件：先在 payload／producer 写入明确 `product_scope`，再回填历史，最后放进 novel 订阅集。

## 6. 数据库与外部资源（删表另批）

| 资源 | 归属 | 退役注意 |
|---|---|---|
| `novel_*` 表、章 run、调用账本、裁决 | 小说 | 保留 |
| `goals`／`runs`／`works`（旧）／preview 工作区 | 旧 | 源码删后表可只读保留 |
| `outbox_events`／`worker_leases` | 共用结构 | 按事件归属排空 |
| `previews/` 目录与 runtime 端口文件 | 旧 | 排空停进程后再删部署配置 |
| 历史 Alembic | 共用 | **不改写**假装旧业务从未存在 |

## 7. 必须保留（勿与旧经营实验一并删）

- 模型网关、账本、租约、幂等、权限、恢复
- `novel/experiments`、盲评与质量实验
- 小说领域：`world_bible`、`dossiers`、`editor_audit`、`prose_patch`、`story_ledger` 等

## 8. 行为／检查点基线（搬家不改行为）

拆分 `direction`／`works` 与接入责编修稿前，应用已有检查点回放对比：

- 模型请求形状、调用键、费用入账
- Outbox／章事件序列
- 正文、`validated_content_hash`、facts、story_ledger
- 暂停恢复、双 Worker、用户改意、预算耗尽、调用 UNKNOWN

**最终接受边界**：`validate_chapter` 尾部写入 hash／facts 并 `_commit_run_story_ledger` 之处；局部修稿必须插在该固化**之前**。

## 9. 建议删除批次对照

| 退役阶段 | 本清单动作 |
|---|---|
| A 记录活跃任务 | 用本表查 outbox／runs／timers／preview |
| B 停新增 | legacy 创建入口 410；关 scheduler 生产者 |
| C 灰度 novel | `REGENT_SERVICE_MODE=novel` API+Worker |
| D 排空 | 仅 legacy Worker 消化旧事件与预览 |
| E–F 物理删除 | 删 `bootstrap/legacy_*`、旧路由、控制台依赖 |
| G 删表 | 独立变更，不在本批 |

## 10. 本批已落地代码挂钩

- `regent.bootstrap.service_mode`／`event_scope`
- `REGENT_SERVICE_MODE` → API／Worker 装配分支
- `REGENT_LEGACY_ACCEPT_NEW`（`legacy_accept_new`）：false 时 POST 创建 goals／discovery 返回 410
- `OutboxDispatcher`／`claim_statement` 按 `event_types` 过滤
- `novel.application.chapter_accept.finalize_chapter_acceptance`（审校后唯一固化入口）
- `EDITOR_REPAIR_MODE=off|shadow|auto`（默认 **shadow**）：`editorial_repair` 在 finalize 前跑局部补丁候选
- `novel.application.decisions`／`works_access`／`directing_types`：打破 direction↔works 主要循环依赖
- 复检修复：novel Worker／heal **不** soft-pause goals、不 prune 预览；`auto` 写回前 `post_apply_safety_ok`（front + 事实摘录 + completion_quote），失败保留原稿；`editorial.frozen_mode` 固化本轮策略
- `works_runtime.advance_background_run`：Worker 与 API 共用推进入口
- `directing_protocol`：`is_directed`／`production_protocol`
- `REGENT_EDITOR_REPAIR_MODE` + `REGENT_EDITOR_REPAIR_AUTO_PERCENT`：auto 按作品稳定分桶灰度（默认 0%＝仅 shadow）
- `chapter_finalize.run_editorial_then_accept`：责编→安全复核→FINISH→唯一固化
- `decisions.apply_decision`／`sweep_expired_decisions`；`works_input.bump_input_version`
- 排空清单：`docs/archive/regent-legacy-drain-checklist-2026-09-16.md`；`compose.yaml` 已挂 SERVICE_MODE 等变量
- `directing_budget`：调用／费用上限；`works_query`：章节只读查询
- 冒烟：`test_novel_api_assembly`（novel 无 `/v1/goals`，combined 保留）
