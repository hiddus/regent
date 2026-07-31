# Ops（运维脚本归档）

> **一致性说明（2026-07-31 复检，状态未变）**：历史一次性脚本归档**仍在进行中**——`archive/oneoff/` 已收纳大量 `diag_*`/`fix_*`/`check_*`/`q*`/`verify_*` 脚本，但 `ops/` 根目录**仍残留**部分一次性脚本（`poll_*.py`、`*_fix_*.py`、`verify_bb40_*.py`、`unstick_*.py`、`reclaim_*.py`、`check_round3.py`、`_remote_fix_*.py`、`pull_server_orchestrator.py`、`remove_console_nginx.py` 等），尚未全部迁至 `archive/oneoff/`。请勿据此 README 判断"清理已完成"。

## 可复用入口（节选）

- `deploy_console.py` — 构建产物同步到公网 `:8000/console/`
- `sync_local_to_server.py` — 同步 `core` 到 api/worker，并维持 host `.env` 中 `REGENT_AAR1_CERTIFIED_HIVE=true`（进程环境需容器 recreate 时带入；见 `archive/oneoff/enable_certified_hive_recreate.py`）

## 布局

- `archive/oneoff/` — 历史 `diag_*` / `fix_*` / `_server_*` / `check_*` / `verify_*` 等一次性脚本归档
- `ops/` 根目录 — 可复用运维入口与门禁脚本

## 规则

1. 产品/运行时代码只应位于 `core/`、`apps/`、`capabilities/`（运行时代码副本不得留在 `ops/` 根）
2. Schema 变更走 `core/migrations/versions/` 下的 Alembic
3. 临时诊断脚本归入 `ops/archive/oneoff/`（或带日期子目录）
4. `ops/check_repo_hygiene.py` 作为根白名单门禁，阻止新脚本落回**仓库根目录**

## 待办（未完全闭环）

- [ ] 将 `ops/` 根目录残留的一次性脚本（`poll_*.py`、`*_fix_*.py`、`verify_bb40_*.py`、`unstick_*.py`、`reclaim_*.py`、`check_round3.py`、`_remote_fix_*.py`、`pull_server_orchestrator.py`、`remove_console_nginx.py` 等）迁至 `archive/oneoff/`，使 `ops/` 根仅保留可复用入口，并启用 `check_repo_hygiene.py` 根白名单元门禁。
