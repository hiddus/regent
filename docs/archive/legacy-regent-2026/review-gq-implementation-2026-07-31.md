# GQ-0..GQ-2 实现复核（2026-07-31）

对应文档：PRD §10.5、Tech-Spec §13.4–§13.7、Plan §13（GQ-0..GQ-5）。
核验对象：提交 `67c5a03 Implement GQ-0..GQ-2 generation quality baseline with fail-closed metadata.`
结论：**实现与计划一致，核心 fail-closed 不变式与反馈闭环已落地，测试全绿，无需回退。**

## 一、逐项核验

### GQ-1 生成器选择一致性（PRD §10.5 / Tech-Spec §13.4 / Plan WP-GEN-SELECT）✅

- `worker/main.py:252` 不再硬编码 `ArtifactBackedCodeGenerator`，改为 `build_code_generator(settings, …, enforce_consistency=True)`。
- `generator_factory.build_code_generator` 先 `resolve_effective_generation_strategy` 再构造，且 `enforce_consistency=True` 时调用 `assert_generator_consistency`。
- `generator_metadata.assert_generator_consistency` 在 strategy / 标签 / 对象类型任一不一致时抛 `DomainError(GENERATOR_METADATA_MISMATCH)`，**失败即闭、无静默回退**；并写 `GeneratorMismatchEvidence`。
- 两个生成器均暴露 `generator_type` / `generator_ref` / `prompt_version`（`generator.py:34`、`code_generator.py:141`）。
- `execution_orchestrator.py:1402` 冻结 `GenerationPlan` 前再校验 `self._generator` 与解析策略一致，不一致写 Evidence 并 raise（**双层防御**）。
- `app_delivery.py` API 路径同样走 `build_code_generator(enforce_consistency=True)`，Worker 与 API 行为一致。

### GQ-2 会话内验证反馈闭环 + Verification 扩展（Tech-Spec §13.5/§13.6 / Plan WP-GEN-FEEDBACK、WP-VERIFY-TEST）✅

- `VerificationAgent._run_project_tests`（`verification.py:105`）解析并执行 pytest / 项目测试命令；命令缺失返回 `degraded=True`（**明确降级，非静默跳过**）。
- 工具集已具备执行能力：`agent/tools.py:204` 定义 `run_command`，`VerificationAgent(self._toolkit)`（`agent_runner.py:249`）复用同一 toolkit，pytest 真实执行路径接通。
- `agent_runner.py:251` 验证失败时**恰好一次受控修正轮**（`_allow_nested_repair` 在递归调用置 `False`，不无限递归）；`prior_gaps` 经 assembler（`agent_runner.py:93 gaps=prior_gaps`）进入模型 prompt，真实失败确实回灌。
- `failure_envelope.py` + 迁移 `0041` 捕获真实 build / smoke 失败；`execution_orchestrator.py:2055`（build）、`:2628`（smoke）两处记录。
- `code_generator._build_retry_context` 优先注入真实 FailureEnvelope 失败（"prefer these over gap reasons"），实现下游失败回灌生成会话。

### GQ-0 合同冻结（Plan §13.2 GQ-0）✅

- `failure_envelope.py`：`FailureEnvelope` / `RepairAttempt` / `STAGE_REPAIR_POLICY` / `clip_error_summary`。
- `generation_strategy_policy.py`：canary / kill-switch / shadow 隔离合同；且 `canary_rollout_allowed` 强制 **GQ-2 先闭环才允许 canary**（诊断顺序）。
- `quality_metrics.py`：用户侧质量指标骨架（first_runnable / repair_rounds / human_intervention / wall_time_to_usable）。
- `generation_strategy_experiment.py`：GQ-3 实验骨架；**显式拒绝 P2-4 org 维度**，避免与 MA-5 混淆。
- `docs/gq0-baseline-report-2026-07-31.md`：现状基线报告。
- 迁移 `0041` 的 `down_revision = "20260731_0040"`，链完整。

### GQ-3 / GQ-4 控制流已接线（2026-07-31 补；原「仅钩子」表述作废）✅

- `config.py` 含 `generation_strategy_kill_switch` / `fallback` / `canary_percent` / `canary_variant` / `canary_gate` / `shadow_mode`；默认 `canary_percent=0`、`canary_gate=False`。
  > **口径更新（2026-08-11）**：成稿时曾写「代码默认策略仍 `artifact-backed`」；现行 Settings 代码默认已是 `agentic`，`artifact-backed` 为 kill-switch / scaffold fallback（见 Tech-Spec §0.1 / §13.7）。
