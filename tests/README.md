# Tests（测试套件）

Regent 的三层测试体系，全部由根 `pyproject.toml` 的 `[tool.pytest.ini_options]` 驱动：
`pythonpath=["core/src"]`、`testpaths=["tests"]`、`asyncio_mode="auto"`。

## 目录布局

- `tests/architecture/` — **架构边界与约束**
  - `test_dependency_boundaries.py`：依赖方向边界检查（断言 `domain` 不依赖 fastapi / sqlalchemy / `regent.api` / `regent.infrastructure`）— ✅ 有效
  - `test_regent_definition_freeze.py`：永久定义防漂移（对照 `docs/definitions/REGENT-DEFINITION-1.0.*`）— ✅ 有效（F-2 已闭环）
- `tests/integration/` — **端到端集成测试**
  - `test_scheduler_checkpoint.py` / `test_scheduler_e2e.py`：调度与检查点
  - `test_health_api.py`：健康端点
  - `test_csv_summary_baseline.py` / `test_evt_parser_gap.py`：评测基线 / 事件解析缺口（P0 验收项）
  - `test_experiment_platform.py`：实验平台（**P2-6 候选特性**，非已验收项）
  - `test_adaptive_organization.py`：自适应组织（**P2-5 条件承诺**，默认 `ROLLOUT_NOT_ALLOWED`）
  - `test_eval_harness_e2e.py`：Eval 工具链（**P2-4 承诺项**）
- `tests/unit/` — **单元测试**，按分层组织：`agent/`、`api/`、`application/`、`domain/`、`infrastructure/`、`model/`、`ops/`、`runtime/`，以及 `test_config.py` / `test_console.py` / `test_worker.py`。

## 运行

```bash
pytest                 # 全量
pytest tests/unit      # 仅单元测试
pytest tests/integration/test_health_api.py  # 单个文件
```

## 历史修复：定义冻结门禁（F-2，已闭环）

该测试曾因文档改名而自身漂移（L13-14 指向仅存于 `docs/archive/` 的 `-v2` 文件），导致永久 `FileNotFoundError` —— 一个防漂移门禁自己漂移了。

现已修复：L13-14 指向 CURRENT 基线 `Regent-PRD.md` / `Regent-Technical-Spec.md`，并新增 `test_freeze_guard_paths_exist`（L19-24）作为前置 meta-guard，使「路径写错」表现为清晰断言失败而非 `FileNotFoundError`。

> 保留此条的意义：**门禁本身也会漂移**。任何依赖硬编码路径的守卫测试都应配 meta-guard。

## 🔴 已知缺口：近期修复缺少回归守卫

2026-07-31 一轮修复（F-1 router 挂载、F-3 agent 沙箱隔离、F-6 transcript 不可丢）**均未附带测试**。grep `tests/` 关键词 `build_agent_sandbox` / `command_sandbox` / `sandbox_mode` / `human-tasks` / `/v1/uploads` / `transcript-persist` 全部 0 命中。

| 已修复行为 | 缺失的守卫 |
|---|---|
| 28 个 router 全部挂载 | 无 OpenAPI 路径完整性断言 → F-1 可原样复发 |
| agent 命令必经 `build_agent_sandbox()` | 无架构测试禁止裸 `WorkspaceToolkit(root)`；测试自身仍全部裸构造（`unit/agent/test_agentic_generation.py:48/72/176`） |
| production 强制 `sandbox_mode=docker` | `test_config.py` 仅 8 行，只覆盖 dev 默认值 |
| transcript 持久化失败须阻断交付 | 无 |
| smoke 探针经沙箱执行 | 测试全部 `run_smoke=False`（`test_agentic_generation.py:88/216`），改后路径零覆盖 |

另有一处不合规守卫：`unit/application/test_delivery_state.py:129-133` 用 `assert "decide_delivery_verdict(" in src` 做源码字符串检查，正是 Technical-Spec §23 明令禁止的方式，应改为行为断言。

背景见 [`docs/doc-implementation-alignment-audit-2026-07-31.md`](../docs/doc-implementation-alignment-audit-2026-07-31.md) §8.3。

## 已知 skip / xfail（均为环境限制，非隐藏缺陷）

| 测试 | 条件 |
|---|---|
| `test_browser_journey.py:70,160` | Playwright 未安装 |
| `test_sandbox_optional_smoke.py:27` | Docker 不可用 |
| `test_workspace_writer.py:109` | 无 symlink 权限（Windows 常见） |

## 注意

测试运行会生成 `.pytest-tmp*` 临时目录（已在仓库中被清理并从 git 索引移除）。不要将这些临时目录提交进仓库；一次性诊断脚本请放 `ops/archive/oneoff/`。
