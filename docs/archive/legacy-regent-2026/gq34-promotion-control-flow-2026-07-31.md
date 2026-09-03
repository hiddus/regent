# GQ-3 / GQ-4 转正控制流程实现说明（2026-07-31）

> 对应诊断 v2 评审的 O5 / O6 / O8 / O9 / O10（纸面契约 / 不可达 / 无调用方 / 无 driver / 无 producer）。
> 把"钩子"补成**可驱动、可复算、代码强制**的转正控制流。

## 一、修复的根因（O6 比原以为更深）

原实现里 `worker/main.py` 在**进程启动期一次性**构造生成器单例（`build_code_generator` 未传 `goal_id` → 永远是默认 `artifact-backed`）。
而 `execution_orchestrator.py:1412` 与 `generation_service.py:209` 在 plan/run 创建时**已用 `goal_id` 解析策略并写入 `generator_ref`**——但真正的生成用的是启动期那个单例。
后果：**一旦 `canary_percent>0` 把某 goal 解析成 `agentic`，`assert_generator_consistency` 会因单例不符而 fail-closed，直接让该 goal 生成失败**（canary 不是"不可达"，而是"启用即让部分目标崩溃"）。

## 二、控制流实现

### GQ-3 Canary（Tech-Spec §13.7.1）
- 新增 `GeneratorSelector`（`core/src/regent/application/generator_factory.py`）：持有轻量 `ArtifactBackedCodeGenerator`；`AgenticCodeGenerator` **首次命中 agentic 时懒构造**（2026-07-31 剪启动期双实例死重）。
- Worker / API 启动期改调 `build_generator_selector`，注入选择器而非单例。
- 生成时按 `goal_id` 调 `GeneratorSelector.select(goal_id)` → `resolve_effective_generation_strategy` 解析有效策略 → 返回对应生成器 → 再对该**具体**生成器做一致性检查。canary 选中的 `agentic` goal 真正使用 agentic 生成器。
- **顺序强制**（O5）：`resolve_effective_generation_strategy` 新增 `gq2_closed`（源自 `config.generation_strategy_canary_gate`，默认 `False`），canary 必经 `canary_rollout_allowed(kill_switch, gq2_closed)` 校验 GQ-2→GQ-3 顺序。
- **已剪死重**：artifact-backed 热路径默认不再调用 `validate_goal_alignment_semantic`（非真实验证；opt-in `REGENT_GOAL_SEMANTIC_ALIGNMENT_ENABLED`）。

### GQ-4 默认切换（Tech-Spec §13.7.2）
- 新增 `core/src/regent/application/generation_strategy_promotion.py`：
  - `apply_gq4_promotion(report, kill_switch=, decision_record_ref=)` 调用 `gq4_default_switch_gate`；仅当 `PROMOTE_AGENTIC_CANDIDATE` 且无 kill switch 才允许，否则 `DomainError(POLICY_DENIED)` 阻止晋级（O8 关闭：孤儿 gate 现在有了强制调用方）。
  - `evaluate_gq4_promotion` 为非抛出版本供巡检。
- 晋级步骤：**实验报告 → `apply_gq4_promotion` 通过 → 记录 DecisionRecord → 运维翻转 `REGENT_GENERATION_STRATEGY=agentic`**。运行时默认仍由 `generation_strategy` 驱动；kill switch 始终覆盖。

### GQ-3 实验驱动（O9 / O10）
- `generation_strategy_experiment.drive_generation_strategy_experiment(config, runner)`：注入 `runner(variant, task) -> StrategyRunResult`，跑通双臂并 `record`。`runner` 即 `UserQualityMetrics` 的 producer（其 `StrategyRunResult` 字段被 `report()` 聚合），关闭了"实验无 driver / user_quality 恒空"。

## 三、改动文件

