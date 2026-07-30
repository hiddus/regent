# Regent 交付质量重构方案 —— 借鉴 Claude Code 的上下文与记忆机制

> 日期：2026-07-27
> 依据：/opt/regent 服务器实测（current → releases/20260725-v3-goalanchor-r5）+ D:\workspace\claude-code-cli-master 源码深度分析

---

## 一、诊断结论：为什么输出的 app 不可交付

### 1.1 实测证据

以最新 ACHIEVED 目标「一个针对 AI 行业从业者的 APP，让他们能发现新技能、新动向、结交新伙伴」为例：

| 检查项 | 实际交付物 | 结论 |
|---|---|---|
| 前端 | 单个静态 index.html，硬编码假数据（假新闻卡片、假用户资料） | 是效果图，不是产品 |
| 后端 | `src/app.py` 共 **8 行**：Flask `send_from_directory` 托管静态页 | 正是系统提示中明文禁止的 "Pure static file serving without business logic"，但照样通过评审并标记 ACHIEVED |
| 数据 | 无数据库、无用户系统、无任何持久化 | "结交新伙伴"完全无法发生 |
| 交付形态 | preview URL（http://118.31.171.159:8000/preview/...） | 演示页，不是可运营的产品 |

**状态机说 ACHIEVED，但按用户目标衡量，交付的是一张会动的海报。**

### 1.2 根因链（按因果顺序）

**根因 ①：单轮结构化生成 —— 复杂度天花板被锁死（最核心）**

`infrastructure/code_generator.py` 的全部生成逻辑是：

```
plan(JSON) → 一次 generate_structured() 调用 → GeneratedSourceBundle(所有文件)
```

- 整个 app 的所有文件必须在**一次 LLM 输出**里以 JSON 返回；
- 无 agent 循环、无工具（不能读文件/跑命令/查报错）、无迭代修正；
- 单次输出 token 上限 ≈ 决定了产物上限：**一个 HTML + 一个 8 行的 app.py 就是这种架构能产出的极限**。
- 对比：Claude Code 交付一个 app 要经历几十~上百轮"读→写→跑→改"的工具循环。**这不是提示词问题，是架构问题**——后续所有 GoalAnchor、语义对齐补丁都在给单轮生成打补丁，无法突破天花板。

**根因 ②：上下文组装几乎为零**

生成时 LLM 能看到的全部上下文 = `json.dumps(plan)` + goal_anchor_text。没有：
- 项目已有代码/文件内容（REPLACE 操作却要求 expected_previous_hash，LLM 根本看不到旧文件）；
- 上一轮失败的具体产物（retry 只传 gap_reasons 文字，不传失败的代码本身）；
- 任何跨 Run / 跨 Goal 的经验沉淀。

**根因 ③：记忆系统是孤岛**

`application/memory_service.py`（309 行，Working/Episodic/Semantic 三层设计）**只被 `api/memories.py` 的 CRUD 端点引用**，执行链路（orchestrator、generator、delivery review）零调用。记忆设计了但从未参与生成 —— 每次生成都是失忆的。

**根因 ④：质量 gate 检查错了对象**

`delivery_review_service.py` 检查的是 **HTML 表面特征**（有 `<main>`、有样式、有条目数、语义对齐评分），从不检查：
- app 是否真的能跑起来（没有启动+冒烟测试）；
- 后端是否有真实业务逻辑（8 行静态托管通过了评审）；
- 用户旅程是否可完成（"结交伙伴"点了按钮无任何后端响应）。

**根因 ⑤：验收标准在流程中被逐步稀释**

- milestone 切片时 `acceptance_contract.pop("success_criteria")` —— 非最终里程碑直接丢弃全局成功标准；
- 系统提示允许 "realistic placeholder content"，为假数据开了正门；
- ACHIEVED 的实际语义已降格为"preview 页通过 HTML 审查"。

**根因 ⑥：工程卫生崩坏（症状同时也是加速器）**

release 目录混入 90+ 个 `check_*.py / fix_egress1-7.py / q1-q10.py` 一次性调试脚本，与产品代码同层。修复靠临时脚本而非测试与迁移，质量标准在日常操作中持续被稀释。

---

## 二、Claude Code 对照：差距在哪

