# Regent 代码回归 / 冲突检查报告

- **日期**：2026-08-02
- **检查人**：小佑（WorkBuddy）
- **结论速览**：当前未提交的「fork / run-think-learn」新特性整体自洽、前后端接线一致；但发现 **2 个高优风险 + 1 处规格漂移**：① 新模块 `goal_runtime_plan.py` 从未纳入版本控制，干净检出会 `ImportError`；② `failure_lessons` 数据结构不兼容，delivery-gap 的失败经验被新读取逻辑静默丢弃，「从失败学习」闭环退化；③ 该新特性未在 PRD/技术规格中描述。

---

## 1. 检查范围与方法

- **代码库**：Regent，共 49 个提交；分支 `main`（领先 `origin/main` 4 个提交）/`aar1-m1-expand`。
- **工作区状态**：21 个已跟踪文件改动（**692 行新增 / 108 行删除**）；26 个未跟踪文件（含新模块、测试、ops 脚本）。
- **方法**：
  1. `git` 历史 churn 分析（定位反复改动热点文件）
  2. 未提交 diff 逐文件审阅（后端 `application/` + 前端 `regent-console`）
  3. 跨模块依赖追踪（`import` / 字段 setter / 数据模型）
  4. AST 静态扫描（语法错误 + 文件内重复定义）
  5. 规格（spec）vs 实现关键字比对

---

## 2. 迭代脉络与 churn 热点

| 文件 | 全史改动次数 |
|---|---|
| `core/src/regent/application/execution_orchestrator.py` | **22**（最高） |
| `core/src/regent/config.py` | 16 |
| `Regent-Technical-Spec.md` | 13 |
| `Regent-PRD.md` | 11 |
| `core/src/regent/application/app_guidance_service.py` | 11 |
| `core/src/regent/api/main.py` | 10 |
| `core/src/regent/worker/main.py`、`infrastructure/models.py`、`infrastructure/code_generator.py`、`agent/agent_runner.py` | 9 |

**信号**：历史提交中大量 `Fix` / `Restore`（如 `Restore agent core through M5`、`Fix console observability...`）。同一文件被反复修改，是「新逻辑冲掉旧逻辑」的高发区。`execution_orchestrator.py` 既是 churn 最高，又是本次未提交改动重头（+77），优先级最高。

---

## 3. 核心发现

### 🔴 [高] F-1　未跟踪新模块 `goal_runtime_plan.py` 被已修改代码强依赖

- **现象**：工作树中 `execution_orchestrator.py`、`app_project_service.py` 现已 `from regent.application.goal_runtime_plan import ...`，但 `goal_runtime_plan.py` 的 `git status` 为 `??`（**从未提交**）。
- **证据**：
  - `git ls-files core/src/regent/application/goal_runtime_plan.py` → 无输出（从未入库）
  - `git show HEAD:.../execution_orchestrator.py | grep goal_runtime_plan` → 无命中（仅工作树引用）
- **影响**：若只提交已跟踪改动（用 `git add <具体文件>`，或 CI 从干净 checkout 构建），→ `ImportError`，服务起不来。
- **同类问题**：`tests/unit/application/test_fork_option_match.py`、`test_goal_runtime_plan.py` 同样未跟踪（新特性的测试也没入库）。
- **修复**：提交本次改动时务必
  ```bash
  git add core/src/regent/application/goal_runtime_plan.py \
          tests/unit/application/test_fork_option_match.py \
          tests/unit/application/test_goal_runtime_plan.py
  ```
  建议在 CI 增加「干净 checkout 后 `python -c "import regent.application.execution_orchestrator"`」冒烟检查。

### 🔴 [高] F-2　`failure_lessons` 数据结构不兼容 → delivery-gap 失败经验被静默丢弃

- **旧写入方（仍在用）**：`delivery_gap_recovery.build_failure_lesson()` 产出字典，字段为 `at / attempt / gap_kind / escalation_method / gap_reasons / learned_constraints / halt_stage / halt_message / last_error / goal_text / replan_required / lesson_digest`。**无 `summary` / `avoid`**。
- **旧读取方（HEAD）**：`execution_orchestrator` 直接 `failure_lessons = list(goal_meta.get("failure_lessons") or [])` → 全量进入 `acceptance_contract`。
- **新读取方（工作树）**：`goal_runtime_plan.lessons_for_acceptance()` 只保留 `item.get("summary") or item.get("avoid")` 为真的条目。
- **结论**：delivery-gap 写入的 lesson **没有 `summary`/`avoid`** → 被新读取方**全部过滤掉** → 不再进入生成验收契约的 `failure_lessons` → 「从交付缺口失败中学习」的闭环退化（生成器看不到这些约束）。
- **注**：新代码在 `execution_orchestrator` 错误分支改用 `append_failure_lesson()`（带 `summary`/`avoid`），所以「生成失败」类 lessons 仍能进契约；但「交付缺口」类被丢弃。
- **影响路径**：`delivery_gap_recovery` → `metadata.failure_lessons` → 下次 generation 的 `acceptance_contract["failure_lessons"]` → generator 提示词约束。
- **修复（二选一）**：
  - **方案一（推荐）**：`lessons_for_acceptance` 兼容两种 schema——除 `summary`/`avoid` 外，也接受含 `gap_reasons`/`learned_constraints` 的旧 lesson，注入生成提示词时归一化字段。
  - **方案二**：把 `delivery_gap_recovery` 改为调用 `append_failure_lesson()` 写入结构化 lesson（补 `summary`/`avoid`），统一单一写入方。
  - 同时：对 DB 中已存的旧 shape lessons 做一次性兼容读取/迁移。

