# 旧业务排空检查清单（批次 9 前置）

日期：2026-09-16。在物理删除旧模块之前，按本清单取证。不要改历史 Alembic。

## 1. 部署开关（先停新增）

```text
REGENT_SERVICE_MODE=novel          # API/Worker 只跑小说
# 若仍用 combined 排空旧队列：
REGENT_SERVICE_MODE=legacy         # 仅排空 Worker
REGENT_LEGACY_ACCEPT_NEW=0         # 拒绝 POST /v1/goals 与 discovery 创建
```

`compose.yaml` 已支持通过环境变量注入上述项（默认仍 `combined`，避免现网误切）。

## 2. 活跃任务查询（只读）

```sql
-- 旧 Outbox 待处理
SELECT status, event_type, count(*)
FROM outbox_events
WHERE status IN ('PENDING','FAILED','DISPATCHING')
GROUP BY 1, 2
ORDER BY 3 DESC;

-- 旧 runs / goals
SELECT status, count(*) FROM runs GROUP BY 1;
SELECT status, count(*) FROM goals GROUP BY 1;

-- 计时器（若有 timers 表）
-- SELECT status, count(*) FROM durable_timers GROUP BY 1;

-- 小说在途（应保留）
SELECT state, count(*) FROM novel_chapter_runs
WHERE state IN ('QUEUED','RUNNING','PENDING_DECISION','AWAITING_INPUT','RETRYABLE_FAILED')
GROUP BY 1;
```

## 3. 完成标准

| 项 | 出口 |
|---|---|
| 无新 goals / discovery | `LEGACY_ACCEPT_NEW=0` 后创建返回 410 |
| Outbox 旧事件排空或 DEAD_LETTER 已分类 | PENDING/DISPATCHING=0（或仅未知隔离） |
| 无 RUNNING 旧 runs、无活跃预览进程 | 主机 `previews/` 无活跃 port 文件 |
| 小说旅程 | 创建→生成→暂停恢复→裁决→导出通过 |
| novel Worker | 不构造 ExecutionOrchestrator；claim 集为空 |

## 4. 物理删除（另批）

排空取证归档后，再删 `bootstrap/legacy_*`、旧 `app_*` 路由、控制台依赖与 combined 模式。删表另开迁移批次。
