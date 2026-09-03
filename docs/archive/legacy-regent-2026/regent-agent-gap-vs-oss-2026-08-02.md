# Regent Agent 内核 vs 主流开源 Agent 项目 —— 遗漏与重大缺口诊断

> 视角：**Agent 内核**（agent loop / 上下文 / 缓存 / 工具 / 记忆 / 子 Agent / 安全 / 可观测）。
> 对标对象：OpenCode（Go 终端编码 Agent）、OpenHands（用户称 Openwork，事件流平台）、crewAI（多 Agent 编排）、Hermes（Nous Research 持久自主 Agent）。
> 目的：**找出 Regent 当前实现"压根没意识到缺了"的能力或大问题**——正如此前发现的"缓存命中不可观测"那类沉默缺口。本报告只做诊断，不展开实施计划（吸收 CC 暂不作为方向）。
>
> 事实核验：`grep` 已确认 Regent 无 per-tool 权限层（仅 `network_allowlist` 前缀白名单）、无 MCP、无 LLM 流式（仅 verification 的 socket）、有 gap 指纹级跑飞停止（`agent_runner.py:572-580` `IDENTICAL_GAP_STOP_AFTER`）、有 workspace 沙箱（`build_agent_sandbox`）、有验收后不可变快照（`accepted_workspace.py`）。

---

## 1. 五个项目各自的 Agent 内核要点

- **OpenCode**：6 个内置 Agent + **Permission Ruleset**（plan agent 直接从工具列表里移除写工具，LLM 看不到就无法调用）；Worker 线程跑 I/O、TUI 经 `GlobalBus` 收事件（UI 永不卡）；`SessionPrompt.loop()` 流式（text/reasoning/tool delta）；**doom-loop 检测**（跟踪工具调用历史）；compaction 隐藏 agent + 8 段式压缩 prompt；task 工具**递归委派**子 Agent，每层独立权限；MCP 一等公民；**9 层 edit 模糊回退匹配**。
- **OpenHands**：**事件流架构**——状态 = 事件日志的 fold，Action/Observation 不可变、可重放；Agent = `state → action` 纯函数；每会话 Docker 沙箱；`LLMSecurityAnalyzer` 给每个工具调用打 LOW/MED/HIGH/UNKNOWN 并可在危险时暂停确认；RouterLLM 中途换模型；**内置 15 基准评测 + 成本追踪**；pause/resume/fork/确定性重放白送。
- **crewAI**：Agents（role/goal/backstory）+ Tasks（结构化 Pydantic 输出 + guardrails + human-input）+ Crews（Process: sequential/hierarchical/consensual）+ **Flows**（@start/@listen/@router 事件驱动状态机）；**4 层内存**（短/长/实体/上下文，可插拔向量库）+ Knowledge RAG；**Checkpointing** 可重放/分叉；MCP + A2A 一等公民；**Planning agent 带自动恢复**。
- **Hermes**：**复利式持久内存**（~/.hermes/ markdown，跨会话/跨模型存活）；**自动写 SKILL.md**（解决问题即沉淀技能，兼容 agentskills.io）；5 种执行后端 + 容器加固；**改前工作区快照 + 一键回滚**；**7 层防御**（allowlist/危险命令审批/Docker 隔离/MCP 凭证过滤/注入扫描/跨会话隔离/输入清洗）；并行子 Agent（RPC，零上下文成本）；自改进（批量轨迹导出做 RL）。

---

## 2. 能力矩阵（✓ 有 / △ 部分 / ✗ 无）

| 能力 | Regent | OpenCode | OpenHands | crewAI | Hermes |
|---|---|---|---|---|---|
| Agent 主循环 + 预算/提交契约 | ✓ 强 | ✓ | ✓ step 函数 | ✓ Flows/Crews | ✓ |
| 流式 / 实时事件协议 | ✗ 仅 1 种事件 | ✓ | ✓ 事件流 | ✓ | ✓ |
| **工具权限/安全层** | ✗ 仅网络前缀白名单 | ✓ Ruleset | ✓ 安全打分器 | △ guardrails | ✓ 7 层 |
| 执行沙箱隔离 | △ 子进程级 | △ 主机 | ✓ Docker | ✓ E2B | ✓ 5 后端 |
| **改动前回滚/检查点** | △ 仅验收后快照 | ✗ | ✓ 事件溯源 | ✓ checkpoint | ✓ 改前快照 |
| 跑飞/重复检测 | △ gap 指纹级 | ✓ 工具级 | △ | △ | △ |
| **Prompt 缓存可观测 + 布局** | ✗ 黑盒 | ✓ | △ | ✗ | ✓ |
| 上下文压缩策略 | △ 分层+微压 | ✓ 3 档 | △ | ✗ | △ |
| **子 Agent 模型主动 spawn** | ✗ 仅 milestone 串行 | ✓ 递归委派 | ✓ Delegate | ✓ delegation | ✓ 并行 |
| 内存架构（分层/召回） | △ REGENT.md 蒸馏 | △ | △ | ✓ 4 层+RAG | ✓ 复利 |
| Skills 渐进披露/自创 | △ 全量注入 | ✓ 按需加载 | ✓ | ✓ | ✓ 自创 |
| MCP / A2A 生态 | ✗ | ✓ | ✓ | ✓ | ✓ 双向 |
| 人在环/中断 | ✗ | ✓ 审批门 | ✓ | ✓ | ✓ |
| 自验证/评测底座 | ✓ VerificationAgent | △ | ✓ 15 基准 | △ | ✓ RL 轨迹 |
| 重放/分叉/确定性 | △ 账本非事件流 | △ | ✓ | ✓ | △ |

---

## 3. 「沉默缺口」——和缓存命中同类的、不炸但会咬人的遗漏

