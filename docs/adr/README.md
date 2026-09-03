# docs/adr

架构决策记录（ADR）。

| ADR | 主题 |
|---|---|
| `0001-modular-monolith.md` | 模块化单体架构 |
| `0002-postgresql-runtime.md` | PostgreSQL 作为唯一事实源 |
| `0003-kernel-capability-boundary.md` | 内核与能力池边界 |

> **ADR-0003 读法提醒**：其中枚举的 7 类能力是**边界范畴**，不是交付清单。当前引导期实际只落地 3 个能力，以 `capabilities/bootstrap/` 下的 `capability.json` 为准（偏差 F-11）。
>
> 旧 Regent 路线的 `decision-note-*` 已移入[历史归档](../archive/legacy-regent-2026/README.md)，不再与当前 Novel Engine 基线具有同等效力。新的产品级决策应直接更新三件套，并在确需记录取舍时新增 ADR。

## 目录内容

文件：
- `0001-modular-monolith.md`
- `0002-postgresql-runtime.md`
- `0003-kernel-capability-boundary.md`

> 本 README 由目录实际内容生成，反映当前结构；如用途有变请同步更新。
