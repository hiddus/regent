# Regent Core

Regent 的可治理、可审计、可恢复的自主产品生成核心（Python / FastAPI）。源码包位于 `core/src/regent`，通过 `pyproject.toml`（hatchling）构建为 `regent-core` wheel，要求 `Python >=3.12,<3.14`。

## 运行入口

- `regent-api` → `regent.api.main:run`：FastAPI 服务，对外暴露 REST API 与静态资源。
- `regent-worker` → `regent.worker.main:run`：Worker 进程，消费执行队列并持久化运行。

启动时的 lifespan 会：建立数据库引擎与 session 工厂，调用 `RuntimeProfileService.seed_bootstrap()` 注入引导期运行时画像，并确保 `delivery_review` / `product_surface` 等引导能力存在（fail-open 启动、fail-closed 使用）。

## 子包结构

| 子包 | 职责 |
|---|---|
| `domain/` | 领域模型与状态机：`GoalState` / `WorkState` / `RunState` 枚举、状态转移 `transitions.py`、错误码 `errors.py`，以及 P1 / Scheduler 专用状态。 |
| `agent/` | Agent 运行时：`generator.py` 调用模型、`agent_runner.py` 执行循环、`subagent.py` 子代理、`tools.py` 工具、`context_assembler.py` 上下文组装、`compact.py` 压缩、`transcript_store.py` 转录存储、`verification.py` 校验、`project_memory.py` 项目记忆、`types.py` 类型。 |
| `api/` | FastAPI 路由层。**已挂载**：goals、works、scheduler、conversations、governance、experiments、eval_runs、self_improvement、side_effects、observations、memories、baselines、product_creation、app_*、runtime_profiles、events、feedback、tools、aar1_v2、**human_tasks、uploads、webhooks、reports、public_deploy**（F-1 已修复）。以 `main.py` 的 `include_router` 为准。 |
| `application/` | 应用服务层：目标服务、执行编排 `execution_orchestrator`、组织引擎 `organization_engine`、能力解析与构建、交付批次与评审、实验平台、调度、许可 `permit_service`、策略引擎 `policy_engine`、恢复与对账 `reconciliation_worker`、Outbox 死信 `outbox_dead_letter_service` 等。 |
| `infrastructure/` | 基础设施：`database.py`（引擎/session 工厂）、`artifact_store.py`、`code_generator.py`、`deployment.py`、证据采集 `evidence_capability.py` / `evidence_sources.py`、能力确保 `delivery_review_capability.py` / `product_surface_capability.py`、`webhook_connector.py`、`report_generators.py`。 |
| `model/` | 模型配置与对话：`chat.py`、模型设置。 |
| `runtime/` | 运行时执行（持久化执行 / 检查点）。 |
| `worker/` | Worker 进程实现（对接执行队列）。 |
| `operations/` | 运维操作层。 |
| `experiments/` | 实验平台支撑。 |
| `capabilities_bootstrap/` | 引导期能力声明 JSON（打包进 wheel，见 `pyproject.toml` 的 `force-include`）。 |
| 根 `config.py` | 配置：`get_settings()` 从环境变量读取运行时设置。 |

> 注：Alembic 数据库迁移位于 `core/migrations/`（`env.py` + `versions/`），与 `src/` 同级，不属于 `src/regent` 包。

## 核心不变式

Core Kernel 负责：状态机、治理、证据、审计、恢复、预算与安全边界。生成的 App 不由 Core 预置业务页面，而是由 Core 依据目标、证据与约束生成。

设计原则：**LLM 只能提出结构化 Command，状态转换由确定性 Application Service 执行**。当前 Agent Loop（`agent/agent_runner.py`）在沙箱内的可逆效应（读写文件、跑测试）不逐条走 Command；不可逆/外部效应仍须前置 ExecutionPermit。该分层见 PRD §4.4.5 与 Technical-Spec §13.8。

## 已关闭偏差（2026-07-31 晚）

完整复检见 [`docs/doc-implementation-alignment-audit-2026-07-31.md`](../docs/doc-implementation-alignment-audit-2026-07-31.md) §7。

| 编号 | 原事实 | 现状 |
|---|---|---|
| F-1 | 5 个 router 未挂载 | ✅ 已 `include_router` |
| F-3 | tools/verification 宿主子进程 | ✅ toolkit sandbox + smoke 探针脚本 |
| F-6 | transcript 静默丢 | ✅ 失败抛 `DeliveryRejection`；其余 best-effort 打日志 |
| F-4 | §21 API 清单失真 | ✅ Tech-Spec §21 双列对照 |

> `generation_strategy` / `canary_*` 保守默认是 **已实现但默认不可启用** 的规范门禁。生产 canary 须 `sandbox_mode=docker`。

## 本地开发

```bash
pip install -e ".[dev]"      # 安装 + 开发依赖
pytest                        # 运行测试（配置见 pyproject [tool.pytest.ini_options]）
ruff check core/src           # 代码风格 / 静态检查
mypy                          # 严格类型检查
```

测试配置：`pythonpath=["core/src"]`、`testpaths=["tests"]`、`asyncio_mode="auto"`。
