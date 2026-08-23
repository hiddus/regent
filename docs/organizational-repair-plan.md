# Regent 组织修复计划

> **落地状态（2026-08-23 标注）**：本计划为历史规划文档。其中阶段 1（subagent 深度收紧、hub-and-spoke 纪律）、目标分类、组织模式选择、运行时行为监测均已实现并接入执行主链（commit `40e5378` 起）；worker 周期监测 tick（本文 §"Worker 主循环"提案）以 `application/behavior_monitor_tick.py` 落地（默认 600s）；`behavior_repair_loop` 已从"仅写 `session_steer_brief`"演进为**护栏内自动再调度**（合并写入 steering + 行锁事务 + retrigger claim，见 `Regent-Architecture-Comparison-2026-08-23.md` §3 R3 与 `Regent-Plan.md` §15.5）。本文代码片段为提案原文，与最终实现有出入，以代码为准。

## 执行摘要

基于对 6065 行 `execution_orchestrator.py`、615 行 `delivery_role_swarm.py`、475 行 `agent_task_service.py`、514 行 `delivery_role_runtime.py`、321 行 `delivery_role_agents.py`、1475 行 `agent_runner.py` 的完整分析，制定以下修复计划。

**核心发现**：
- 执行管线锁定瀑布模式（Discovery→Requirement→Capability→Generation→Build→Preview）
- Agent 互调路径存在死循环风险（subagent depth≤3，delegate_plan_item 可嵌套）
- 无运行时行为监控（仅有部署时一次性检查）
- 无目标特征分析（所有目标用同一模式）
- Delivery Role Swarm 是"一次性门禁"而非"持续观察者"

---

## 阶段 1：切断执行 Agent 互调路径（P0 — 止血）

### 1.1 问题定位

**互调路径**：
1. `agent_runner.py` L928-950: `delegate_plan_item` → `SubagentRunner`（嵌套 depth≤3）
2. `delivery_role_runtime.py` L179-218: product→tech SUPERVISES/DELEGATES_TO + test/ux/ops→tech REVIEWS
3. `agent_task_service.py`: offer_task/claim_task 允许 deployment 间任务路由

**危害**：
- 嵌套 subagent 产生指数级 token 消耗
- Agent A 调 Agent B，B 再调 A → 死循环
- 每个中间层消耗 token 但不产生交付物价值

### 1.2 修复方案

**原则**：星型调度（Hub-and-Spoke），不允许 spoke-to-spoke。

#### 1.2.1 收紧 subagent 深度限制

文件：`core/src/regent/agent/agent_runner.py`
- `max_subagent_depth` 默认从 3 降为 1
- depth=0（主 Agent）可 delegate 给 subagent
- depth=1（subagent）**禁止**再次 delegate

```python
# agent_runner.py __init__
max_subagent_depth: int = 1,  # was 3
```

#### 1.2.2 切断 delivery role 间的直接关系

文件：`core/src/regent/application/delivery_role_runtime.py`
- 移除 product→tech 的 SUPERVISES/DELEGATES_TO 关系
- 保留 REVIEWS 关系但改为单向（reviewer→tech），不允许 tech→reviewer 反向
- 所有角色只向协调者（orchestrator）汇报，不互相调用

```python
# 移除 L186-200 的 SUPERVISES / DELEGATES_TO 关系创建
# 保留 L201-212 的 REVIEWS 但确保单向
```

#### 1.2.3 添加跨 Agent 调用守卫

文件：新建 `core/src/regent/application/agent_invocation_guard.py`
- 在 `AgentTaskService.offer_task()` 中检查：source 和 target 是否存在循环依赖
- 拒绝同一 goal 内的 A→B→A 路径

### 1.3 测试

- `tests/unit/application/test_agent_invocation_guard.py`
- 验证：depth=1 的 subagent 调用 delegate_plan_item 被拒绝
- 验证：delivery role 间无直接任务路由

---

## 阶段 2：目标特征分析与组织模式选择（P1）

### 2.1 问题定位

文件：`execution_orchestrator.py` L521-557 `_is_direct_generation_goal`
- 仅有"SMALL + 短文本 + 无外部证据 → 直接生成"的二元判断
- 无多维特征分析（复杂度、领域、交互性、迭代需求）
- 所有目标强制走同一条管线

### 2.2 修复方案

#### 2.2.1 目标特征分析器

文件：新建 `core/src/regent/application/goal_classifier.py`

```python
@dataclass
class GoalProfile:
    scale: str          # SMALL / MEDIUM / LARGE
    domain: str         # static-web / interactive-app / api-service / data-pipeline / other
    complexity: str     # LOW / MEDIUM / HIGH
    iteration_need: str # NONE / LIGHT / HEAVY
    monitoring_need: str # NONE / BASIC / CONTINUOUS

class GoalClassifier:
    def classify(self, goal_input: str, spec: GoalSpecModel, metadata: dict) -> GoalProfile:
        """Analyze goal characteristics and return a profile."""
```

