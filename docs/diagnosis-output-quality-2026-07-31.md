# 输出质量诊断（修订版 v2，2026-07-31）

> 对象：Regent 生成链路（`config.py` / `worker/main.py` / `code_generator.py` / `agent_runner.py` / `execution_orchestrator.py` / `execution_service.py` / `verification.py` / `p24_frozen_experiment.py` / `hive_runtime.py`）
> 性质：诊断文档（RESEARCH），不改任何验收口径。
> 修订：v1 经专家复核，约 70–80% 成立；本版收回 4 处过强/事实偏差表述，并采用复核建议的总论与行动顺序。

## 修订说明（相对 v1 的变更）

| v1 表述 | 复核结论 | 本版处理 |
|---|---|---|
| "默认路径无真实执行验证" | 不准确：下游有依赖解析、构建、部署后浏览器 smoke | 改为"缺**会话内**执行反馈闭环；下游验证存在但晚、纠错贵" |
| "`VerificationAgent.run_smoke` 跑 pytest" | 错误：仅 `compileall` + 起服务 + 探测 ≤4 路由 | 删除 pytest；把"接入项目测试命令"列为新增需求 |
| "开蜂巢只会放大问题" | 未被当前证据证明（17.2× 是特定研究条件） | 改为"净收益未经真实任务实验确认，不应假定必然改善或必然放大" |
| "Hive Dev 仍用同一个 ArtifactBackedCodeGenerator" | 仅适用于**应用代码生成路径**；普通 work 的 Dev 是独立结构化调用 | 限定讨论范围 |
| "P2-4 只测协调指标" | 错误：已含 pass_rate / Wilson CI / cost | 改为"缺代表性真实任务样本与可靠端到端成功判定" |
| "须接真实强模型" | 默认 None 不能证明生产模型弱 | 降级为"待核验项" |

**确证成立（v1 核心结论保留）**：主 Worker 固定构造 `ArtifactBackedCodeGenerator`（`worker/main.py:252`），未按 `generation_strategy` 选择；Agentic 真循环主要从 API delivery 分支实例化（`app_delivery.py:47`），未接入主 Worker；编排器按配置填 `generator_ref` 但不更换已注入的生成器（`execution_orchestrator.py:1389`），故存在"标为 agentic、实际仍 artifact-backed"的可能。方向结论正确：**当前最该优先修复的是基础单 Agent 生成闭环，而非继续增加组织层复杂度。**

## 一、确认成立的部分

- 主 Worker 确实固定构造 `ArtifactBackedCodeGenerator`，没有根据 `generation_strategy` 选择生成器。（`worker/main.py:252`）
- `ArtifactBackedCodeGenerator` 基本是一次结构化整包生成（`code_generator.py:180` `generate_structured`）；失败后再生成主要依赖文字化问题摘要（`code_generator.py:266` `_build_retry_context`），缺少生成器内部"运行—读错—修改"闭环。
- 编排器根据配置填写 `generator_ref`，但不负责更换已注入的生成器对象（`execution_orchestrator.py:1389`），存在"标为 agentic、实际仍 artifact-backed"的可能。
- Agentic 生成器目前主要从 API delivery 分支实例化，没有接入主 Worker。（`app_delivery.py:47`）
- 因此"当前最值得优先修复的是基础单 Agent 生成闭环，而不是继续增加组织层复杂度"这一方向正确。

## 二、需要修正的部分（附代码证据）

1. **"默认路径无真实执行验证"不准确**
   下游确实存在真实执行验证：依赖解析与构建（`execution_orchestrator.py:1992` `materialize_dependencies` / `execute_build`）、构建失败进入恢复（`execution_orchestrator.py:2013` `_halt_goal_stage`）、部署后浏览器/端点 smoke（`execution_orchestrator.py:2551` `DeploymentSmokeTestService.run_smoke_test` + `BrowserJourneyRunner`）。
   → 改为：默认生成器缺少"真实执行结果回灌到同一次生成会话"的闭环；下游验证存在，但反馈发生较晚、纠错成本高。

2. **`VerificationAgent.run_smoke` 不运行 pytest**
   实际执行：`compileall` → 启动 Flask/ASGI 应用 → 探测最多四个 HTTP 路由（`verification.py:90` `_smoke_http`）。
   → 删除"pytest"；"接入 pytest / 项目测试命令"列入新增需求。

