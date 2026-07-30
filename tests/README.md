# Tests（测试套件）

Regent 的三层测试体系，全部由根 `pyproject.toml` 的 `[tool.pytest.ini_options]` 驱动：
`pythonpath=["core/src"]`、`testpaths=["tests"]`、`asyncio_mode="auto"`。

## 目录布局

- `tests/architecture/` — **架构边界与约束**
  - `test_dependency_boundaries.py`：依赖方向边界检查
  - `test_regent_definition_freeze.py`：永久定义防漂移（对照 `docs/definitions/REGENT-DEFINITION-1.0.*`）
- `tests/integration/` — **端到端集成测试**
  - `test_scheduler_checkpoint.py` / `test_scheduler_e2e.py`：调度与检查点
  - `test_health_api.py`：健康端点
  - `test_experiment_platform.py`：实验平台
  - `test_adaptive_organization.py`：自适应组织
  - `test_eval_harness_e2e.py`：Eval 工具链
  - `test_csv_summary_baseline.py` / `test_evt_parser_gap.py`：评测基线 / 事件解析缺口
- `tests/unit/` — **单元测试**，按分层组织：`agent/`、`api/`、`application/`、`domain/`、`infrastructure/`、`model/`、`ops/`、`runtime/`，以及 `test_config.py` / `test_console.py` / `test_worker.py`。

## 运行

```bash
pytest                 # 全量
pytest tests/unit      # 仅单元测试
pytest tests/integration/test_health_api.py  # 单个文件
```

## 注意

测试运行会生成 `.pytest-tmp*` 临时目录（已在仓库中被清理并从 git 索引移除）。不要将这些临时目录提交进仓库；一次性诊断脚本请放 `ops/archive/oneoff/`。
