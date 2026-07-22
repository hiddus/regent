# Regent 产品需求文档 V1.1

> 状态：编码基线；替代 V1  
> 日期：2026-07-16

## 1. 产品定义与边界

Regent 接收自然语言目标，在用户授权、资源、约束和治理边界内，自主解释目标、发现与补齐能力、组建人机组织、创建并运营独立应用，并依据外部证据持续调整计划和组织。

```text
regent/
├─ core/   # 通用自治组织运行内核
└─ apps/   # Regent 创建和运营的独立应用
```

Core 只包含目标、能力、组织、工作、执行、证据、策略和资源等通用语义。每个 App 拥有独立源码、依赖、数据、测试和部署，可以脱离 Core 运行。用户唯一必填输入是自然语言 Goal；附件、约束、资源、期限和偏好均可选。GoalSpec 是内部解释结果。

## 2. P0 能力

1. 保存原始 Goal，生成版本化 GoalSpec，区分显式约束、推断和未知项；
2. 持久化 Goal、Work、Run、Artifact、Evidence、HumanTask 和审计，进程重启后恢复；
3. 从 Goal 和计划推导能力需求，区分能力、权限、资源、信息和普通执行失败；
4. 按复用、配置、组合、构建、请求人类的顺序补齐能力；
5. 根据能力覆盖、并行收益、隔离、成本和风险形成最小组织，并可收缩、替换或解散；
6. 在 `apps/<app-id>` 创建、构建和测试独立 App，不向 Core 引入 App 业务模型；
7. 通过外部 Observation 和 Evidence 评价进展并重规划；
8. 所有副作用行动经过策略判断和一次性 ExecutionPermit；
9. 人工输入与审批使用独立 HumanTask，等待期间不占用 Worker；
10. 支持暂停、恢复、取消、预算限制、无进展停止和完整审计。

## 3. Goal 终态

- `ACHIEVED`：成功标准已有充分证据；
- `EXHAUSTED`：当前硬约束和资源上限内已无可行路径；
- `FAILED`：不可恢复的系统或状态完整性错误；
- `CANCELLED`：Goal Owner 主动终止。

`PAUSED`、`WAITING_HUMAN` 和 `BLOCKED` 均为可恢复状态，不是终态。终态 Goal 不重新打开；用户改变目标或资源后应创建新 Goal，并引用原 Goal。

## 4. Work 与 Run

Work 是计划中的逻辑工作单元，保存稳定的目的、输入引用、验收标准、依赖、优先级和预算。Run 是执行某个 Work 的一次不可变尝试，绑定实际执行者、模型、工具、输入版本和 Permit。

```text
Goal 1 ──* Work 1 ──* Run
```

一个 Work 可以因重试、换 Agent 或换 Tool 产生多个 Run，但同一时刻最多一个活动 Run。历史 Run 永不覆盖。Run 执行成功后仍需独立 Evaluator 接受 Evidence，Work 才能完成；Work 的目的或验收发生变化时必须创建新 Work 或新版本。

## 5. 组织、调度与冲突

Organization 定义能力主体、角色、责任、授权、委派和退出条件；Scheduler 只负责已批准 Work 的优先级、并发、资源、Timer 和 Lease。Planning 与 Organization 交替迭代。

子目标冲突依次依据根 Goal、用户成功条件、硬约束和策略、资源不可共存、证据质量、全局收益/成本/风险及可逆性裁决。仍无法确定时创建 HumanTask。裁决必须保存候选、证据、被拒原因和影响范围。

## 6. ExecutionPermit 产品约束

Permit 绑定 Goal、Work、Run、Actor、动作、目标环境、参数哈希、数据和网络范围、资源上限、有效期、一次性 nonce 与副作用幂等键。参数或目标变化必须申请新 Permit。Permit 只能使用一次；动作成功、失败或结果未知后均不可重用。Permit 不包含明文凭证。

## 7. P0 固定基准

`CSV_SUMMARY_BASELINE` 是工程验收夹具：

```text
Goal：读取授权目录中的 orders.csv，生成 summary.json。
输入列：id, amount
固定数据：
1,12.50
2,7.50
3,INVALID
4,10.00
约束：禁止联网；不得修改输入；只能写入 output/。
期望输出：{"row_count":4,"valid_count":3,"invalid_count":1,"total_amount":30.0}
```

自动验收必须证明：原始 Goal 被保存；GoalSpec 区分约束和推断；至少形成一个 Work 和一个 Run；输出逐字段相等；Evidence 含输入与输出哈希；杀死 Worker 后可恢复；相同幂等键重放不产生第二份输出；最终 Goal 为 `ACHIEVED`。字段错误、越界写入或只有执行者自评均不得通过。

## 8. 首批长期 Goal

- 创建并运营面向 AI 从业者的产品，第一阶段达到 100 个有效付费用户；
- 创建并运营面向成年人的短篇童话网站，第一阶段达到 10,000 有效 DAU。

两者使用同一自然语言 Goal API。业务指标来自外部可验证数据，不作为 P0 编码完成条件，也不得反向固化 Core。

## 9. P0 完成定义

Core 在空 Apps 条件下通过固定基准；随后仅凭普通 Goal 形成可解释的最小组织，补齐至少一个能力缺口，在独立 Apps 目录创建可运行产品候选。运行可恢复、副作用幂等、高风险行动受控，新 App 接入不改变 Core 领域模型。