| 维度 | Claude Code（实测源码） | Regent 现状 | 差距等级 |
|---|---|---|---|
| 生成方式 | 多轮 agent 循环 + 15 种工具（Read/Write/Bash/Grep…），边跑边改 | 单轮 JSON 结构化输出 | ★★★ 致命 |
| 上下文组装 | 分段系统提示（memoize 缓存）+ CLAUDE.md 四层发现 + git 状态 + 目录快照 | plan JSON + goal 文本 | ★★★ |
| 上下文压缩 | autoCompact（阈值=窗口−13k，熔断 3 次）+ microCompact（清旧工具结果）+ 压缩后重注最近 5 文件 | 无（单轮也用不上；改多轮后必须有） | ★★★ |
| 持久记忆 | user/project/local 三层 + MEMORY.md 索引（200 行/25KB 上限）+ 后台自动抽取 | 三层模型有 schema 但未接入执行链 | ★★ |
| 目标防遗忘 | todos 存 AppState，10 轮未更新自动注入 todo_reminder 附件 | GoalAnchor 只在开头注入一次 | ★★ |
| 质量 gate | 独立对抗式验证子 agent（禁止改代码、强制跑 build/test、识别"读代码=已验证"的自欺） | 规则式 HTML 检查 + HTML 语义评分 | ★★★ |
| 子任务隔离 | createSubagentContext 独立上下文 + sidechain 旁路 transcript，结果回传不污染主链 | 无子 agent 概念 | ★★ |
| 注入有界性 | 所有注入都有预算（MEMORY 25KB、skill 5k token、post-compact 50k） | 无预算概念 | ★ |

关键文件参考（供实施时对照阅读）：
- 上下文组装：`constants/prompts.ts` `getSystemPrompt()`、`utils/claudemd.ts` `getMemoryFiles()`（四层+向上遍历+@include）
- 压缩：`services/compact/autoCompact.ts`（AUTOCOMPACT_BUFFER_TOKENS=13000、熔断器）、`compact.ts` `buildPostCompactMessages()`、`microCompact.ts`
- 记忆：`memdir/memdir.ts`（MEMORY.md 索引 + 主题文件）、`tools/AgentTool/agentMemory.ts`（user/project/local 三 scope）
- 目标提醒：`utils/attachments.ts` `getTodoReminderAttachments()`（TODO_REMINDER_CONFIG，10 轮阈值）
- 验证：`tools/AgentTool/built-in/verificationAgent.ts`（对抗式验证专家）
- 子代理：`tools/AgentTool/runAgent.ts` `createSubagentContext()`

---

## 三、重构方案

### 总原则

> **把"生成一个 app"从一次 LLM 调用，升级为一次有工作记忆、有预算、有验证 gate 的 agent 会话。**
> 状态机（Goal/Work/Run/Permit）保留 —— 它是治理层，没问题；要换的是 Run 内部的执行引擎。

### P0（决定成败，先做）

**P0-1 Agentic 生成引擎：AgentRunner 替换单轮生成器**

新增 `regent/agent/` 模块，Run 的执行体从 `ArtifactBackedCodeGenerator.generate()` 换成多轮循环：

```
AgentRunner(goal_context, workspace, budget):
    loop (直到 verification 通过 / 预算耗尽):
        messages = assemble_context()        # 见 P0-2
        resp = provider.chat(messages, tools=[read_file, write_file,
                                              run_command, search, todo_write])
        执行工具调用 → 结果追加进 messages
        if 接近上下文预算: compact()          # 见 P1-1
```

要点：
- 工具在**沙箱工作区**内执行（builds/sandbox 已有隔离基础，复用）；
- `run_command` 允许 pip install / 启动 app / 跑测试 / curl 冒烟 —— 这是产物能"真的跑起来"的前提；
- 每轮工具结果落 `agent_transcript` 表（对应 CC 的 sidechain transcript），Run 可恢复、可审计；
- 预算三维：max_turns（如 60）、max_tokens、max_wall_time，耗尽 → Run FAILED 带 EXHAUSTED_BUDGET 证据，**绝不降格交付**。
- provider 需新增 `chat()`（多轮+tool calls），现有 `generate_structured()` 保留给小任务（假设生成、评审打分）。

**P0-2 分层上下文组装器（对标 getSystemPrompt 分段制）**

新增 `regent/agent/context_assembler.py`，每轮按固定顺序拼装，每段有硬预算：

| 段 | 内容 | 预算 |
|---|---|---|
| 1. goal_anchor | 原始目标 + success_criteria + 当前 milestone | 2k tok |
| 2. project_memory | 该 AppProject 的 REGENT.md（见 P0-4） | 6k tok |
| 3. workspace_state | 工作区文件树 + 最近改动文件内容 | 8k tok |
| 4. todo_state | 当前任务清单及状态 | 1k tok |
| 5. recent_failures | 上次评审 gap + **失败产物的关键片段**（不是只有文字描述） | 4k tok |
| 6. conversation | 本 Run 的多轮历史（可压缩区） | 剩余 |

goal_anchor 与 todo_state **每 10 轮强制重注一次**（对标 todo_reminder 机制），防长任务目标漂移。

**P0-3 对抗式验证 Gate：VerificationAgent 替换 HTML 规则审查**

