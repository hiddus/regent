# 交付状态机落地设计（2026-07-31）— 基于真实代码校正版

> 本文是对「PM+Tech 混合路径实现分析」的落地级校正。
> 结论先行：**方向正确，但约 70% 设想能力在仓库中已存在**，首版应做"薄层收口 + 画像预算耦合 + grep 门禁"，而非从零重建，否则会与 `DeliveryGapRecoveryService` 重复。

## 0. 传输与事实核对

- 我侧（助手）Grep / Read 完全正常，本轮已成功命中全部符号并定位文件。
- 你引用的文件全部真实存在，但**行号是近似的**，已在 §4 给出真实行号。
- 重要校正：你原稿把 `DeliveryState` / `DeliveryVerdict` 当作"待新建"，但等价的**状态转移与交人机制已经存在**，只是散布在 8 处 orchestrator handler 中，没有集中成枚举。

## 1. 已存在的能力（不要重建）

| 能力 | 真实位置 | 对应 AC |
|---|---|---|
| 恢复服务 + 结果结构 | `application/delivery_gap_recovery.py:254` `recover()` → `DeliveryGapRecoveryResult(recovered, method, message, attempts, gap_kind, terminal_exhaust, recovery_work_id, organization_id)`（`:145-155`） | — |
| "可恢复→重跑；耗尽→交人"收尾 | `execution_orchestrator.py` 中 8 处：`1568 / 1612 / 1669 / 2121 / 2395 / 2527 / 3174 / 3594`，均为 `if recovery.recovered: ... elif recovery.terminal_exhaust: _halt_goal_stage(..., terminal=GoalCommand.WAIT_FOR_HUMAN, event_type="HUMAN_TASK_REQUIRED")` | AC2/AC3 |
| 最优产出不丢弃（agentic 路径） | `agent/generator.py:178-205` 验证失败时 `_persist_verification_draft(...)` 保留草稿树 + artifact URI，再抛错 | AC4 |
| 需主观判断即交人（短路） | `delivery_gap_recovery.py:306-318` `gap_kind=="goal_intent"` → `goal_attainment_needs_human` → `WAITING_HUMAN` | AC3 |
| "非死端/禁止裸阻塞"门禁 | `ops/console_confirm_gate.py`（CON-5）：禁止产品代码中的裸 `confirm/input` 阻塞模式 | AC1 同源 |
| 画像枚举 + 默认 balanced | `application/confirmation.py:14` `DecisionPreference(AGGRESSIVE/BALANCED/CONSERVATIVE)`；CON 域默认 `balanced`（见 `docs/decision-note-console-dialog-2026-07-31.md:10`） | AC5 锚点 |
| 语义对齐 LLM 调用已默认关闭 | `infrastructure/code_generator.py:150-156, 248-269`；`validate_goal_alignment_semantic` 仅 `REGENT_GOAL_SEMANTIC_ALIGNMENT_ENABLED=true` 时调用（DecisionNote dead-weight-trim ACCEPTED） | Q2 已解决 |

## 2. 真正缺失的三块（首版范围）

> 这是混合路径"还差什么"，而不是"要建什么"。

**A. 集中的 `DeliveryState` 枚举 + `DeliveryVerdict` 数据类（薄层）**
- 现状：8 处 handler 各自重复 `if recovered ... elif terminal_exhaust: _halt(...)`。
- 价值：把转移逻辑收口到一个函数 `_apply_delivery_verdict()`，使 AC1 的 grep 门禁可被静态强制；新状态 `AUTO_RECOVERING` / `DELIVERED_FOR_REVIEW` 才有名可分。

**B. 画像 → AUTO_RECOVERING 预算耦合（AC5）**
- 现状：`AgenticCodeGenerator` 用 `BudgetExhaustedError` 控制预算（`agent/generator.py:132`）；artifact-backed 路径**没有画像驱动的恢复预算**，只靠 `DeliveryGapRecoveryService` 内部 attempt 循环。
- 缺口：`DecisionPreference` 尚未驱动"生成/恢复预算"与"转评审阈值"。这是 genuinely new 的工作。

**C. 交付专用 grep 门禁（AC1 细化）**
- 在 CON-5 基础上，新增一条针对 `execution`/`delivery` 终态的断言："任何 `raise`/`FAILED`/`incomplete` 终态必须伴随 `WAIT_FOR_HUMAN` 或 `HANDED_OFF` 出路"。
- 真实死端靶点：`execution_service.py:544` `raise RuntimeError("execution receipt is incomplete")`；`release_service.py:151` `POLICY_DENIED "release approval task is incomplete"` —— 而非你原稿写的 `execution_orchestrator.py:1992/2013/2551`（那几行实为 build-request handler 与 deploy 拦截的 `WAIT_FOR_HUMAN` 收尾，并非死端）。

## 3. 数据模型（新增，薄）