分类规则：
- **static-web + LOW complexity** → 敏捷快速模式（跳过 Discovery/Requirement，直接 Generation）
- **interactive-app + MEDIUM complexity** → 迭代模式（Generation → Monitor → Fix 循环）
- **api-service + HIGH complexity** → 分阶段模式（完整瀑布 + 里程碑）
- **data-pipeline** → 批处理模式（无 Preview，用数据验证）

#### 2.2.2 组织模式选择器

文件：新建 `core/src/regent/application/organization_mode_selector.py`

```python
class OrganizationMode:
    WATERFALL = "waterfall"      # 完整管线
    AGILE_ITERATIVE = "agile"    # 快速迭代
    HUB_AND_SPOKE = "hub_spoke"  # 中心辐射
    BATCH = "batch"              # 批处理

def select_mode(profile: GoalProfile) -> OrganizationMode:
    ...
```

#### 2.2.3 集成到 execution_orchestrator

文件：`execution_orchestrator.py`
- 在 `_is_direct_generation_goal` 之后，加入 `GoalClassifier.classify()` 调用
- 根据 `OrganizationMode` 选择不同的管线路径
- 将分类结果写入 `goal.metadata_json["goal_profile"]`

### 2.3 测试

- `tests/unit/application/test_goal_classifier.py`
- `tests/unit/application/test_organization_mode_selector.py`

---

## 阶段 3：独立全局监控组件（P1）

### 3.1 问题定位

现有监控：
- `host_guard.py` + `host_resources.py`：监控磁盘/内存/进程（基础设施层）
- `delivery_role_swarm.py`：部署时一次性审查（非持续）
- 无应用行为监控（不观察"虚拟小镇对话是否合理"等）

### 3.2 修复方案

#### 3.2.1 运行时行为监控器

文件：新建 `core/src/regent/application/runtime_behavior_monitor.py`

```python
@dataclass
class BehaviorObservation:
    goal_id: uuid.UUID
    observed_at: datetime
    metric_name: str        # e.g. "dialogue_time_distribution"
    metric_value: Any       # e.g. {"night_ratio": 0.8, "expected_max": 0.3}
    anomaly: bool
    severity: str           # NONE / LOW / MEDIUM / HIGH
    detail: str

class RuntimeBehaviorMonitor:
    """Independent observation loop — runs outside the execution pipeline."""

    async def observe(self, goal_id: uuid.UUID, preview_url: str) -> list[BehaviorObservation]:
        """Fetch preview endpoint and analyze behavioral quality."""

    async def observe_dialogue_realism(self, goal_id, preview_url) -> BehaviorObservation:
        """Check if character dialogues respect time-of-day constraints."""

    async def observe_content_diversity(self, goal_id, preview_url) -> BehaviorObservation:
        """Check if content has sufficient variety (not repetitive)."""
```

**关键设计**：
- **独立于执行管线**：不嵌入 `execution_orchestrator.py`，作为独立后台任务
- **只观察不干预**：产生 `BehaviorObservation` 写入数据库，不直接修改应用
- **可配置检查项**：每个目标类型有不同的检查策略

#### 3.2.2 监控循环集成

文件：`core/src/regent/worker/main.py`
- 在 Worker 主循环中添加 `runtime_behavior_monitor` tick（类似 host_guard）
- 间隔可配置（默认 5 分钟）
- 观察结果写入 `goal.metadata_json["behavior_observations"]`

```python
# worker/main.py run() loop
if monotonic() >= self._next_behavior_monitor:
    observations = await self._behavior_monitor.observe(goal_id, preview_url)
    # Write to goal metadata
    self._next_behavior_monitor = monotonic() + self._behavior_monitor_interval
```

#### 3.2.3 监控 API 端点

文件：`core/src/regent/api/main.py`
- `GET /v1/goals/{goal_id}/behavior` — 返回最新行为观察结果
- `GET /v1/goals/{goal_id}/behavior/history` — 返回历史观察趋势

### 3.3 测试

- `tests/unit/application/test_runtime_behavior_monitor.py`

---

## 阶段 4：运行时修复闭环（P2）

### 4.1 问题定位

当前"发现→修复"路径：
1. Delivery Role Swarm 拒绝 → `evolve_failed_delivery_roles` 修改 skill → 重新生成
2. 但这是部署时触发，不是运行时
3. 运行时问题（对话不合理、角色单一等）无修复路径

### 4.2 修复方案

#### 4.2.1 观察→决策→修复 管线

文件：新建 `core/src/regent/application/behavior_repair_loop.py`

```python
class BehaviorRepairLoop:
    """Connects monitoring observations back to the agent for repair."""

    async def evaluate_and_repair(
        self,
        goal_id: uuid.UUID,
        observations: list[BehaviorObservation],
    ) -> RepairDecision:
        """
        1. Aggregate observations since last repair
        2. If anomaly severity >= MEDIUM, inject repair steering
        3. Steer = conversation message to the agent with concrete fix instructions
        """

    async def _inject_repair_steering(
        self,
        goal_id: uuid.UUID,
        anomalies: list[BehaviorObservation],
    ) -> None:
        """Write session_steer_brief into goal metadata to guide next agent turn."""
```

