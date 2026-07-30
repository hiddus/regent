# GQ-0 现状基线报告（2026-07-31）

> 合同冻结交付物。不构成 GQ-3/GQ-4 实验结论；门槛已预注册，实验窗口未开。

## 1. 合同冻结摘要

| 合同 | 落点 | 状态 |
|---|---|---|
| 生成器元数据协议 | `FileChangeSetGenerator` + `generator_metadata.py` | ✅ 冻结 |
| 类型错位 fail-closed | `GENERATOR_METADATA_MISMATCH`，禁止静默回退 | ✅ 冻结 |
| FailureEnvelope / RepairAttempt | 迁移 `0041` + `failure_envelope.py` | ✅ 骨架落地 |
| 独立生成策略实验 | `generation_strategy_experiment.py`（非 P2-4 组织维） | ✅ 合同冻结 |
| 用户质量指标 | `quality_metrics.py`（首次可运行率/修正轮次/人工介入） | ✅ 骨架 |
| Canary / 影子 / kill switch | `generation_strategy_policy.py` + Settings | ✅ 钩子；canary% 默认 0 |
| 预注册门槛 | `PreregisteredThresholds`（样本量/lift/成本/延迟/护栏） | ✅ 登记 |

## 2. 代码现状（基线事实）

- 默认 `REGENT_GENERATION_STRATEGY=artifact-backed`。
- Worker 经 `build_code_generator` 按有效策略分派（GQ-1）；不再硬编码 artifact-backed。
- 生产 `REGENT_AAR1_CERTIFIED_HIVE` 既有 opt-in **保持**；代码默认仍 False；**GQ-5 前不得扩容**。
- P2-5 自适应拓扑仍 `ROLLOUT_NOT_ALLOWED`。
- Canary 百分比默认 0；诊断顺序要求 GQ-2 反馈闭环后再开 canary（`canary_rollout_allowed`）。

## 3. 预注册门槛（实验前已登记）

见 `default_preregistered_thresholds()`：

- 最小成功率提升 5% 或非劣界 2%
- 最大 mean cost 退化 25%；P95 延迟退化 30%
- 每臂最少样本量 30
- 平均修正轮次 ≤ 3；人工介入率 ≤ 20%
- 安全事件 / 严重质量回退 → 停止

## 4. 明确未完成（后续批次）

| 批次 | 剩余 |
|---|---|
| GQ-2 | 生产路径上 FailureEnvelope→再生成已接线；完整端到端修正成功率待真实任务窗 |
| GQ-3 | 影子隔离 runner 与生产 canary 流量窗（钩子已备，**尚未开流量**） |
| GQ-4 | 默认切换 DecisionRecord 需 GQ-3 报告；kill switch 语义已冻结 |
| GQ-5 / MA-5 | 强单 Agent 基线晋级前不跑真实组织扩容实验 |

## 5. 验收指向

- 单元：`tests/unit/application/test_generation_quality.py`
- 工作包：`WP-GEN-SELECT` / `WP-GEN-FEEDBACK` / `WP-VERIFY-TEST` 骨架；`WP-CANARY` / `WP-DEFAULT-GATE` 为钩子