### 🟡 [中] F-3　fork 自动启动门控依赖前端正确渲染（已核实自洽，仅部分提交才断裂）

- **行为变更**：`create_app_draft` 由「无条件自动启动」改为「`needs_user_fork` 时等待用户选择再启动」。
- **已核实**：前后端接线一致——后端写 `pending_fork_options` / `needs_user_fork`；前端 `ConfirmationCard` 从 `plan.fork_options || pending_fork_options` 渲染选项按钮，点击 → `api.guidance("option:<id> <label>")` → `SELECT_OPTION` → `_handle_select_option` 解析 fork → 必要时 `GoalExecutionService.start()`。`MessageList` 用 `FORK_SELECTED` 消息把目标移出 `movingGoals` 隐藏按钮。
- **风险**：仅在「部分提交」（后端改了但前端/新模块没一起）时才会断裂，导致 DRAFT 目标卡死。当前工作树完整，风险已缓解。
- **建议**：加一条集成测试覆盖 `needs_user_fork=true → 用户选择 → 目标自动启动` 全链路。

### 🟡 [中] F-4　`execution_orchestrator.py` 高频 churn + 反复 Fix（稳定性信号）

- 全史 22 次改动；近 15 次提交里 12 次；多次 `Fix`/`Restore`。
- 单文件职责过重（>4400 行），易引入「改 A 坏 B」。本次未提交又改了 `failure_lessons` 取法、两处 `needs_user_fork` 守卫、错误分支 lesson 写入。
- **建议**：对该文件做一次职责拆分/架构复盘（生成验收契约、失败学习、fork 守卫可下沉到子模块）。

### 🟢 [低] F-5　消息类型 `GOAL_UNDERSTANDING_READY` → `GOAL_PLAN_PROPOSED` 改名

- 创建流程改用 `GOAL_PLAN_PROPOSED`；旧类型仍被 guidance `MODIFY` 流程（`app_guidance_service.py:2130`）、`live_action` 标签、前端 `MessageList`/`progressNodes` 使用 → **无断裂**，属正常扩展。前端已同时登记两种类型，一致。

### 🟢 [低] F-6　大量未跟踪 `ops/probe_*.py` 一次性脚本

- 26 个未跟踪文件中多数如 `ops/probe_goal_*.py`、`ops/apply_*.py`、`ops/clear_invalid_state_storm.py` 等——疑似调试/救火脚本。
- **建议**：确认无用的清理或归档到 `ops/` 下明确命名，避免误提交污染主分支。

---

## 4. 静态扫描结果

- **语法错误**：`core/src` 210 个 `.py` 全部通过 AST 解析，**0 语法错误**。
- **文件内重复定义（被覆盖的旧逻辑）**：**0 处**。
- **结论**：本次「冲毁旧逻辑」不发生在语法/重复定义层，而是跨模块依赖与数据模型层（见 F-1 / F-2）。

---

## 5. 规格与实现一致性

- 工作树同步更新了 `Regent-PRD.md`（+25）、`Regent-Technical-Spec.md`（+51）、`Regent-Plan.md`，但内容是关于 **M6 canary、交付缺口软暂停、prompt-cache、GQ-4** 等，**未描述本次最大的新特性 fork / `needs_user_fork` / run-think-learn / `SELECT_OPTION`**（关键字 grep `fork|needs_user_fork|run-think-learn|SELECT_OPTION` 在三个文档中 **0 命中**）。
- 即：代码已引入「目标在推演不清时暂停、请用户从有限选项拍板、再自动启动」的显著用户行为，但 PRD / TechSpec 尚未记载。
- **建议**：补一段规格（用户分叉决策 / run-think-learn L1–L3 失败经验），或确认该特性属「先代码后文档」并排期补文档。

---

## 6. 修复优先级清单

1. **[阻断]** 提交时一并 `git add` `goal_runtime_plan.py` + 两个测试文件；加干净 checkout 的 import 冒烟 CI。（F-1）
2. **[高]** 统一 `failure_lessons` schema，让 `lessons_for_acceptance` 兼容旧 lesson，并迁移存量数据。（F-2）
3. **[中]** 为 fork 全链路补集成测试。（F-3）
4. **[中]** 对 `execution_orchestrator` 做架构复盘 / 拆分。（F-4）
5. **[低]** 规格补 fork / run-think-learn 章节，或确认排期。（F-5 / 规格）
6. **[低]** 清理 `ops/probe_*.py` 一次性脚本。（F-6）

---

## 7. 需你确认

- 是否要把 26 个未跟踪文件（尤其 `ops/*.py`）一并提交或删除？
- DB 中是否已有旧 shape 的 `failure_lessons` 存量数据，需不需要迁移脚本？
- fork 特性是否属「先代码后文档」，还是应暂缓合入直到规格补齐？
