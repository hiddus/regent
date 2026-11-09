# core/migrations

Alembic 数据库迁移：`env.py` + `versions/`，**单一迁移链**，与 `src/` 同级（不属于 `src/regent` 包）。配置见根 `alembic.ini`。

## 现状（2026-07-31）

- `versions/` 下共 **41 个迁移**，编号 `0001`–`0041` 连续无缺口。
- 最新迁移：`20260731_0041_failure_envelope_repair.py`。
- 起始迁移：`20260716_0001_s1_kernel.py`。

## 规则

- **所有 schema 变更必须走本目录的 Alembic 迁移**，禁止在 `ops/` 或 `scripts/` 的脚本中直接执行 DDL（见 `ops/README.md` 与 `scripts/README.md`）。
- 扩展/收缩类变更遵循 [`docs/migration-policy.md`](../../docs/migration-policy.md)（如 `0032`/`0033` 的 aar1 expand / contract 成对迁移）。
- 保持单链：不要产生多 head 分叉。

## 目录内容

子目录：
- `versions/`

文件：
- `env.py`

> 本 README 由目录实际内容生成，反映当前结构；如用途有变请同步更新。
