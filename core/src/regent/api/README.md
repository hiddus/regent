# core/src/regent/api

FastAPI 路由层。**判断某端点是否真的对外可用，以 `main.py` 的 `include_router` 为准，而不是以本目录存在同名文件为准。**

## 路由挂载状态（2026-07-31 晚 · F-1 已修复）

### ✅ 已挂载

`goals`、`works`、`scheduler`、`conversations`、`governance`、`experiments`、`eval_runs`、`self_improvement`、`side_effects`、`observations`、`memories`、`baselines`、`product_creation`、`app_projects`、`app_guidance`、`app_delivery`、`app_previews`、`runtime_profiles`、`events`、`feedback`、`tools`、`aar1_v2`、**`human_tasks`、`uploads`、`webhooks`、`reports`、`public_deploy`**

人工确认与上传：

- `POST /v1/human-tasks/{id}/complete`（Console TaskCard）
- `POST /v1/uploads`（Console 上传）

### 与 Technical-Spec §21

以 Tech-Spec §21.1 **规范意图 ↔ 实际路由** 双列对照表为准（2026-07-31 已改写）。完整审计见 [`docs/doc-implementation-alignment-audit-2026-07-31.md`](../../../../docs/doc-implementation-alignment-audit-2026-07-31.md) §7。

## 目录内容

文件：
- `__init__.py`
- `aar1_v2.py`
- `app_delivery.py`
- `app_guidance.py`
- `app_previews.py`
- `app_projects.py`
- `baselines.py`
- `conversations.py`
- `events.py`
- `eval_runs.py`
- `experiments.py`
- `feedback.py`
- `goals.py`
- `governance.py`
- `human_tasks.py`
- `main.py`
- `memories.py`
- `observations.py`
- `preview_security.py`
- `product_creation.py`
- `public_deploy.py`
- `reports.py`
- `runtime_profiles.py`
- `scheduler.py`
- `self_improvement.py`
- `side_effects.py`
- `tools.py`
- `uploads.py`
- `webhooks.py`
- `works.py`
