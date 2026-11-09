# core/src/regent/domain

领域模型与状态机：Goal / Work / Run / Permit / ExternalOperation 等状态枚举、状态转移（transitions.py）、错误码（errors.py），以及 P1 / Scheduler 专用状态。

## 一致性状态：✅ 已验证对齐（2026-07-31）

`states.py` 与 `transitions.py` 与 [`docs/appendices/State-Machines-and-Invariants.md`](../../../../docs/appendices/State-Machines-and-Invariants.md) **逐项吻合**，本轮全项目审计未发现偏差。

依赖边界由 `tests/architecture/test_dependency_boundaries.py` 强制：**domain 层不得依赖 `fastapi` / `sqlalchemy` / `regent.api` / `regent.infrastructure`**。该测试当前有效。

## 目录内容

文件：
- `__init__.py`
- `errors.py`
- `p1_states.py`
- `scheduler_states.py`
- `states.py`
- `transitions.py`

> 本 README 由目录实际内容生成，反映当前结构；如用途有变请同步更新。