- `GeneratorSelector` + `canary_rollout_allowed` / `apply_gq4_promotion` / `drive_generation_strategy_experiment` 已落地（见 `docs/gq34-promotion-control-flow-2026-07-31.md`）。
- 真实 canary 流量窗与 GQ-4 DecisionRecord 晋级仍待数据；运维可用 `REGENT_GENERATION_STRATEGY` 覆盖运行时策略（≠ 正式晋级）。

## 二、测试实证

运行（venv `D:/users/showmac/documents/agentOS/.venv`）：

```text
tests/unit/application/test_generation_quality.py
tests/unit/application/test_aar1_foundation.py
tests/unit/agent/test_agentic_generation.py
→ 53 passed / 0 failed / 0 error
```

覆盖率对照 Plan §13.5 验收：`WP-GEN-SELECT`（两生成器元数据协议 + fail-closed + 双策略分派）、`WP-VERIFY-TEST`（pytest 解析 + 缺失降级）、`WP-CANARY`（稳定分桶 + 报告 + 拒绝 P2-4 维度）、`WP-DEFAULT-GATE`（gq4 门控 + kill-switch）、`WP-GEN-FEEDBACK`（failure_envelope 策略 + clip）均有正例。

## 三、可选增强（非阻塞，不阻碍当前验收）

> 经代码逐模块复核后补全。此前只列了 O1/O3/O4 三条，下述 O5–O12 为本次新核出；O2（修复前/提示注入）已确认闭环，不列入；O7、O13 为疑似缺口但经核实**不成立**，附在末尾以免误报。

### A. 此前已列、本轮复核仍成立
- **O1（运行态）**：artifact-backed 路径不调用 `VerificationAgent`，pytest 目前只作用于 agentic 路径；其真实反馈依赖 FailureEnvelope + 重试上下文（已接通）。若希望 artifact-backed 也在生成会话内跑 pytest，需在该路径显式接入 `VerificationAgent` 或等价调用。
- **O3（小）**：`_run_project_tests` 超时 `timeout_seconds=120` 硬编码（`verification.py:123`），建议抽为配置，与 `agent_max_wall_seconds` 对齐。
- **O4（小）**：一致性检查覆盖 Worker / API / Orchestrator 三处；未来若新增第三条生成入口需同样接线（`GeneratorMismatchEvidence` 可加 `producer` 字段便于归因）。

### B. 本轮新核出（此前遗漏）
- **O5（真实，建议 GQ-3 前补）**：`canary_rollout_allowed(gq2_closed=True)` 已定义但**全代码无调用方**；`build_code_generator` / `resolve_effective_generation_strategy` 从不 consult 它。文档"GQ-2 必须先于 GQ-3 canary"目前是**纸面契约，非代码强制不变式**。
- **O6（真实，范围比 O5 更大）**：`build_code_generator` 在 **Worker 与 API 两条路径都未传 `goal_id`**（`worker/main.py:254`、`app_delivery.py:46`）。而 canary 分支要求 `canary_percent>0 and goal_id:`（`policy.py:50`），故即便配 `generation_strategy_canary_percent>0`，canary 也**结构性不可触发**——当前任何路径都到不了 canary 分支。
- **O8（真实，GQ-4 激活前须知）**：`gq4_default_switch_gate` 是定义好的 hook，但**无调用方**。设计如此（需 GQ-4 DecisionRecord），但 GQ-4 激活时必须接线，否则即便实验 `PROMOTE_AGENTIC_CANDIDATE` 也不会翻转默认策略。
- **O9（真实，GQ-3 仍不可端到端运行）**：`GenerationStrategyExperiment` 是"喂结果"的 harness，需外部调 `.record(StrategyRunResult)`；**没有 driver 把 `default_frozen_task_set` 实际跑两臂并写结果**。GQ-3 "跑实验" 仍需驱动代码，目前不能端到端跑。
- **O10（真实，与 O9 同源）**：`UserQualityMetrics` **无 producer**——`aggregate_user_quality` 被 `report()` 调用，但无任何代码从真实 run 构造 `UserQualityMetrics` 并喂入实验。用户质量指标（first_runnable / repair_rounds / human_intervened / wall_time）当前定义但永不被填，实验 `user_quality` 输出将恒空。
- **O11（小）**：`report()` 把 run `latency_ms` 直接当 `wall_time_to_usable_ms` 用（`generation_strategy_experiment.py:190` `wall_time_to_usable_ms=r.latency_ms if r.passed`），混淆"运行总耗时"与"到可用耗时"两概念。
- **O12（小）**：`shadow_isolation_contract()` / `kill_switch_contract()` 返回文档 dict，仅 `resolve_effective_generation_strategy` 强制 kill-switch；shadow 隔离（禁发布/禁外部副作用）未被代码强制，靠执行方自律。
- **O14（小）**：`resolve_effective_generation_strategy` 返回 `canary_variant` 前不校验其合法性（config 用 `Literal` 约束，函数本身不校验），与策略集不一致。