| 文件 | 改动 |
|---|---|
| `core/src/regent/config.py` | 新增 `generation_strategy_canary_gate: bool = False` |
| `core/src/regent/application/generation_strategy_policy.py` | `resolve_effective_generation_strategy` 增 `gq2_closed` 并咨询 `canary_rollout_allowed` |
| `core/src/regent/application/generator_factory.py` | 新增 `GeneratorSelector` + `build_generator_selector` |
| `core/src/regent/worker/main.py` | 启动期改调 `build_generator_selector` |
| `core/src/regent/api/app_delivery.py` | API 路径改调 `build_generator_selector` |
| `core/src/regent/application/execution_orchestrator.py` | plan 创建前先 `select()` 再一致性检查 |
| `core/src/regent/application/generation_service.py` | `execute()` 先 `select()` 再一致性检查与生成 |
| `core/src/regent/application/generation_strategy_promotion.py` | **新增**：GQ-4 强制晋级门 |
| `core/src/regent/application/generation_strategy_experiment.py` | 新增 `drive_generation_strategy_experiment` |
| `tests/unit/application/test_generation_quality.py` | 增 selector / canary 闸门 / gq4 强制门 / experiment driver 测试；修正既有 canary 测试 |
| `Regent-PRD.md` / `Regent-Technical-Spec.md` / `Regent-Plan.md` | §10.5 / §13.7(+13.7.1/13.7.2) / §13.7 状态表与 WP 验收同步 |

## 四、测试实证

- `test_generation_quality.py` + `test_aar1_foundation.py` + `test_agentic_generation.py`：**56 passed**。
- `test_generation_service_recovery.py` + `test_execution_orchestrator.py` 回归：**26 passed**。
- 鸭子类型选择器（`hasattr(gen, "select")`）未破坏注入普通生成器的既有单测。

## 五、配置与运维约定

| 旋钮 | 代码默认 | 含义 |
|---|---|---|
| `REGENT_GENERATION_STRATEGY` | `agentic` | 代码默认生成策略；`artifact-backed` 为 kill-switch / scaffold fallback |
| `REGENT_GENERATION_STRATEGY_CANARY_PERCENT` | `0` | canary 流量比例；0 = 不开流量 |
| `REGENT_GENERATION_STRATEGY_CANARY_GATE` | `False` | GQ-2 闭环验证后才允许 canary |
| `REGENT_GENERATION_STRATEGY_KILL_SWITCH` | `False` | 强制新 Run 回落 fallback |

- **运维可覆盖**：生产 `.env` 可设 `REGENT_GENERATION_STRATEGY=agentic` 作为运维侧运行时覆盖；这**不等于** GQ-4 已正式晋级（晋级仍须 `apply_gq4_promotion` + DecisionRecord）。
- **部署约束**：`ops/deploy_console.py` / `ops/sync_local_to_server.py` **不得擅自改写**生产策略；除非 DecisionRecord 明确要求，保持服务器现有 `.env` 原值。
- Hive：`REGENT_AAR1_CERTIFIED_HIVE` **代码默认 True**；GQ-5 前不扩容自适应拓扑（生产既有配置保持不扩容）。

## 六、仍属后续交付（非阻塞）

- **真实任务实验窗口**：在真实模型/工具/预算下跑双臂、产出带 95% CI 的成功率/成本/延迟与用户结果，仍需真实 runner 接入（生产 wiring）。控制流已可驱动、可复算，不再是空钩子。
- O1 / O3 / O4（artifact-backed 路径不跑 VerificationAgent、pytest 超时硬编码、新增生成入口需同接线一致性检查）仍为非阻塞可选增强。

## 七、合规前置更正（2026-07-31 重订）

> 对照架构评审 C2 / Tech-Spec §13.8 / 审计 §8：**仅有 CD-0.1 名义隔离不足以开生产 canary**。开窗前必须：
>
> 1. **CD-6 全绿**：N-3 专用 agent-exec 镜像 + entrypoint；N-3c uid 写盘；N-3d `host_path_map` fail-closed；N-3b 支持矩阵或 DinD；T1–T6；三联验收（echo + 写文件 + pytest）。见 [`cd6-execution-plan-2026-07-31.md`](./cd6-execution-plan-2026-07-31.md)。
> 2. **CD-7 全绿**：技 P1-1…4 + N-4/N-6。见 [`conversational-delivery-next-plan-2026-07-31.md`](./conversational-delivery-next-plan-2026-07-31.md)。
>
> 沙箱不合规或硬债未收口时开流量 = 无效实验 / 规范违反。GQ-4 仍见 PENDING DecisionNote。
