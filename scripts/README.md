# Scripts（仓库级辅助脚本）

本目录存放**非运行时**的仓库辅助脚本（与 `core/` 运行时、`ops/` 运维脚本区分）。

## 当前内容

- `credential_scan.py` — **凭据扫描**：检测代码与配置中泄露的密钥/凭据，作为安全门禁的一环。
- `release_tag.sh` — **发布打标签**：标准化发布版本打标流程。

## 与其他运维目录的边界

| 位置 | 定位 | 是否随 wheel 发布 |
|---|---|---|
| `scripts/`（本目录） | 非运行时的仓库辅助脚本 | 否 |
| `ops/` | 仓库级运维入口与 CI 门禁（如 `check_repo_hygiene.py`、`delivery_dead_end_gate.py`） | 否 |
| `core/src/regent/operations/` | 随 Core 发布、容器内可执行的运维命令 | 是 |

## 约定

- 一次性诊断 / 热修脚本**不要**放在仓库根目录或本目录，应归入 `ops/archive/oneoff/`（见 `ops/README.md` 与 `ops/check_repo_hygiene.py` 的 CI 门禁）。
- 涉及数据库结构变更必须通过 `core/migrations/versions/` 的 Alembic 迁移，禁止在脚本里直接执行 DDL。
