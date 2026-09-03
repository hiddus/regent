# 控制台对话框改造 PRD（2026-07-31）

**状态**：ACCEPTED（首版可验收切片）  
**视角**：产品经理 + 技术专家  
**DecisionNote**：[decision-note-console-dialog-2026-07-31.md](./decision-note-console-dialog-2026-07-31.md)  
**计划**：[console-dialog-plan-2026-07-31.md](./console-dialog-plan-2026-07-31.md)

## 1. 背景与问题

Regent Console（`apps/regent-console`，React Web）与 Core API 的确认路径当前存在两类问题：

1. **死等（Web 语义）**：不是 CLI `confirm()`/`input()`（仓库内无此类调用），而是：
   - `HumanTask` 超时后仅标 `TIMED_OUT`、**不应用默认决策**，Goal 可长期停在 `WAITING_HUMAN`
   - 聊天批准 / 前端 TaskCard 无倒计时，用户不知超时默认
   - 进度节点卡在 `waiting`（如 `DELIVERY_GAP_EXHAUSTED`、`HUMAN_TASK_REQUIRED`）
2. **输出无用**：对话框/任务卡把 gap reasons、`DomainError` 原文、验证原材料直接甩给用户，缺少「规则 / 风险 / 双向后果 / 超时默认」。

### 1.1 核实：勿重复过时诊断

| 项 | 现状（2026-07-31） |
|---|---|
| `validate_goal_alignment_semantic` | **默认已关**（死重修剪 `b485eaf` / DecisionNote dead-weight）。仅 `REGENT_GOAL_SEMANTIC_ALIGNMENT_ENABLED=true` 时调用。**不是** fail-closed 真实验证。 |
| 控制台形态 | **Web**（React + Core API + SSE），非 CLI rich Panel。 |
| 安全门 | `assert_not_replacing_kernel` / `verify_template_certification` / `assert_generator_consistency` / `gq4_default_switch_gate` 仍 fail-closed，画像不得削弱。 |

## 2. 目标

| ID | 目标 |
|---|---|
| G1 | 消除无默认的死等：可确认交互须有超时 + `default_on_timeout`（安全项 `timeout=0`） |
| G2 | 对话框从「结果展示」升级为「规则与逻辑展示」 |
| G3 | 决策偏好画像 `aggressive` / `balanced`（默认） / `conservative` |
| G4 | 按动作类型 allow/deny（首版最小动作集） |
| G5 | 用户偏好不削弱 fail-closed 安全不变量 |

## 3. 决策偏好矩阵

| 画像 | 常规默认（按风险） | 超时默认 | 适用 |
|---|---|---|---|
| aggressive | 低/中 → allow；高 → 询问 | allow | 追求速度 |
| balanced（默认） | 低 → allow；中 → 询问；高 → deny | deny | 多数 |
| conservative | 仅 allow-list 自动；其余询问 | deny | 生产/敏感 |

**优先级**：安全不变量 > deny 硬规则 > 画像默认 > allow 规则 > 询问。

## 4. ConfirmationRequest 合同

```
action, summary, rules_applied[], risk_level(low|medium|high),
rationale, on_allow, on_deny,
timeout_seconds (0=不超时，仅安全项), default_on_timeout(allow|deny|cancel),
options[allow, allow_always, deny, deny_always],
safety_invariant: bool=False,
detail?: 折叠的 DomainError / gap 原文
```

## 5. 安全不变量（恒最高，任何画像不自动放行）

| 门 | 行为 |
|---|---|
| `assert_not_replacing_kernel` | 永远 DENY |
| `verify_template_certification` 失败 | DENY |
| `assert_generator_consistency` 不一致 | DENY |
| `gq4_default_switch_gate` 未过 | 晋级被拒 |

拒绝路径包装为 `ConfirmationRequest(safety_invariant=True)`；**禁止**把裸 `DomainError` / gap 原文作为对话框主体（折叠进「详情」）。

## 6. 对话框内容规范（Web Console）

```
[风险徽章] 需要确认：<summary>
动作：<action> | 触发规则：<rules_applied>
为什么：<rationale>
允许后：<on_allow> | 拒绝后：<on_deny>
倒计时：<n>s 后默认 <default_on_timeout>（timeout=0 则无倒计时）
[允许][拒绝]（首版；allow_always/deny_always 可随后）
<details>原始错误 / gap</details>
```

接入点（已 grep）：

- Frontend：`ConfirmationCard`、`TaskCard`、`MessageList`、`progressNodes`
- Core：`HumanTaskService`、`execution_orchestrator`（`HUMAN_TASK_REQUIRED` / `RELEASE_APPROVAL`）、`app_guidance_service`、安全门模块

## 7. 验收

| AC | 标准 |
|---|---|
| AC1 | 无裸无超时确认原语；HumanTask 超时应用默认决策 |
| AC2 | 三画像 × 三风险路由符合矩阵 |
| AC3 | `safety_invariant` 任何画像 DENY，timeout=0 |
| AC4 | 对话框含规则/风险/双向后果/倒计时；报错在详情 |
| AC5 | deny/allow 规则可覆盖画像 |
| AC6 | 超时触发默认，不永久挂起可恢复闸门 |

## 8. 非目标（本切片）

- 不引入 LangGraph；不扩 Hive；不擅自改生产 `REGENT_GENERATION_STRATEGY`
- 不削弱 kernel / cert / metadata / gq4 gate
- 不做 DB 迁移；画像与规则配置驱动，可回滚到 `balanced`
