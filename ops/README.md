# Ops（运维脚本归档）

> **一致性说明（2026-07-30 修复后）**：`diag_*` / `fix_*` / `_server_*` 历史一次性脚本已迁入 `ops/archive/oneoff/`；`ops/` 根目录仅保留可复用的运维入口（如 `deploy_console.py`、`sync_local_to_server.py`、`check_repo_hygiene.py` 等）。

## 布局

- `archive/oneoff/` — 历史 `diag_*` / `fix_*` / `_server_*` / `check_*` / `verify_*` 等一次性脚本归档
- `ops/` 根目录 — 可复用运维入口与门禁脚本

## 规则

1. 产品/运行时代码只应位于 `core/`、`apps/`、`capabilities/`（运行时代码副本不得留在 `ops/` 根）
2. Schema 变更走 `core/migrations/versions/` 下的 Alembic
3. 临时诊断脚本归入 `ops/archive/oneoff/`（或带日期子目录）
4. `ops/check_repo_hygiene.py` 作为根白名单门禁，阻止新脚本落回**仓库根目录**

## 已完成

- [x] 将 `ops/` 根目录 `diag_*` / `fix_*` / `_server_*` 迁移至 `archive/oneoff/`（2026-07-30 对齐审计修复轮）
