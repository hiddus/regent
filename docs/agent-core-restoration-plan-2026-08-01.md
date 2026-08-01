# Agent 内核修复计划（2026-08-01）

> 状态：ACTIVE — 提出即取代 `docs/cd6-execution-plan-2026-07-31.md` 的执行优先级
> 审视方式：产品 / 技术架构 / AI 科学三方独立复审 + 逐条源码核验
> 结论一句话：**治理层不是过度设计，它是 Agent 内核能力缺失后的代偿性增生。**

---

## 0. 判决

所有者的判断——「项目核心是 Agent，但 Agent 最核心的是 loop / 上下文 / Skills / 大模型，当前只看到治理框架，再好也是一次性生成的垃圾数据」——**成立**。

但需要一处修正：**不是「只有治理框架」**。Agent loop、分段上下文预算、autoCompact、子 Agent、项目记忆都真实存在且工程质量不低。问题在于这套内核被**三个结构性错误**锁死在一个稳态上：

> 静态检查通过 → 运行时从未真跑 → 每次从零重来

而 36,068 行治理层做的事，是把这个稳态**记录得非常完整**，而不是改变它。

---

## 1. 事实基线

### 1.1 生产数据（`docs/gq3-experiment-report-2026-08-01.json`，今日生成）

| 指标 | 实测值 | 阈值 | 判定 |
|---|---|---|---|
| 真实 Goal 数 | 39（artifact-backed 35 / agentic 4） | ≥30/臂 | 样本不足 |
| ACHIEVED pass_rate | **0.0**（两臂 fail_rate 均 = 1.0） | — | 归零 |
| preview_ready_rate | **1/39 = 2.56%** | — | 归零 |
| first_runnable_rate | **0.0** | — | 归零 |
| human_intervention_rate | **1.0** | ≤0.2 | 超标 5× |
| mean_repair_rounds | 4.54 / 5.5 | ≤3.0 | 超标 |
| p95 延迟 | 67,475,699 ms ≈ **18.7 小时** | — | 失控 |
| decision | `INSUFFICIENT_EVIDENCE` + `funnel_degraded` | — | 主链路不健康 |

同时 `delivery-pipeline-unblock-plan-2026-08-01.md:24-27` 记录「无更新 32 天」「PENDING 21」。PRD §0.4 把 `apps/` 定义为目标成果，实际 **0 个 Regent 生成物**——现有两个应用都是手写的。

### 1.2 代码体量与开关状态

