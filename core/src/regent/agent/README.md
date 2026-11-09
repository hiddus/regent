# core/src/regent/agent

Agent 运行时：模型调用、执行循环、子代理、工具、上下文组装、压缩、转录存储、校验与项目记忆。

## Agent Loop 现状

`agent_runner.py` 实现 think → act → observe → iterate（轮次 / token / 时长预算）。

`tools.py` 内建工具：`list_files`、`read_file`、`write_file`、`run_command`、`todo_write`。  
命令经 `command_sandbox`（`build_agent_sandbox()` → Docker 或 Local）。  
`capability_tools.load_capability_tool_specs()` 可发现带 `parameters` 的能力包（如 `product-surface-v1`）；执行适配器仍为后续项。

## 已关闭偏差（对齐审计 §7）

| 编号 | 原事实 | 现状 |
|---|---|---|
| F-3 | tools/verification 宿主子进程 | ✅ `run_command` → sandbox；smoke → `.regent_smoke_probe.py` |
| F-6 | transcript 静默丢 | ✅ 失败抛 `DeliveryRejection`；其余 best-effort `logger.exception` |

生产环境 `sandbox_mode` 必须为 `docker`；canary 仅生产且 docker 时允许。详见 Technical-Spec §13.8。

## 目录内容

- `agent_runner.py` / `compact.py` / `context_assembler.py` / `capability_tools.py`
- `generator.py` / `project_memory.py` / `subagent.py`
- `tools.py` / `transcript_store.py` / `types.py` / `verification.py`