```python
# core/src/regent/application/delivery_state.py
from dataclasses import dataclass
from typing import Literal
from regent.application.failure_envelope import FailureEnvelopeModel  # 复用 0041 模型

class DeliveryState:
    GENERATING = "GENERATING"
    VERIFYING = "VERIFYING"
    DELIVERED = "DELIVERED"
    AUTO_RECOVERING = "AUTO_RECOVERING"          # 预算内重规划重跑
    DELIVERED_FOR_REVIEW = "DELIVERED_FOR_REVIEW" # 带当前最优产出交评
    ESCALATED = "ESCALATED"                       # 不可恢复 → 升级（仍带出路）

@dataclass
class DeliveryVerdict:
    status: Literal["delivered", "partial", "failed"]
    output: "ArtifactSet"                 # 当前最优产出，永不丢弃（复用 artifact 版本）
    errors: list[FailureEnvelopeModel]   # 真实错误，0041 模型已就绪
    recoverable: bool                     # agent 能否自助修复
    needs_human: bool                     # 是否需主观判断
    rationale: str
    review_prompt: str | None            # 交评时告诉用户看什么

def decide_delivery_verdict(*, success, needs_human, recoverable,
                            budget_left, output, errors) -> DeliveryVerdict:
    if success:
        return DELIVERED
    if needs_human:                       # AC3：主观判断立即交评，不等耗光预算
        return DELIVERED_FOR_REVIEW(output, errors, review_prompt=...)
    if recoverable and budget_left:
        return AUTO_RECOVERING            # 重规划重跑
    if recoverable:                       # 预算耗尽 → 带产出交评
        return DELIVERED_FOR_REVIEW(output, errors)
    return ESCALATED                      # 不可恢复 → 升级（仍带出路）
# 铁律：无死端终态——DELIVERED / DELIVERED_FOR_REVIEW / ESCALATED 全部带出路。
```

## 4. 接入点（真实行号，供落地）

1. **`infrastructure/code_generator.py:248-269`** 语义对齐块：已是 opt-in，**不要动**；Q2 已解决。
2. **`agent/generator.py:125-131`** `runner.run(verify=True, ...)`：已是 AUTO_RECOVERING 的可复用执行引擎；`132-136` 的 `BudgetExhaustedError→ValueError` 改为产出 `DeliveryVerdict(AUTO_RECOVERING/ESCALATED)`，不再抛裸 `ValueError`。
3. **`execution_orchestrator.py` 8 处（1568/1612/1669/2121/2395/2527/3174/3594）**：把 `if recovered ... elif terminal_exhaust: _halt_goal_stage(WAIT_FOR_HUMAN)` 统一收口到 `_apply_delivery_verdict()`，并把 `goal_intent` 短路映射为 `DELIVERED_FOR_REVIEW`。
4. **`application/failure_envelope.py:22-53`** `STAGE_REPAIR_POLICY` 已有 per-stage `max_attempts` + `human_handoff_on_exhaust`：作为 AUTO_RECOVERING 预算的底层来源，画像层在其上再乘系数（aggressive×N / conservative×1）。
5. **画像预算**：`AgenticCodeGenerator._budget` 与 artifact-backed 恢复 attempt 上限，由 `DecisionPreference`（确认域已有，默认 `balanced`）解析，不新造枚举。

## 5. 两个拍板项结论

- **Q1 默认画像：采用 `balanced`（中预算）。** 与 CON 域既有默认一致（`decision-note-console-dialog-2026-07-31.md:10`），且直接复用 `DecisionPreference`，不另起画像体系。aggressive 可作为运维覆盖，但首版默认 balanced（更早交评、更少 token/延迟浪费）。
- **Q2 语义对齐 LLM 调用：保持"可配置开关、默认关闭"，不回加默认调用。** 该决策已由 `decision-note-dead-weight-trim-2026-07-31.md`（ACCEPTED）落地，原稿"建议删掉/可关"中的"可关"路径已实施。默认路径零成本，无需再改。

## 6. 验收标准（修正）

- AC1：任何 `execution`/`delivery` 终态必须伴随 `WAIT_FOR_HUMAN` 或 `HANDED_OFF`/升级出路；新增 grep 门禁（引用 CON-5，不另立平行规则）。
- AC2：可恢复错误 → `AUTO_RECOVERING` 直到画像预算，再 `DELIVERED_FOR_REVIEW` 且**带当前产出**。
- AC3：`goal_intent` 等需主观判断 → 立即 `DELIVERED_FOR_REVIEW`，不等耗光预算（现有 `WAITING_HUMAN` 短路需显式映射进新枚举）。
- AC4：失败/未达标时当前最优产出永不丢弃（agentic 已由 `_persist_verification_draft` 覆盖；**artifact-backed 路径需核对 artifact 版本/草稿保留**，为唯一待补点）。
- AC5：`DecisionPreference` 驱动生成/恢复预算与转评审阈值（aggressive 多跑、conservative 早交评）。

## 7. 架构约束（ADR 处理）

- "非死端铁律"已是 **CON-5**（`ops/console_confirm_gate.py`）。本设计作为 CON-5 在 delivery 域的细化，**不另起平行 ADR**，避免规则重复。
- 如确需独立 ADR，标题建议：`ADR: 交付状态机非死端约束（CON-5 细化）`，正文引用 CON-5 与 `DeliveryGapRecoveryService`。

## 8. 落地点清单（首版）

1. 新增 `core/src/regent/application/delivery_state.py`（枚举 + `DeliveryVerdict` + `decide_delivery_verdict` + `_apply_delivery_verdict` helper）。
2. `execution_orchestrator.py` 8 处收口到 `_apply_delivery_verdict()`。
3. `AgenticCodeGenerator` 预算由 `DecisionPreference` 解析；`BudgetExhaustedError` 改为产出 verdict。
4. 新增 grep 门禁（ops 或 docs），针对 `execution`/`delivery` 终态断言出路。
5. 补 artifact-backed 路径的"草稿/最优产出保留"核对（AC4 收口）。
6. 单测：枚举转移、画像预算、goal_intent 短路、AC1 门禁命中。