| 项 | 事实 | 来源 |
|---|---|---|
| 治理编排层 | 36,068 行 / 107 文件 | `core/src/regent/application/` |
| ├ 单文件最大 | `execution_orchestrator.py` 4,472 行 / 44 方法 | — |
| ├ 组织 + 多 Agent 簇 | 4,213 行，**大于整个 agent/** | `organization_*.py` `hive_runtime.py` 等 |
| └ compliance/privacy/north_star | 2,015 行，`compliance_risk_service` 全仓仅 1 处引用 | — |
| Agent 内核 | **2,518 行 / 13 文件** | `core/src/regent/agent/` |
| 模型层 | **368 行**，单一 OpenAI 兼容 provider | `core/src/regent/model/` |
| 能力池 | 3 个 bootstrap 能力，**从未接入 tool schema** | `capabilities/` |
| Agent 默认状态 | `generation_strategy="artifact-backed"`、canary=0、gate=False | `config.py:33-39` |
| 多 Agent 默认状态 | `aar1_certified_hive=False`、`delivery_batch_enabled=False` | `config.py:40,66` |

> **agentic 分支默认关闭。** 2,518 行 Agent 在生产上只跑了 4 个 goal，全部失败。

---

## 2. 根因：三个结构性错误

这不是投入不足，是三个错误的**乘积**。任何一个不修，另外两个的修复都不会显现效果。

### 根因 A — 系统在自造失败（隐式契约）

**A1. 强制 health 端点，且从未告知 Agent。**
`verification.py:265-267` 无条件把 `/api/health` 和 `/health` 追加进 smoke 探测路由：

```python
for extra in ("/api/health", "/health"):
    if extra not in routes:
        routes.append(extra)
```

探测用 `urllib.request.urlopen`（`verification.py:238`），该 API 对 **4xx 直接抛 HTTPError** → 被 `:245` 的 `except Exception` 捕获 → `SMOKE_FAIL`。
而 `:241` 的 `if int(code) >= 500` 是**永远走不到的死分支**。

⇒ **任何未实现 `/health` + `/api/health` 的应用一律 smoke 失败。**
而 system prompt（`context_assembler.py:52-62`）只说 "must be a real WSGI/ASGI app"，**从未提及这两个路由**。

**A2. 入口对象契约同样隐藏。**
探测脚本 `verification.py:207-210` 要求：模块路径必须是 `src/app.py` 或 `app.py`，且模块级必须暴露名为 `app` 的对象，并以 `import` 方式加载（非 `__main__`）。用标准 `if __name__ == "__main__": uvicorn.run(...)` 写法的 FastAPI 应用**必然失败，且 Agent 永远不知道为什么**。

**A3. 产物形态被硬编码成 4 个文件。**
`planned_path_policy.py:11-30`：`DEFAULT_PLANNED_PATHS` = `src/app.py` / `src/index.html` / `requirements.txt` / `README.md`；`_ALLOWED_SUFFIXES` 只含 `.html/.css/.js/.py/.md/.txt/.json`。
`tools.py:200-212` 的快照后缀白名单同样**不含 `.ts/.tsx/.jsx/.vue/.svg/.sql/Dockerfile`**；工具命令白名单 `tools.py:133-149` **无 node/npm/git**。

⇒ Agent 写一个 React/Vue 前端 → 快照近乎为空 → `RuntimeError("agent produced no files")`（`agent_runner.py:285`）或 empty-changeset。
更糟的是**归因错误**：`generator.py:258-263` 报的是 "planned-path filter"，而真凶常常是后缀白名单，导致恢复策略走错分支。

**A4. 快照静默截断。**
`tools.py:222` 的 `break` 在 80 文件处按字母序截断，无告警。**verification 用的是同一个被截断的快照**（`verification.py:30`）——验的不是真实产物。

**A5. 模型响应被截断 → 假完成。**
`provider.py:275-276` 的 `_parse_tool_calls` 对 JSON 解析失败静默 `continue`；`chat()` 未传 `max_tokens`，`finish_reason` 在 `:201` 取出后**从不使用**。
⇒ 模型输出被长度截断 → arguments JSON 不完整 → 工具调用被丢弃 → `agent_runner.py:203` 的 `if not assistant.tool_calls: break` 认为**「交付完成」**。这是一条从「模型被截断」直达「宣布成功」的完整静默路径。

> **A 类合计：系统性制造 delivery gap，再用 1,553 行 `delivery_gap_recovery.py` 去"恢复"一个根本不存在的缺陷。治理层的规模，相当比例是被自造故障喂大的。**

### 根因 B — 修复是冷重启，不是收敛

`agent_runner.py:306` 用 **递归** `self.run()` 实现修复轮，递归内 `:107-113` 重建 `ContextAssembler` 并把 `conversation` 清空为 `[]`。后果四条：

| 后果 | 位置 | 说明 |
|---|---|---|
| B1 因果链丢失 | `:113` | 修复轮只继承 `gaps` 文本。工作区文件还在，但「为什么这么写」的推理轨迹没了——Agent 面对的是自己写的陌生代码。这是 cold restart with hints，不是 repair。 |
| B2 预算护栏被绕过 | `:116-119` | `started` / `input_tokens` / `output_tokens` 都是局部变量，递归重置。`agent_nested_repair_max=2` ⇒ 实际墙钟上限 **3×900s = 2,700s**、token 上限 **3×200k = 600k**。治理层读到的预算数字是失真的。 |
| B3 审计链断裂 | `:327` | `return repaired` 直接丢弃外层 `transcript` / `compact_events` / token 计数。`generator.py:157-182` 持久化的只有最后一轮，**成本系统性少算**。 |
| B4 工作成果丢弃 | `:281-282` | 轮次耗尽走 `for/else` 抛 `BudgetExhaustedError`，此时 `snapshot_files()`（`:284`）根本没执行——40 轮产出的文件留在 sandbox 里无人认领。 |

**B5. 验证阶梯长于修复预算（结构性死锁）。**
`verification.py:53 / 67` 是短路结构：静态有 gap 就不跑 tests，tests 有 gap 就不跑 smoke。走完三层至少需要 3 轮，而 `agent_nested_repair_max=2`。
⇒ **除非首轮静态全绿，否则结构上不可能触达运行时验证。** 系统的稳态就是「静态通过、从未真跑」。

**B6. temperature 恒为 0。**
硬编码在调用点 `agent_runner.py:183`。修复轮 conversation 已清空、workspace 基本不变、唯一扰动是几百字符的 gaps 文本 vs 几万字符前缀。
⇒ 确定性 argmax + 弱扰动 = **模型倾向输出同一个错误答案**。「修复轮反复撞墙」在数学上可预期。

**B7. 没有 edit 工具。**
`tools.py` 只有全量 `write_file`。改一行要重发整个文件 ⇒ output token 爆炸，且每次重写都引入新错误 ⇒ **修复轮越多，退化越严重（regression by rewrite）**。

### 根因 C — 知识在验证器里，不在生成器里（Skills 缺位）

三个 capability.json 的真实形态：
- `allowlisted-http-source-v1` = RSS 白名单配置
- `delivery-review-v1` = **静态检查阈值表**（`min_style_chars:220`、`min_style_signals:4`、stub_titles 黑名单）
- `product-surface-v1` = 4 条文字 guidance

`load_capability_tool_specs`（`capability_tools.py:54`）**全仓库无生产调用点**，唯一引用是它自己的单测 `tests/unit/agent/test_capability_tools.py:8`——**测试制造了「已接入」的假象**。

Claude Code 意义上的 Skill = SKILL.md（渐进披露知识）+ scripts + references，回答「**怎么做**」。
Regent 这三个回答的是「**做没做对**」。**方向完全相反。**

**机理：为什么这必然产出一次性垃圾**

1. **Goodhart / reward hacking。** `min_style_chars:220` + 4 个 style_signals 是可被表面满足的 proxy metric。Agent 不知道「怎么做好界面」，只知道「CSS 要够 220 字符、要出现 flex/padding」。LLM 会精确产出**恰好满足计数的最小产物**。用验收指标当唯一信号却不提供达成路径 ⇒ 静态全绿、真跑空壳。
   （`delivery_review_service.py:78-85` 对 SMALL 目标还会降到 40/1/80 并关掉 `require_observation_hook`。）
2. **隐式契约不可学习。** 根因 A1/A2 的契约藏在验证器里，Agent 无法从失败中推断出「要加 /health」——它收到的 gap 文本是 `SMOKE_FAIL: request /health failed: HTTP Error 404`，而它并不知道这是**框架强加**的而非需求。
3. **无跨 run 知识累积。** 唯一的累积机制 `REGENT.md` 是**无 LLM 的字符串拼接**（`project_memory.py:59-110`），把 goal/stack/文件路径/gap 文本 append 进 5 个固定 section。存的是日志，不是做法。
   ⇒ **第 N 次生成不比第 1 次强。这就是「一次性」的操作定义。**

---

## 3. 附带发现（不构成根因，但影响可信度）

| # | 问题 | 位置 |
|---|---|---|
| D1 | 默认部署**没有隔离**：`REGENT_SANDBOX_MODE` 默认 `local` → `LocalSandboxDriver` 在 worker 容器内 `create_subprocess_shell` 直接执行；`sandbox.py:476` 一行 `del allow_network` 使 egress fail-closed 在该路径**完全失效**，`pip install` 裸奔 | `compose.yaml:22`、`sandbox.py:469-482` |
| D2 | N-3c / N-3d **已闭合**（uid 用 `os.getuid()` 对齐并拒绝 root；缺 `REGENT_HOST_PATH_MAP` 时 fail-closed）——README 的已知阻断表已过期 | `sandbox.py:560-581`、`178-186` |
| D3 | Preview 是**静态文件服务器**（解 zip → 静态托管），后端逻辑在预览里根本不执行，与 system prompt 的 "no pure static hosting" 直接矛盾 | `deployment.py:130-169`、`worker/main.py:256` |
| D4 | 成功交付后**不写基线**：`last_good_draft_uri` 只在失败路径写入，成功后无人写 → 下轮 `_prepare_sandbox` 直接 `rmtree` 从空沙箱重来 | `execution_orchestrator.py:4116-4128`、`generator.py:347-353` |
| D5 | autoCompact 的摘要**不经过 LLM**（`HeuristicSummarizer` 头尾截断）；且 `_build_summary` 主动跳过 `[cleared]` → 被 micro_compact 清掉的内容**永久蒸发，连摘要都进不去** | `compact.py:147-165`、`agent_runner.py:85` |
| D6 | `micro_compact` 只清 `role=="tool"` 的 content；`write_file` 的完整文件内容留在 assistant 消息的 tool_call arguments 里，**永不被压缩**——这是最大膨胀源 | `compact.py:51-57` |
| D7 | 失忆方向反了：workspace 段每轮重建（文件状态永远新鲜），命令输出/traceback 被清成 `[cleared]`。Agent 保留「世界是什么」，丢失「为什么错」 | `agent_runner.py:280` |
| D8 | offload 悬空指针：>阈值的 tool 结果换成 `OffloadRef` JSON，但 `TOOL_SPECS` **没有 read_artifact 工具**——大 pytest 输出恰恰最易超阈值，关键日志静默蒸发 | `agent_runner.py:220-227`、`tools.py:11-99` |
| D9 | prompt cache 命中率恒为 0：易变 blob（workspace rglob + 5 个文件全文 + todos）放在 conversation **之前**，前缀每轮必变。40 轮 × ~40k 前缀 ≈ **1.6M token 全额重复计费** | `context_assembler.py:87-91`、`174-186` |
| D10 | `chat()` **零重试**（`raise_for_status()` 裸抛，调用点无 try）。40 轮 × 3 递归 = 120 次调用，任一次 429 抖动即报废整个 run | `provider.py:195`、`agent_runner.py:180` |
| D11 | 无测试即视为通过（`degraded=True` 但不算失败）——这是真正的假绿口子 | `verification.py:63-65` |
| D12 | `_pick_free_port` 在**宿主机**探测空闲端口，供**容器内**绑定使用——命名空间不同，逻辑无意义 | `verification.py:290` |
| D13 | 109 个测试文件中 **101 个**用 mock；`tests/unit/agent/test_agentic_generation.py:93` 的 `_ScriptedProvider` 按调用计数返回硬编码 ToolCall。⇒ 上述 A5/B2/B3/B4 **在测试中不可能暴露** | `tests/` |

---

## 4. 修复计划

原则：**先让系统停止说谎，再让 loop 会收敛，再给它知识。** 顺序不可交换——在 A 修完之前，任何 B/C 的改进都会被自造失败淹没，无法归因。

### 阶段 0 — 止血：停止自造失败（约 1 周）

目标：让「失败」重新等于「真的失败」。这是所有后续测量的基线前提。

| # | 动作 | 文件 | 验收 |
|---|---|---|---|
| 0.1 | health 路由改为**仅在 `success_criteria` 显式声明时**探测；同时把入口契约（`src/app.py` 暴露模块级 `app` 对象）写进 system prompt | `verification.py:256-268`、`context_assembler.py:52` | 一个不含 health 端点的最小 Flask 应用 smoke 通过 |
| 0.2 | 快照白名单改**黑名单**（排除二进制 / node_modules / .git），`max_files` 提到 500，截断时**抛出可见告警**而非静默 break | `tools.py:195-224` | 写入 30 个 `.tsx` 的 run 能产出非空 changeset |
| 0.3 | 截断即失败：`finish_reason == "length"` 或 `_parse_tool_calls` 丢弃过任何条目 → 抛 `ModelOutputError`；payload 补 `max_tokens` | `provider.py:201-205`、`275` | 注入截断响应的测试断言抛错，而非 loop 正常 break |
| 0.4 | 轮次耗尽不丢件：`for/else` 改为 break + 快照 + 验证 + 标记 `degraded=true` | `agent_runner.py:281` | `max_turns=1` 强制耗尽时仍产出 changeset |
| 0.5 | provider 加重试退避（429/5xx/超时，指数退避 3 次） | `provider.py:190` | httpx mock 返回两次 429 后成功 |
| 0.6 | 修正 empty-changeset 归因：区分「后缀过滤丢弃」与「planned-path 丢弃」，分别上报 | `generator.py:258-263`、`390` | 错误信息能指明真实丢弃原因与文件清单 |
| 0.7 | 默认部署闭合隔离：`REGENT_SANDBOX_MODE` 默认改 `docker` 并挂 docker.sock（或 sysbox）；`sandbox.py:476` 的 `del allow_network` 改为 `allow_network=True` 时直接 `raise PermissionError` | `compose.yaml:22`、`sandbox.py:476` | worker 容器内无 proxy 配置时 `pip install` 被拒绝 |
| 0.8 | 更新 README 已知阻断表（N-3c/N-3d 已闭合，新增 D1） | `README.md:14-24` | 表与代码一致 |

**阶段 0 出口 gate（可证伪）**：重跑 20 个真实 goal，`preview_ready_rate` 从 2.56% 提升到 **≥30%**，且所有 smoke 失败都能归因到应用自身缺陷（无一例来自 health/后缀/截断）。

### 阶段 1 — 让 loop 会收敛（约 2 周）

目标：把「冷重启 + 重复同一错误」改成「单轨迹增量收敛」。

| # | 动作 | 对标 | 验收 |
|---|---|---|---|
| 1.1 | **修复轮改增量**：取消 `self.run()` 递归，gap 作为新 user 消息追加进**同一 conversation**继续 loop | SWE-agent 单 trajectory | 修复轮能引用上一轮的推理；重复同一错误率下降 |
| 1.2 | 预算跨轮累计：`started` / token 计数提到实例级或显式透传 | — | 3 轮修复总墙钟 ≤ `max_wall_seconds` |
| 1.3 | 合并 transcript 与成本：不再 `return repaired` 丢弃外层 | — | DB transcript 轮数 = 所有修复轮之和 |
| 1.4 | **加 `str_replace` / `edit_file` 工具** + `grep` / `glob` | Claude Code Edit/Grep | output token/轮 下降 ≥5×；重写引入的回归下降 |
| 1.5 | **验证反转**：取消 `verification.py:53/67` 的短路，一次跑完三层给全量 gap | — | 单轮 gap 覆盖率提升；不再出现「结构上够不到 smoke」 |
| 1.6 | 修复轮温度阶梯（0 → 0.4 → 0.8），或 best-of-N 并行 + **用 verification 当 selector** | AlphaCode 采样多样性 | 修复轮跳出局部最优率提升 |
| 1.7 | 补 `read_artifact` 工具，解 offload 悬空引用 | — | 大 pytest 输出可被 Agent 重新取回 |
| 1.8 | 显式 `submit` 工具 + 完成度自检终止，替代「不调工具 ≡ 完成」 | SWE-agent | 无 submit 的 run 不被判为成功 |

**阶段 1 出口 gate**：`mean_repair_rounds` 从 4.5 降到 **≤2.5**；`first_runnable_rate` 从 0 提升到 **≥40%**。

### 阶段 2 — Skills 层（约 2-3 周）

目标：把知识从验证器搬到生成器。**这是「一次性 vs 可累积」的分水岭。**

**2.1 建立 Skill 格式**：`SKILL.md`（渐进披露的做法）+ `scripts/`（可执行脚手架）+ `references/`（模板片段）。接入 `TOOL_SPECS`，Agent 可按需加载。

**2.2 首批 7 个 Skill**：

| Skill | 内容 | 直接解决 |
|---|---|---|
| `deploy-contract` | 入口对象契约、模块路径、health 约定的**显式化** | 根因 A1/A2 |
| `web-app-scaffold` | 目录约定 + 入口 + 钉版本依赖 + 可执行脚手架脚本 | 每次架构都不同 |
| `ui-design-system` | 真实 CSS token 表 + 组件片段库 | **取代 220 字符计数** |
| `persistence-layer` | SQLAlchemy schema / migration / seed / 空状态 | 假占位数据 |
| `http-api-contract` | 路由 / 错误码 / 分页 / health | API 随机性 |
| `test-harness` | pytest fixture + smoke 模板 | D11 无测试即通过 |
| `evidence-rendering` | RSS → 带来源标注的产品内容 | 证据未被用进产物 |

**2.3 `delivery-review-v1` 双向化**：每条检查规则必须配一条「怎么做到」的 Skill 引用。规则表不再是唯一信号。

**2.4 REGENT.md 改 LLM 蒸馏**：从 append 日志改为蒸馏「可复用做法」，并支持跨 run 检索。

**阶段 2 出口 gate**：同一目标连续生成 5 次，**架构一致性 ≥80%**；第 5 次的首轮静态通过率显著高于第 1 次（**证明知识在累积**）。

### 阶段 3 — 上下文与经济性（约 1-2 周）

| # | 动作 | 预期 |
|---|---|---|
| 3.1 | 缓存友好布局：静态段固定前置，易变段（workspace/todos）移到**尾部**，打 `cache_control` | input 成本下降 5-8×，TTFT 下降 |
| 3.2 | micro_compact 策略反转：**保报错，清可重取的文件全文**；tool_call arguments 纳入压缩；改为压力驱动而非无条件执行 | 修 D6/D7，失忆方向纠正 |
| 3.3 | 被清内容先落 `context_artifacts` 再清，并让摘要能看见 | 修 D5 永久蒸发 |
| 3.4 | autoCompact 摘要改 LLM 生成 | 摘要质量 |
| 3.5 | streaming + planner/executor 双模型分工 | 用户可早停；架构决策与改 typo 不再同价 |

### 阶段 4 — 冻结与回收治理层（阶段 1 完成后启动）

**立即冻结（不删除，停止投入）**：

| 模块 | 行数 | 理由 |
|---|---|---|
| 组织 / 多 Agent 簇（`organization_*`、`hive_runtime`、`multiagent_metrics`） | 4,213 | 默认关闭；单 Agent pass_rate=0 时讨论多 Agent 无意义 |
| compliance / privacy / north_star | 2,015 | PRD §2.1 已排除高风险场景；`compliance_risk_service` 全仓 1 处引用 |
| GQ 实验框架 | — | 两臂 pass_rate 都是 0，实验无信息量 |
| `capability_tools.py` + 其单测 | 82 | 死代码。要么接入 `TOOL_SPECS`，要么连同测试删除 |

**结构性回收（阶段 4.2）**：把 `failure_lessons` / `learned_constraints` / `replan_nonce` 的组装从 `execution_orchestrator.py:1404-1444` **下沉到 `ContextAssembler`**，编排器只传 `goal_id`。
理由：Agent 的「上一轮我错在哪」现在要经过 DB 往返 + 事件总线 + 编排器序列化，再降级成几行文本喂回去——这是把 loop 的内存拆成了分布式状态机。
验收：orchestrator 该段 <80 行，且 Agent 测试可独立构造修复上下文。

**产品链路补齐（与阶段 2 并行）**：
- 4.3 Preview 换容器运行时（复用 DockerSandboxDriver），修 D3。验收：Preview 上 POST 一条数据、刷新后仍在。
- 4.4 成功发布后写 `last_good_workspace`，REVISE 以它为 `base_workspace`，修 D4。验收：第二轮 changeset 中 REPLACE > 0 且文件数不归零。
- 4.5 默认埋点 SDK 进 planned_paths。验收：无人工 POST 即产生 `is_internal=False` 的 Observation。

### 阶段 5 — 测试可信度（贯穿）

- 5.1 新增 provider 录制/回放层（VCR 式），至少覆盖**截断、429、畸形 tool_call** 三种真实响应。验收：阶段 0.3 的缺陷能被测试独立捕获。
- 5.2 至少 1 条**不打 mock 的端到端**：真实模型 → 真沙箱 → 真 smoke → 真 Preview。

---

## 5. 总验收：一条真实闭环

所有阶段的最终 gate 只有一条，且不可用内部指标替代：

> **1 个真实目标 → 生成一个真能用的 App → 3 位真人使用 → 产生 1 次由真实反馈驱动的迭代 → 第二版基于第一版增量修改（非从零重来）。**

配套量化门槛（n≥20）：

| 指标 | 现状 | 目标 |
|---|---|---|
| preview_ready_rate | 2.56% | ≥50% |
| first_runnable_rate | 0.0 | ≥40% |
| human_intervention_rate | 1.0 | ≤0.3 |
| mean_repair_rounds | 4.5 | ≤2.5 |
| 跨 run 架构一致性 | 无 | ≥80% |

达不到之前，**不恢复 GQ-4 推广、不解冻 Hive、不新增治理模块**。

---

## 6. 与现有计划的关系

- 本计划**取代** `docs/cd6-execution-plan-2026-07-31.md` 的执行优先级。CD-6 中与阶段 0 重合的项并入阶段 0，其余降级为阶段 4 之后。
- `docs/conversational-delivery-next-plan-2026-07-31.md`（CD-6…12）中涉及治理增强的条目**全部冻结**，直到总验收通过。
- README「已知阻断」表需按 D1/D2 更新（阶段 0.8）。
- 本计划的判断如与 PRD 冲突，按 README 约定：产品语义以 PRD 为准，但**若 PRD 承诺的价值链路已被证明未交付（0 个生成物、pass_rate=0），应先修实现再谈规范**，必要时出 ADR。
