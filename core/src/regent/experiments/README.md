# core/src/regent/experiments

实验平台支撑：冻结任务集、基线、盲评、统计 Gate、DecisionRecord。

## GQ 灰度状态：已实现，但默认不可启用

GQ-0～GQ-4 的控制流**均已落地**（`application/generator_metadata.py:67 assert_generator_consistency`、`generator_factory.py:125 build_generator_selector`、`generation_strategy_policy.py`、`generation_strategy_promotion.py:45 apply_gq4_promotion`、`generation_strategy_experiment.py:333 gq4_default_switch_gate`）。

当前**缺的是实验而非代码**：GQ-3 真实流量窗与 GQ-4 晋级 DecisionRecord 尚未产出。

> **CD-0.1（Agent 沙箱隔离）已完成**，`config.py:72` 与 `sandbox.py:452` 对生产环境强制 `sandbox_mode=docker`，Technical-Spec §13.7 的「独立 sandbox」前置在**规范层面**已满足。
>
> 🔴 但在打开 `canary_gate` 前仍须先解决 **N-3**：docker 模式下 agent 命令因缺 `--entrypoint` 实际不会被执行（见 `infrastructure/README.md` 沙箱现状）。影子/canary 任务依赖该路径，**当前打开 canary 会得到无效的实验数据**。
>
> 「已实现但不可启用」的统一状态标签已建立，见 `Regent-Technical-Spec.md:782-789`（F-9 已闭环）。

## 目录内容

文件：
- `__init__.py`
- `report.py`
- `runner.py`

> 本 README 由目录实际内容生成，反映当前结构；如用途有变请同步更新。
