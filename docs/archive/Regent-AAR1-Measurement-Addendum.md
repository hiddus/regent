# Regent AAR-1 组织选择与实验测量增补

> 状态：CURRENT  
> 日期：2026-07-27  
> 继承：`Regent-Measurement-Decision-Framework.md`；AAR-1 范围冲突时本增补优先

## 1. 有限可行候选

`argmax` 仅表示在本次有限、已通过 C/V/R 硬约束的候选集上最大化冻结预测效用：

```text
F_t = {O ∈ Candidates_t | C(O)=PASS ∧ V(O)=PASS ∧ R(O)=PASS}
O_hat = arg max_{O ∈ F_t} U_hat(O | X_t)
```

UNKNOWN 按不可行处理；无可行候选返回 `NO_FEASIBLE_ORGANIZATION`。当前启发式只能命名为 `HeuristicUtilityV1`，不得称为校准成功概率或全局最优。

`predicted_utility` 与 `realized_utility` 分开保存。综合效用只用于候选排序；成功/质量是主指标，成本、延迟、人工为关键次指标，安全为不可抵消 Guardrail。

## 2. 数据与谱系

每次决策必须记录 candidate set hash、Goal/task class、特征与 C/V/R 快照、Policy/Resource/State/Utility/Selector 版本、所有候选的判定与预测区间、选中版本、模型/工具/Prompt 版本、随机分层、seed 与 assignment probability。

每次结果必须记录 acceptance、盲审质量、总成本、墙钟、超时/删失、人工分钟、风险事件、违规、重试、协调 Token、Agent 数、realized utility、Evidence hash 和失败分类。未选候选保留预测，但不得当作反事实真实结果。

## 3. 离线评估

- 推断单位是 `task_id`/业务 Goal；同任务重复只估计噪声，先在 task 内聚合。
- 各 Variant 在同任务上配对，执行顺序随机；按任务类型、风险、规模和能力缺口分层。
- 成功率使用 McNemar 或 task-level 配对 bootstrap CI；连续指标使用配对 bootstrap CI。
- 样本量由预注册 baseline、MDE、α=0.05、power≥0.80 决定；固定 n=30 不自动充分。
- 样本或功效不足统一输出 `INCONCLUSIVE`。
- 默认配置初值：成功率优效≥5pp且95% CI下界>0；或非劣界−2pp且成本或 p95 延迟改善≥15%。最终数值在 Eval Manifest 冻结。
- 观察到 0 次严重安全事件仍须报告事件率单侧 95% 上界。

## 4. Champion/Challenger

Champion 为已批准的单 Agent/固定模板。Challenger 按 1%→5%→20%→50% 分阶段；以 Goal/task 随机，按 task class、risk tier、org 分层并检查 SRM。

实验必须采用固定样本，或版本化 group-sequential alpha-spending；禁止持续查看普通 p 值后提前晋级。多 Challenger/多主指标使用 Holm 校正或预注册唯一主指标。

Critical 安全、隐私、越权、Permit 绕过、职责分离失败、随机化/数据完整性异常或预算硬上限命中时：

```text
停止分流 → 取消未开始任务 → 安全收尾/隔离在途副作用
→ 保存证据 → 通知 Owner → 禁止同版本自动恢复
```

## 5. 防振荡、KPI 与漂移

重组必须具有最小驻留、冷却、最大次数和效用改善滞后；同一原因连续两次失败升级人工。禁止无限增加 Agent。

KPI 状态：

```text
INSUFFICIENT_DATA → BASELINE → IMPROVING/STABLE/DEGRADING
→ TARGET_SUSTAINED
```

每个 KPI 版本化保存公式、单位、方向、目标区间、窗口、分母、基线、季节分层、迟到策略、数据源和 Owner。`TARGET_SUSTAINED` 默认要求连续 4 个完整窗口达标且 95% 区间不越界，并可因退化退出。

输入、结果/残差或数据质量漂移触发冻结晋级、回退 Champion/heuristic、安全重评和新实验版本。阈值由基线回放校准，不把通用常数当作无条件科学结论。

## 6. Rollout Gate

同时满足才允许 `ROLLOUT_ALLOWED`：

1. 预注册主指标达到优效或非劣门槛；
2. 成本或延迟至少一项达到预注册实际收益；
3. 全部安全、治理、恢复和数据完整性 Guardrail 通过；
4. 评价器与生成者隔离，盲评和机器验收完整；
5. 形成唯一、签名、可重放的 Eval DecisionRecord。

`INCONCLUSIVE` 保留 Champion，不等于 Challenger 失败，也不能开放 Rollout。