新增独立验证 agent（独立上下文，看不到生成 agent 的自述，只看产物）：
1. 在沙箱**真实启动** app（安装依赖 → 起服务 → 冒烟请求核心路由）；
2. 按 success_criteria 逐条走用户旅程（HTTP 断言，可扩展 Playwright——服务器已有 acceptance_playwright.py 底子）；
3. 静态审查：禁止模式扫描（SimpleHTTPRequestHandler、纯静态托管、lorem ipsum）**对所有代码文件生效，不只 HTML**；
4. 输出结构化 verdict：PASS / FAIL(gap list)。FAIL → gap 连同失败产物片段回注生成 agent（P0-2 第 5 段）重试。
- 系统提示直接移植 verificationAgent.ts 的对抗立场："你的价值在于找到最后 20%"、禁止修改产物、"读代码≠已验证"。
- **删除现有 delivery_review 的宽松路径**：placeholder 视为 FAIL（除非 Goal 明确是 demo）；milestone 切片不再 pop success_criteria，只标记"本轮验收子集"，全局标准始终可见。

**P0-4 ACHIEVED 语义收紧（治理层一行定义改动，价值巨大）**

`GOAL_ACHIEVED` 事件必须携带 VerificationAgent 的 PASS verdict 证据，否则转 WAITING_HUMAN 请人裁决。杜绝"HTML 审查通过 = 目标达成"。

### P1（让长任务稳定、让系统越跑越聪明）

**P1-1 双轨上下文压缩（对标 autoCompact + microCompact）**
- microCompact：超过 N 轮的工具结果替换为 `[cleared]`（保留最近 8 个完整）；
- autoCompact：token > 窗口−15k 时摘要压缩，压缩后重注：goal_anchor、todo、最近 5 个写过的文件（对标 POST_COMPACT_MAX_FILES_TO_RESTORE=5）；
- 连续压缩失败 3 次熔断 → Run FAILED，不空转烧钱。

**P1-2 记忆接入执行链（激活现有 memory_service）**
- **Run 内 Working**：todo + transcript（P0 已含）；
- **AppProject 级**：每个 AppProject 维护一份 `REGENT.md`（对标 CLAUDE.md）：技术栈、结构、已知约束、历史 gap 教训。Run 结束后由廉价 LLM 调用做增量蒸馏（对标 extractMemories），上限 25KB/200 行，超限截断；
- **全局 Semantic**：跨目标沉淀"哪类目标用哪种方案曾通过验证"，供 planning_service 检索。写入走现有 admit/verify 流程，天然带治理。

**P1-3 子任务上下文隔离**
LARGE 目标里程碑级并行时，为每个子任务 spawn 独立上下文 agent（只带 goal_anchor + 自己的 milestone + REGENT.md），结果以结构化摘要回传主 Run，不共享对话历史（对标 createSubagentContext）。

### P2（工程卫生与制度化）

- **P2-1 仓库清理**：`check_*/fix_*/q*.py` 等 90+ 调试脚本移出 release（有价值的进 `ops/`+文档化，其余删除）；建立 release 目录白名单 CI 检查——临时脚本文化就是质量稀释的日常载体；
- **P2-2 回归评测集（graduation harness 升级）**：固化 10 个代表性 Goal 作为回归集，每次 release 全量跑，报告"验证通过率/预算消耗/人工介入次数"三指标，防止再次无感回退；
- **P2-3 Alembic 迁移链重建**（此前 stamp 0029 的欠账），修复动作一律走 migration + 测试，禁止临时脚本改库。

### 实施顺序与依赖

```
P0-1 AgentRunner ──┬─→ P0-3 VerificationAgent ─→ P0-4 ACHIEVED 收紧
P0-2 ContextAssembler ┘         │
        ↓                       ↓
P1-1 压缩    P1-2 记忆接入    P2-2 回归评测集
        ↓
P1-3 子任务隔离              P2-1/P2-3 随时并行
```

先在**一条新 capability 通道**（如 `agentic-generation-v1`）上并行实现 P0，用同一批目标与旧通道 A/B 对比（正好复用已冻结的对照实验设计），验证通过后切默认。

---

## 四、一句话总结

Regent 的治理层（状态机、Permit、事件溯源）是完整的，但**执行层还停留在"一次 LLM 调用生成整个 app"**，上下文近乎为零、记忆未接线、验证只看 HTML 表面 —— 所以 ACHIEVED 的目标交付的是海报而非产品。借鉴 Claude Code，核心是三件套：**agent 循环 + 有预算的分层上下文（含目标重注）+ 对抗式真实运行验证 gate**，其余（压缩、记忆蒸馏、子任务隔离）在此之上依次落位。