> 这类缺口的特征：**功能看起来"能跑"，所以没人觉得缺；直到上生产才暴露成本、风险或不可观测**。

### 3.1 Prompt 缓存是黑盒（你举的例子，确认仍在）
Regent 能解析 `cached_tokens`，但：
- 没有 **cache_control 主动布局**（system/static 段没断点规划）；
- 没有 **缓存断点检测**（任何 system prompt / 工具集变化都会悄悄让缓存失效，且无人知晓）；
- 命中率没有进 `ops/probe`，成本护栏测不了。
→ 结果：长会话成本可能悄悄翻倍而无人告警。**这是"沉默缺口"的典型。**

### 3.2 没有 per-tool 权限 / 安全层（比缓存更危险）
Regent 的 `WorkspaceToolkit` 只有 `network_allowlist` 前缀白名单（`_NETWORK_PREFIXES=("pip ","curl ")`）。对比：OpenCode 的 plan agent **直接从工具列表移除写工具**（LLM 看不到就调不了）；OpenHands 的 `LLMSecurityAnalyzer` 给每个工具调用打 LOW/MED/HIGH/UNKNOWN 并可在危险时暂停；Hermes 有 7 层防御 + 危险命令审批。
Regent 是**会写代码、会跑命令的自主 Agent**，却没有任何"这个动作危险吗 / 要确认吗"的机制。**对它这种产品生成场景，这是头号沉默风险。**

### 3.3 没有改动前回滚检查点
Regent 有 `accepted_workspace.py`——但那是**验收成功后**写不可变快照（用于审计），不是工作**进行中**的回滚点。Hermes 是"改前先拍快照、一键回滚"。自主 Agent 跑歪了，Regent 目前只能靠验收失败触发 repair，没有"回到 3 步前"的能力。

### 3.4 跑飞检测只在 gap 层，不在工具执行层
Regent 有 `gap_repeat` + `IDENTICAL_GAP_STOP_AFTER`（验证 gap 指纹级停止，这是独门强项）。但 **OpenCode 的 doom-loop 检测是工具调用历史级**——同一工具同参反复调用会被拦。Regent 对"模型在一个 turn 内反复调同一工具空转"没有防护，仍可在预算内悄悄烧 token。

### 3.5 没有流式 → 控制台盲区（"能跑所以没人觉得缺"）
仅发 `tool_call` 一种事件，导致此前审计的"什么都看不到"。这不是功能坏了，是**可观测性债务**——不修就永远黑盒。

---

## 4. 其他显著遗漏（能力级，非沉默类）

- **子 Agent 不能由模型主动 spawn**：只能按 milestone 串行调度（`subagent.py` 注明模型不能主动 spawn）。OpenCode/OpenHands/Hermes 都支持模型按需递归委派。这限制了复杂任务的并行与隔离。
- **无 MCP / A2A**：所有工具靠内置 10 个 + 白名单，扩展性受限于改内核。crewAI/Hermes/OpenCode 都把 MCP 当一等公民。
- **内存是单层的**：只有 `REGENT.md` 蒸馏 + run_ledger，没有短/长/实体/上下文分层召回，也没有跨会话学习、没有自动沉淀技能。Hermes 的"越跑越懂你"、crewAI 的 4 层内存是主流预期。
- **Skills 全量注入、无渐进披露、无自创**：manifest 一次全塞；不像 Hermes 解决问题即写 SKILL.md、OpenCode 按需加载。
- **编辑工具无模糊回退**：Regent `edit_file` 对 .py 先 compile 校验，但没有 OpenCode 那种 9 层容错匹配，LLM 输出的微小偏差（空格/缩进/转义）更易 match 失败。
- **无 checkpoint / 确定性重放 / fork**：账本是累计数字，不是可重放事件流（OpenHands 靠事件溯源白送这些）。
- **无人在环 / 中断**：提交契约是 milestone 级，没有工具调用级审批或中途打断（OpenCode/OpenHands/Hermes 都有）。

---

## 5. Regent 的实打实强项（别在补课中退化）

- **VerificationAgent + gap 路由 + 验收后不可变快照**：治理/可审计强于绝大多数 OSS，是差异化资产。
- **Agent 主循环扎实**：预算循环、submit 契约、去递归 repair、gap 指纹级跑飞停止——比"裸 agent loop"成熟。
- **里程碑/目标拆解模型**：goal → milestone → subagent 的结构化治理，OSS 多不具备。
- **CJK token 估算已修**、缓存 token 已解析、中文 Skills 路由已修（W4 闭环）。

---

## 6. 建议优先"正视"的遗漏（按风险/隐蔽性排序）

1. **缓存黑盒**（沉默·成本高）→ 加 cache_control 布局 + 断点检测 + 命中率进 probe。
2. **无 per-tool 安全层**（沉默·风险高）→ 至少加工具调用危险度打分 + 危险动作审批门。
3. **无改前回滚**（沉默·不可逆）→ 工作区改动前拍快照。
4. **工具级跑飞检测**（沉默·烧钱）→ 在 `gap_repeat` 之外补工具调用重复检测。
5. **无流式/事件协议**（可观测债务）→ 这是让 3.5 节所有面板能亮的前提。
6. **子 Agent 不能主动 spawn / 无 MCP**（能力缺口）→ 影响复杂任务与生态。
7. **内存单层 / Skills 无渐进披露自创**（体验缺口）→ 影响长期智能化。

> 注：本诊断不主张立刻吸收 CC。OpenCode/OpenHands/crewAI/Hermes 已覆盖上述绝大多数能力，可作为各自方向的参考样本（如安全层看 OpenHands/Hermes、内存看 crewAI/Hermes、事件流看 OpenHands、跑飞检测看 OpenCode）。