**流程**：
```
Monitor observes → anomalies detected →
  BehaviorRepairLoop evaluates →
    severity >= MEDIUM → inject steering into conversation →
      agent picks up steering on next turn → fixes behavior →
        monitor re-observes → confirms fix
```

#### 4.2.2 集成到 Worker 循环

文件：`core/src/regent/worker/main.py`
- 在 behavior monitor tick 之后，调用 `BehaviorRepairLoop.evaluate_and_repair()`
- 如果产生 repair steering，通过 `_append_conversation_event` 注入对话

### 4.3 测试

- `tests/unit/application/test_behavior_repair_loop.py`

---

## 阶段 5：简化 execution_orchestrator 状态机（P2）

### 5.1 问题定位

`execution_orchestrator.py` 6065 行，状态过多：
- DISCOVERING → GATE_INSUFFICIENT_EVIDENCE → PREVIEW_SUCCEEDED → SMOKE_FAILED → PREVIEW_PRODUCT_QA_FAILED → DELIVERY_SOFT_PAUSE → WAITING_HUMAN → ...
- 状态转换散落在多个方法中，难以追踪

### 5.2 修复方案

#### 5.2.1 提取状态机到独立模块

文件：新建 `core/src/regent/application/execution_state_machine.py`

```python
class ExecutionPhase(Enum):
    CLASSIFYING = "classifying"          # 目标分类
    PIPELINE_RUNNING = "pipeline"        # 瀑布/敏捷管线
    DELIVERING = "delivering"            # 部署 + QA
    MONITORING = "monitoring"            # 运行时监控
    REPAIRING = "repairing"              # 行为修复
    COMPLETED = "completed"              # 达成
    BLOCKED = "blocked"                  # 等待人工

VALID_TRANSITIONS = {
    CLASSIFYING: [PIPELINE_RUNNING, COMPLETED, BLOCKED],
    PIPELINE_RUNNING: [DELIVERING, BLOCKED],
    DELIVERING: [MONITORING, REPAIRING, BLOCKED],
    MONITORING: [REPAIRING, COMPLETED, MONITORING],
    REPAIRING: [MONITORING, DELIVERING],
    ...
}
```

#### 5.2.2 简化 execution_orchestrator

- 将散落的 `metadata["execution_stage"] = ...` 收归到状态机模块
- execution_orchestrator 只调用 `state_machine.transition(goal_id, new_phase)`

---

## 执行优先级与依赖

```
阶段 1（止血）──→ 阶段 2（分类）──→ 阶段 3（监控）──→ 阶段 4（修复闭环）
                                                              ↓
                                                    阶段 5（状态机简化）
```

**阶段 1** 无依赖，立即执行。
**阶段 2** 依赖阶段 1（先切断互调，再引入新模式）。
**阶段 3** 可与阶段 2 部分并行（监控器独立于分类器）。
**阶段 4** 依赖阶段 3（需要观察数据才能修复）。
**阶段 5** 可在任何阶段后执行（重构性质）。

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| subagent depth 降为 1 可能影响 LARGE 目标 | 保留 metadata override：`goal.metadata_json["max_subagent_depth"]` 可覆盖 |
| 行为监控增加服务器负载 | 默认 5 分钟间隔 + 可配置关闭 |
| 状态机重构影响现有管线 | 阶段 5 最后执行，先保证前 4 阶段稳定 |
| 修复闭环与现有 delivery_role_swarm 冲突 | 修复闭环只在 MONITORING 阶段激活，不影响 DELIVERING |

---

## 文件变更清单

### 新建文件
1. `core/src/regent/application/goal_classifier.py` — 目标特征分析
2. `core/src/regent/application/organization_mode_selector.py` — 组织模式选择
3. `core/src/regent/application/runtime_behavior_monitor.py` — 运行时行为监控
4. `core/src/regent/application/behavior_repair_loop.py` — 行为修复闭环
5. `core/src/regent/application/execution_state_machine.py` — 执行状态机
6. `core/src/regent/application/agent_invocation_guard.py` — Agent 互调守卫
7. `tests/unit/application/test_goal_classifier.py`
8. `tests/unit/application/test_organization_mode_selector.py`
9. `tests/unit/application/test_runtime_behavior_monitor.py`
10. `tests/unit/application/test_behavior_repair_loop.py`
11. `tests/unit/application/test_agent_invocation_guard.py`

### 修改文件
1. `core/src/regent/agent/agent_runner.py` — subagent depth 默认值 3→1
2. `core/src/regent/application/delivery_role_runtime.py` — 移除 SUPERVISES/DELEGATES_TO
3. `core/src/regent/application/execution_orchestrator.py` — 集成 GoalClassifier
4. `core/src/regent/worker/main.py` — 添加 behavior monitor tick
5. `core/src/regent/api/main.py` — 添加 behavior API 端点