3. **"开蜂巢只会放大问题"未被当前证据证明**
   17.2× 是特定研究条件下的误差放大结果，不能直接套用到 Regent。当前固定 Hive 有集中式 PM 与独立 QA，并非"无验证的独立 Agent"：PM 生成执行计划（`execution_service.py:230`）、Dev 执行（`execution_service.py:269`）、QA 独立评价（`execution_service.py:305`）。
   → 改为：当前没有实验证据证明 Hive 能改善应用交付质量；弱基础生成器可能限制 Hive 收益，因此不应在强单 Agent 基线建立前扩大启用范围。

4. **"Hive Dev 仍使用同一个 ArtifactBackedCodeGenerator"只适用于应用生成路径**
   普通 work execution 的 Hive Dev 是单独的 `_provider.generate_structured(...)` 结构化调用（`execution_service.py:269`），并非 `ArtifactBackedCodeGenerator`。应用代码生成路径中，Hive 调度确实没有替换主 Worker 注入的生成器。
   → 文档需限定讨论范围：该论断仅针对"应用代码生成"路径。

5. **P2-4 不只是协调指标**
   当前 `summarize_variant` 已包含：`pass_rate`、`ci95`（Wilson）、`mean_cost`、`cost_per_verified_success`（`p24_frozen_experiment.py:66`）。真正的问题是它更接近实验框架，尚缺有代表性的真实任务样本和可靠的端到端成功判定，而非"只测协调指标"。

6. **模型默认值不能证明生产模型较弱**
   `model_name=None` / `base_url=None` 只是代码默认值；生产环境可能通过环境变量配置。除非进一步检查生产配置与实际调用记录，否则"须接真实强模型"只能算待核验项。

## 三、修订后的总论（采纳复核建议）

> Regent 当前的主要质量瓶颈，是主 Worker 未将已有 Agentic 生成循环接入默认交付路径。Artifact-backed 路径虽然拥有下游构建和部署验证，但缺少低延迟、会话内的执行反馈自纠正闭环。固定 Hive 的净收益目前未经真实任务实验确认，不应假定它必然改善或必然放大质量问题。优先事项应是修复生成器选择一致性、建立 artifact-backed 与 agentic 的真实任务对照基准，并将构建、测试和 smoke 失败可靠地回灌至生成循环；之后再评估 Hive。

## 四、对比 Claude Code 的缺口（修订）

| 能力 | Regent 现状 | 与 Claude Code 差距 |
|---|---|---|
| 主 Worker 接入 agentic 循环 | 仅 API 路径有，主路径硬编码 artifact-backed | 🔴 差距大 |
| 会话内执行反馈自纠正闭环 | artifact-backed 无；仅下游构建/部署验证（晚、贵） | 🔴 差距大 |
| 工具接地 / 环境交互 | 仅 agentic 路径 `WorkspaceToolkit` | 🟠 中 |
| 真实失败回灌生成循环 | 下游 smoke 失败进入恢复，但非同会话闭环 | 🟠 中 |
| 单 Agent 端到端基准 | P2-4 已有 pass_rate/Wilson/cost，但缺代表性样本与可靠成功判定 | 🟠 中 |
| pytest / 项目测试命令 | `run_smoke` 仅 compileall + 起服务 + ≤4 路由探测 | 🟠 中（新增需求） |
| 模型强度 | 默认 None，生产可能已配置——**待核验** | ⚪ 未知 |
| 治理壳（Gate/指标/认证/A2A/持久化/裁剪） | 完整先进 | ✅ 领先 |
| 下游构建 + 部署浏览器 smoke | 已有（`execution_orchestrator.py:1992/2013/2551`） | ✅ 具备 |

## 五、修订后的行动顺序（采纳复核建议）

1. **修复 Worker**，使其真正遵循 `generation_strategy`（`worker/main.py:252` 按标志选择 `AgenticCodeGenerator`）。
2. **增加生成器实际类型与 `generator_ref` 一致性检查**，消除"标 agentic、实 artifact-backed"的错位。
3. **补充 pytest / 项目测试命令**，并以持久化失败信封和修正尝试把构建、测试和 smoke 的真实失败可靠回灌至生成循环。
4. **完成验证闭环后，再用隔离影子任务或小比例 canary 比较** artifact-backed 与 agentic；实验门槛、任务集、样本量、停止规则和副作用隔离须预先冻结。
5. **达到成功率、成本和延迟门槛后**，再将 agentic 设为默认。
6. **最后基于强单 Agent 基线重新评估固定 Hive**（此时 P2-4 才有意义）。

> 蜂巢在当前状态下既不应假定必然改善，也不应假定必然放大；结论应留给第 6 步的真实对照实验。
