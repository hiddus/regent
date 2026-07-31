# docs/appendices

技术附录（CURRENT）：状态机与不变式、Durable Execution 与外部副作用、安全租户与恢复。实现合同时查阅。

| 附录 | 代码落点 | 一致性（2026-07-31 审计） |
|---|---|---|
| `State-Machines-and-Invariants.md` | `core/src/regent/domain/states.py`、`transitions.py` | ✅ 逐项吻合 |
| `Durable-Execution-and-External-Effects.md` | `core/src/regent/runtime/`、`infrastructure/models.py`（`ExternalOperationModel`） | ✅ 一致 |
| `Security-Tenancy-and-Recovery.md` | `infrastructure/sandbox.py`、`config.py` | 🟠 F-3 已闭环（agent 命令经 `build_agent_sandbox()`，生产 fail-closed 见 `config.py:72`）；遗留 N-3 / N-4，见 `core/src/regent/infrastructure/README.md` |

## 目录内容

文件：
- `Durable-Execution-and-External-Effects.md`
- `Security-Tenancy-and-Recovery.md`
- `State-Machines-and-Invariants.md`

> 本 README 由目录实际内容生成，反映当前结构；如用途有变请同步更新。