### C. 疑似缺口但经核实不成立（避免误报）
- **O7（不成立）**：`AgenticCodeGenerator.generate` 完全委托 `AgentRunner.run(verify=True)`（`generator.py:106,125`），故经工厂构建的 agentic 路径**确实跑了 GQ-2 的 VerificationAgent + 受控修正环**。GQ-2 对 worker/agentic 路径生效，无需补。
- **O13（不成立）**：迁移 `0041` 正确建 `failure_envelopes` + `repair_attempts` 表（FK/check 完整），`FailureEnvelopeService.record_failure` 在 `execution_orchestrator.py:2064,2637` 真实写入；持久层健全，非缺口。

> 一句话：非阻塞项远不止四条。最该在 GQ-3 之前补的是 **O5（canary 排序未强制）+ O6（canary 因缺 goal_id 结构性不可达）**；最该在 GQ-3 启动前补的是 **O9/O10（实验无 driver / 用户质量指标无 producer，当前不可端到端运行）**。

## 四、总体

GQ-0..GQ-2 实现与三份文档一致：核心 fail-closed 生成器一致性不变式、会话内验证反馈闭环（pytest + 受控修正 + FailureEnvelope 回灌）均已落地；GQ-3/GQ-4 控制流已实现并接线（见 `docs/gq34-promotion-control-flow-2026-07-31.md`），canary 排序、per-goal 选型、gq4 晋级门、实验驱动、UserQualityMetrics producer 均已闭环。未实现项见 §五。

## 五、GQ-3/GQ-4 实现后的未实现项对照（2026-07-31 补）

### 5.1 已被 GQ-3/GQ-4 控制流关闭（不再是未实现项）
- **O5** canary 排序强制：`resolve_effective_generation_strategy` 现 consult `canary_rollout_allowed(gq2_closed=)`（policy.py:64）。
- **O6** per-goal 选型：`GeneratorSelector.select(goal_id)` 取代启动期单例（generator_factory.py:69；worker/main.py:255；app_delivery.py:46）。
- **O8** gq4 晋级门：`apply_gq4_promotion` 接成晋级前强制门（promotion.py:44）。
- **O9** 实验驱动：`drive_generation_strategy_experiment`（experiment.py:361）。
- **O10** UserQualityMetrics producer：runner 即 producer，`user_quality` 不再恒空。

### 5.2 仍未实现（非阻塞可选增强）
- **O1（中，影响 GQ-3 实验对称性）**：`ArtifactBackedCodeGenerator` 不调 `VerificationAgent`（code_generator.py:140 无 verify 引用），会话内 pytest 仅 agentic 臂生效。若 GQ-3 仅 agentic 臂验证，两臂成功率/首次可运行率**不可比**，实验结论失真。
- **O3（小）**：pytest 超时 `timeout_seconds=120` 硬编码（verification.py:123），建议抽为配置，与 `agent_max_wall_seconds` 对齐。
- **O4（小，含残留风险）**：一致性检查已覆盖 Worker / API / Orchestrator / GenerationService 四处；但旧 `build_code_generator` 普通版仍开放（generator_factory.py:24），建议标记 deprecated，防误用绕过 per-goal 选型。
- **O11（小）**：`report()` 把 run `latency_ms` 当 `wall_time_to_usable_ms`（experiment.py:190），混淆"运行总耗时"与"到可用耗时"。
- **O12（中）**：`resolve_effective_generation_strategy` 仅强制 kill-switch；shadow 禁发布/禁外部副作用未被代码强制，靠执行方自律。
- **O14（已关）**：`resolve_effective_generation_strategy` 现对非法 `canary_variant` 回落 `agentic`（policy.py）。

### 5.3 准阻塞（GQ-4 真正转正的硬前提，非代码缺陷）
- **真实双臂实验窗口**：`drive_generation_strategy_experiment` 已可驱动，但需在真实模型/工具/预算下跑两臂产出带 95% CI 的报告；目前仅能 dry-run / 注入结果。无此真实数据，`gq4_default_switch_gate` 永不过（`PROMOTE_AGENTIC_CANDIDATE` 不可达），agentic 不会成为默认。

### 5.4 建议优先级
1. **先做 5.3 + O1**：真实实验窗口是 GQ-4 转正唯一前提；O1 不修则两臂不可比，实验无效。
2. **再补 O12**：shadow 隔离代码强制，作为安全护栏。
3. **O3 / O4 / O11 / O14** 作为小迭代补强。
