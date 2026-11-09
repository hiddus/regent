# core/src/regent/model

模型接入层：Provider 抽象、工厂与对话封装。

| 文件 | 职责 |
|---|---|
| `provider.py` | 模型 Provider 抽象与实现 |
| `factory.py` | 按配置构建 Provider |
| `chat.py` | 对话调用封装 |

对外错误类型 `ModelConfigurationError` / `ModelOutputError` 在 `api/main.py` 中被映射为 HTTP 错误响应。凭据管理见 [`docs/contracts/model-secrets.md`](../../../../docs/contracts/model-secrets.md)。

> Technical-Spec §16 要求「生成 Agent 不得拥有长期凭据」。F-3 修复后，Agent 命令经 `infrastructure/sandbox.py:442 build_agent_sandbox()` 执行，不再继承持有 provider key 的 Worker 宿主进程环境；生产由 `config.py:72` fail-closed 强制 `sandbox_mode=docker`。
>
> 🟡 残留：dev/test 下 `LocalSandboxDriver` 仍在宿主执行（`sandbox.py:412-439`），此路径下 §16 约束不成立，仅适用于本地开发。

## 目录内容

文件：
- `__init__.py`
- `chat.py`
- `factory.py`
- `provider.py`

> 本 README 由目录实际内容生成，反映当前结构；如用途有变请同步更新。
