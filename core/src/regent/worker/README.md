# core/src/regent/worker

Worker 进程实现，消费执行队列并持久化运行。入口 `regent-worker` → `regent.worker.main:run`。

## 生成器选择与沙箱装配（已核对）

- `main.py:255` 调用 `build_generator_selector(..., enforce_consistency=True)` 构建**按 Goal 生效的** GeneratorSelector（注释见 :253-254：单例注入会把 canary 锁死在启动时的默认值）。**生成策略路径本身是通的**，实际走哪条由 `config.py` 的 `generation_strategy` / `canary_gate` / `canary_percent` 决定 —— 三者的保守默认值是 PRD §10.5 与 Technical-Spec §13.7.1 **规定的门禁**，不是硬编码缺陷。
- `main.py:265-271` 按 `settings.sandbox_mode` 在 `LocalSandboxDriver` 与 `DockerSandboxDriver` 间二选一，覆盖**构建路径**。
- agent 侧命令执行已独立走 `infrastructure/sandbox.py:442 build_agent_sandbox()`（F-3 已闭环），`agent/tools.py:232` 缺沙箱即拒绝执行，不再回退本进程。遗留缺陷 N-3（docker 模式缺 `--entrypoint`）见 `infrastructure/README.md`。
- `api/app_delivery.py:199-202` 存在**第二处独立的 sandbox_mode 分支**，即 API 进程也会直接起沙箱。

## 目录内容

文件：
- `__init__.py`
- `main.py`

> 本 README 由目录实际内容生成，反映当前结构；如用途有变请同步更新。
