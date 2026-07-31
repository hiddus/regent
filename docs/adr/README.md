# docs/adr

架构决策记录（ADR）。

| ADR | 主题 |
|---|---|
| `0001-modular-monolith.md` | 模块化单体架构 |
| `0002-postgresql-runtime.md` | PostgreSQL 作为唯一事实源 |
| `0003-kernel-capability-boundary.md` | 内核与能力池边界 |

> **ADR-0003 读法提醒**：其中枚举的 7 类能力是**边界范畴**，不是交付清单。当前引导期实际只落地 3 个能力，以 `capabilities/bootstrap/` 下的 `capability.json` 为准（偏差 F-11）。
>
> 除 ADR 外，较新的决策以 `docs/decision-note-*.md` 形式记录（如 [`decision-note-auto-start-journey-2026-07-31.md`](../decision-note-auto-start-journey-2026-07-31.md)）。两者效力等同，均需在与基线文档冲突时被援引。

## 目录内容

文件：
- `0001-modular-monolith.md`
- `0002-postgresql-runtime.md`
- `0003-kernel-capability-boundary.md`

> 本 README 由目录实际内容生成，反映当前结构；如用途有变请同步更新。
