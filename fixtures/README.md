# Fixtures（测试与评测固定数据）

存放测试与评测使用的固定输入数据，保证测试可复现、Eval 可比对。

## 当前内容

- `eval_task_set_v1.json` — **评测任务集 v1**。用于 Eval 工具链（`tests/integration/test_eval_harness_e2e.py`）与 Graduation 矩阵验证，定义一组标准化评测任务及其预期口径。
- `eval_single_agent_baseline_v1.json` — **强单 Agent 基线任务集 v1**。对照实验 A 组（强单 Agent 基线）的固定任务与口径，与 `eval_task_set_v1.json` 配对用于 A/B/C 净收益比对。

新增固定数据请保持版本化命名（`*_vN.json`），并在对应测试/评测文档中说明用途与来源，避免硬编码到测试逻辑中。

> 区分：本目录是**评测输入**；实验产物存 [`docs/experiments/`](../docs/experiments/)；Graduation 签名证据存 [`docs/graduation-evidence/`](../docs/graduation-evidence/)。
