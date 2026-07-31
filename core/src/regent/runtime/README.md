# core/src/regent/runtime

运行时执行层：持久化执行、检查点、租约与定时器。这是「进程重启后可恢复」这一核心不变式的落点。

| 文件 | 职责 |
|---|---|
| `dispatcher.py` | 执行分发 |
| `long_tasks.py` | 长任务持久化执行 |
| `oe2.py` | ExternalOperation 第二代执行路径（配合 `infrastructure/models.py` 的 `ExternalOperationModel`：`operation_key` 唯一约束 + `dispatch_generation` + `local_fencing_token`） |
| `timers.py` | Timer（等待期不占 Worker） |
| `worker_leases.py` | Worker 租约与围栏令牌 |

对应规范：Technical-Spec §9/§10 与 [`docs/appendices/Durable-Execution-and-External-Effects.md`](../../../../docs/appendices/Durable-Execution-and-External-Effects.md)。✅ 本轮审计未发现偏差。

## 目录内容

文件：
- `__init__.py`
- `dispatcher.py`
- `long_tasks.py`
- `oe2.py`
- `timers.py`
- `worker_leases.py`

> 本 README 由目录实际内容生成，反映当前结构；如用途有变请同步更新。
