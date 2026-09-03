# Agent 内核完成度审计 · 对标 Claude Code（2026-08-02）

> 范围：AgentLoop / Tools / Skills / 上下文工程 / Prompt Cache / 子 Agent / 交互面
> 方法：读源码 + 跑测试 + 对账项目自身出口 Gate（`docs/agent-core-restoration-executable-plan-2026-08-01.md`、`docs/token-cost-cache-fix-plan-2026-08-01.md`）
> 证据基线：`tests/unit/agent` 62 项全绿；`tests/unit + tests/architecture` 9 项失败（详见 §7）

## 复审勘误（2026-08-02 · W4）

1. **「repair 不吃主预算」有误**：`max_extra_turns` 是阶段帽；repair 轮仍计入 `max_turns` 与 wall/token。  
2. **「`.env=agentic` 一定降级」过绝对**：仅在 `QUAL` 未达可显式 agentic 时成立；`OFFLINE_QUALIFICATION+` 允许显式 agentic。  
3. **对 Claude Code 的分项打分**为产品推断，非源码对账；治理 5.0 偏高（cache 观测 / 定量门禁当时未齐）。  
4. **漏写已落地能力**：真 Preview 进程、recoverable REVISE、资格相邻+报告门、soft-pause continue、gap→skill 反查。  
5. W4 已修：CJK token 计权、`probe_cache_hit`、中文 Skill 路由、live golden 车道（见 `agent-core-next-wave-plan-2026-08-02.md`）。

---

## 0. 一句话结论

**Agent 内核「骨架完整、地基扎实、但没上场」。**

- 代码层：M0–M5 的工程接线确实落地，失败语义 / 预算 / 验证 / 去递归修复这些**别人常缺的硬骨头，Regent 反而做得比 Claude Code 严谨**。
- 能力层：Agent 作为「通用工作体」的那一半——工具面、Skills 机制、上下文自适应、交互可操控性——**只完成三成左右**。
- 交付层：生产 `agentic_qualification_state=DISABLED`，`canary_percent=0`。即使 `.env` 写了 `REGENT_GENERATION_STRATEGY=agentic`，`generation_strategy_policy._default_strategy()` 也会强制降级为 `artifact-backed`。**Agent 当前承担 0% 生产流量。**

> 「本项目核心是 Agent」这句话，在代码层完成度约 **65%**，在生产交付层完成度 **0%**。

---

## 1. 能力计分卡（0–5）

| 维度 | Regent | Claude Code | 判定 |
|---|---|---|---|
| AgentLoop 主循环 | **4.0** | 4.5 | 循环骨架合格，缺流式与打断 |
| 工具集 Tools | **2.0** | 5.0 | 10 个本地工具，**无 Web / 无子 Agent / 无 MCP** |
| Skills 库 | **1.5** | 4.5 | 是「提示词片段路由器」，不是 Skill 系统 |
| 上下文工程 | **3.5** | 4.5 | 分层+双轨压缩到位，但中文 token 估算是 bug |
| Prompt Cache | **3.0** | 4.0 | 布局已修、解析已做，**命中率无出口** |
| 子 Agent / 并发 | **1.5** | 4.0 | 有 SubagentRunner 但串行、模型不可调用 |
| 交互 / 可操控性 | **1.0** | 5.0 | 无流式、无中断、无中途插话 |
| 记忆 | **3.0** | 3.5 | REGENT.md + 语义/情景记忆，蒸馏偏粗 |
| 验证 / 自愈 | **4.5** | 3.0 | **强于 CC**：独立 verifier + gap 路由 + 反抖动 |
| 治理 / 预算 / 审计 | **5.0** | 2.5 | **显著强于 CC**：Permit / 账本 / 不可变 Artifact |

加权观感：**内核成熟度 ≈ Claude Code 的 55–60%**，但两者强项不重叠——Regent 强在「可治理的交付机器」，CC 强在「可协作的通用工作体」。

---

## 2. AgentLoop（4.0 / 5）

### 已落地（有源码可查）

| 能力 | 位置 |
|---|---|
| 单轨迹预算循环 | `agent_runner.py:237` |
| 显式 `submit` 才算完成；无 tool_call 只算「停止」 | `agent_runner.py:354`、`:576-579` |
| `finish_reason` 分类 → `MODEL_TRUNCATED` / `TOOL_CALL_INVALID` fail-closed | `agent_runner.py:339-352`、`provider.py:310-321` |
| 三重预算（wall / token / turns），耗尽先落盘诊断再抛 | `agent_runner.py:113-169` |
| **去递归 repair**：gaps 作为新 user turn 追加到同一轨迹 | `agent_runner.py:538-549` |
| 反抖动：gap fingerprint 重复即停 | `agent_runner.py:483-495` |
| repair 阶段独立轮次上限，不吃主预算 | `agent_runner.py:442-455` |
| HTTP 退避（429/5xx/超时抖动重试；400/401/403 不重试） | `provider.py:343-406` |
| 跨 repair 累计账本 | `run_ledger.py` |
| tool 异常转为模型可见文本，不炸循环 | `tools.py:540-541` |
| `write_file` 对 `.py` 先 `compile()` 预检 | `tools.py:486-494` |

这套「submit 契约 + failure taxonomy + 预算落盘」是 Claude Code 没有的工程严谨度，属于本项目真正的资产。

### 缺口

1. **无流式输出**。`provider.py` 全文 0 处 `stream`，一次性 POST 等完整响应。用户只能看到 `on_turn` 的「第 N/40 轮」文字，看不到 token 级进展。长 run（20–40 轮）体验上等同黑盒。
2. **无中断 / 中途插话（steering）**。全仓 grep 无 cancel / interrupt token。一旦起跑只能等预算耗尽或 submit。现有 `resume_after_human`（`delivery_gap_recovery.py:820`）是**跨 Run 粒度**——「这次交付失败了，人来给方向」，不是「循环中改需求」。CC 的 Esc 打断 + 补充指令是核心交互，这里完全没有。
3. **工具串行执行**。`for call in assistant.tool_calls:` 逐个 `await`（`agent_runner.py:359-437`）。CC 对只读工具（read/glob/grep）并发执行。多文件探查时延迟线性叠加，直接吃 `max_wall_seconds=900`。
4. **无 per-call 授权钩子**。`run_command` 靠前缀白名单（`tools.py:235-251`），治理在更外层的 Permit。粒度不同：无法做「这条命令要不要放行」的实时判断。
5. **`micro_compact` 每轮全量重建 conversation**（`agent_runner.py:437`），在长 run 下是 O(n²) 的对象拷贝——目前规模无碍，但轮次上限提高后会显性。

---

## 3. Skills 库（1.5 / 5）—— 差距最大的一块

### 现状

7 个包：`evidence` / `http-api` / `persistence` / `runtime-contract` / `test-harness` / `ui` / `web-app-scaffold`。
每包 = `SKILL.json`（元数据）+ `GUIDANCE.md`（**308–473 字节**，约 5 条 bullet）。
`skills.py` 共 142 行：manifest 载入、`content_hash`、关键字路由、gap→skill 反查、消融报告算术骨架。

### 逐项对标

| 维度 | Claude Code | Regent | 差距 |
|---|---|---|---|
| 发现机制 | 系统提示只放 name+description，模型判断后用 Skill 工具拉正文 | 关键字命中后**整段 guidance 直接塞进 user 前缀**（`context_assembler.py:155-170`） | 无渐进披露 |
| 披露层级 | 三级：元数据 → SKILL.md → references/scripts | 一级：全量注入 | 缺两级 |
| 可执行资产 | `scripts/` 可执行、`assets/` 模板 | **只有 markdown**，无脚本无模板 | M5-2 原文要求「脚本/模板」，未实现 |
| 扩展性 | 用户级 / 项目级目录，运行时可装 | 只有代码库内 `skill_packs/`，改 Skill = 改代码发版 | 无扩展点 |
| 路由 | 模型自己选 | `any(token.lower() in goal_text.lower())`（`skills.py:115`） | 见下方致命问题 |
| 消融验证 | — | `skill_ablation_report` 只有算术骨架 | M5-4 **未执行** |

### 致命问题：路由对中文近乎失效

`applies_when` 全是英文 token（`api`/`rest`/`flask`/`sqlite`/`pytest`…），路由是纯 substring 匹配。
而 `regent_validation_goals.md` 里 50 个验证目标**全部是中文**（「中国历史人物全集」「城市噪音地图」…）。
结果：绝大多数目标一个 Skill 都命不中，只能落到兜底分支——`if any(k in text for k in ("app","web","flask","api","site"))`（`skills.py:118`）——中文目标连这个也命不中，**Skill 注入为空**。

> 也就是说：M5 的 Skills 在本项目自己的验证目标上，实际命中率接近 0。

### 纪律偏差

计划第 8 节白纸黑字：「不在 M5 消融通过前扩充 Skill 数量」。
实际 `agentic-repair-wave-2026-08-02.md` R3 把 3 → 7。**先扩后证**，且出口 Gate「Skill 路由准确率 ≥ 90%」至今无任何报告。

---

## 4. 上下文处理（3.5 / 5）

### 做得好的部分

- **分层硬预算**：goal 8k / skill 6k / REGENT.md 24k / workspace 8k / todo 4k / failures 16k（`context_assembler.py:19-29`）
- **缓存友好布局**：`system → static user → conversation → volatile user`（`:115-134`），易变段一律后置
- **workspace 只给路径树、不给全文**（`:233-245`），强制模型用 `read_file`
- **目标每 10 轮重注入**防漂移（`:107`）
- **双轨压缩**：`micro_compact` 清旧 tool 结果 + 剥旧 `write_file/edit_file` 的 content（`compact.py:63-130`）；`autoCompact` 近窗时结构化摘要 + rehydration（`:235-306`）
- **压缩熔断**：连续 3 次失败直接 `BUDGET_EXHAUSTED`（`:175-178`），不无限重试
- **压缩前先落 artifact**（`agent_runner.py:264-274`），历史不丢
- **大 tool 结果 offload + `read_artifact` 带 hash 校验回读**（`agent_runner.py:375-388`、`tools.py:386-401`）

这套设计思路是对的，方向和 CC 一致。

### Bug 级问题（两个）

**① 中文 token 估算低估 3–4 倍 —— 高危**

```python
# compact.py:34-42
total += max(1, len(msg.content) // 4)
```

英文 ≈ 4 chars/token 成立；中文 1 字 ≈ 1–1.5 token。
autoCompact 阈值 = 128k − 15k = **113k 估算 token**。中文场景下，估算到 113k 时真实已约 **300k+**，早就爆窗。
后果：压缩**根本不会触发**，直接撞 provider 400 / `finish_reason=length` → 走 `MODEL_TRUNCATED` 失败路径。
本项目 50 条验证目标全中文 → 这是生产必炸的路径。

修法：CJK 字符单独计权（`cjk_count * 1.0 + ascii_count * 0.25`），并用上一轮 provider 返回的真实 `prompt_tokens` 做闭环校正。

**② `context_window_tokens` 写死 128k**

`AgenticCodeGenerator` 构造 `AgentRunner` 时没传该参数（`generator.py:115-127`），永远吃默认值，与实际模型窗口脱钩。换模型不会自动跟随。

### 其他缺口

3. autoCompact 默认用 `HeuristicSummarizer`（掐头去尾拼接，`compact.py:309-315`），不是 LLM 摘要。长 run 后语义损失大——`ContextCompactor` 支持注入 `Summarizer`，但 `agent_runner.py:107-111` 硬编码传了 heuristic 版本，**provider 就在手边却没用**。
4. `REGENT.md` 蒸馏是 crude section 字符串拼接（`project_memory.py:59-110`），上限 200 行 / 25KB，无去重无排序无衰减。
5. 上下文用量对模型不可见 —— 模型无法自我节流（CC 会告知剩余预算）。

---

## 5. Prompt Cache 命中（3.0 / 5）

### P0 阶段已完成（可验证）

- ✅ 布局止血：static / volatile 分离（`context_assembler.py:83-134`）——这是最关键的一刀，已落
- ✅ 删除每轮 workspace 全文 dump，改路径树
- ✅ goal reminder 移到 volatile 后缀，不污染稳定前缀
- ✅ `cached_tokens` 多字段兼容解析：`cached_tokens` / `prompt_cache_hit_tokens` / `cache_read_input_tokens` / `prompt_tokens_details.*`（`provider.py:40-61`）；**解析不到记 `None` 而非 0**，符合计划要求
- ✅ ledger 累加 + `cache_hit_rate` 属性（`run_ledger.py:17,69-73`）
- ✅ `micro_compact` 剥旧 write 参数是**滑窗**式的：更早的消息已定格为 `[cleared]`，每轮只失效尾部约 8 条 —— 前缀稳定性保住了，设计正确

### 缺口

**① 命中率没有出口 —— 护栏无法度量**

`cached_tokens` 只写进 workspace 里的 `.regent_run_ledger.json`（`agent_runner.py:610-613`）。
全仓 grep `cached_tokens|cache_hit`：只命中 `agent_runner.py` / `run_ledger.py` / `model/*` / `multiagent_metrics.py`。
**`ops/` 100 个脚本里 0 处**，`probe_m6_canary.py` 里 0 处，未落库、未进 `budget_entries`。

计划 §2.4 的成本护栏是「within-run `cached_tokens/prompt_tokens` 中位数 ≥ 40%（P0 后）/ ≥ 60%（P1 后）」——
**当前既测不出，也就不能宣称达标**。P1-3「探针/日更输出 cache hit rate」未完成，计划 §5 的勾选项 `[ ] cached_tokens 可观测` 实际只完成一半（能采集，不能观测）。

**② 无显式 cache 断点**

全仓 0 处 `cache_control` / `ephemeral`（已 grep 确认）。当前完全依赖 OpenAI / DeepSeek 的**隐式前缀缓存**。
换成 Anthropic 兼容端点，缓存收益直接归零。CC 是显式打断点的。
建议：provider 层加 cache breakpoint 抽象（OpenAI 端 no-op，Anthropic 端注入），断点位置 = tools 数组尾 + static prefix 尾。

**③ volatile 后缀每轮全量重建**

`workspace tree + todos + failures` 每轮重算（`context_assembler.py:95-113`），设计如此，代价是尾部必然 miss。
可优化为「tree diff」而非全树，进一步压缩每轮增量。

**④ 跨 Goal 缓存未做** —— 计划标记为 P2，属预期内，不算欠账。

---

## 6. Claude Code 有、Regent 没有的必备能力

| 能力 | 状态 | 影响 |
|---|---|---|
| **Web 抓取 / 搜索工具** | ❌ 完全没有 | 见下方专项 |
| **模型可调用的 Task / 子 Agent** | ❌ | `SubagentRunner` 存在但只能外层按 milestone 调度，且**串行**（`subagent.py:139-161` 注释自陈 parallelism 待做）。模型无法主动 spawn 降噪 |
| **MCP 接入** | ❌ | `agent/` 目录 0 处 mcp；`mcp_governance_service.py` 未进 loop |
| **认证能力池 → 工具面** | ❌ 死代码 | `capability_tools.py:1-12` 文件头自陈 "not injected into AgentRunner"。`capabilities/` 认证能力**根本没接进工具集** |
| 多模态（图像 / PDF） | ❌ | 无 |
| 流式 + 打断 | ❌ | 见 §2 |
| Hooks / 自定义工具 | ❌ | 无扩展点 |

### 专项：Web 能力缺失 = 目标与能力的结构性错配

`TOOL_SPECS` 共 10 个工具（`tools.py:21-174`）：`list_files` / `glob` / `grep` / `read_file` / `write_file` / `edit_file` / `read_artifact` / `run_command` / `todo_write` / `submit`。
唯一外网通路是 `run_command` 里的 `curl `（`tools.py:188` `_NETWORK_PREFIXES`），且生产默认关网、需 Permit + allowlist。

再看 `regent_validation_goals.md` 的 50 个目标与其验证维度映射：

| 维度 | 目标编号 | 依赖 |
|---|---|---|
| 大规模数据聚合 | 1–10 | 爬虫 / 入库 / 检索 |
| 实时数据 + 可视化 | 11–20 | 多源接入 / 清洗 |
| 个性化 + 长周期编排 | 21–28 | 外部内容源（arXiv / OJ） |

**前 28 个目标（56%）物理上依赖网络数据获取，而 Agent 没有一等公民的 Web 工具。**
这不是「做得不够好」，是「做不了」。这是整份审计里最需要产品侧决策的一条。

---

## 7. 测试与门禁实况

```
tests/unit/agent                    62 passed        ← Agent 内核自身全绿
tests/unit + tests/architecture      9 failed
```

9 条失败分两类：

**（A）陈旧计数断言 —— 无害，但污染门禁信号**

- `test_budget_ledger.py::test_all_cost_types_defined` → `assert 7 == 5`（新增 `external_operation` / `failure_recovery` 后未更新）
- `test_goal_execution_contract.py::test_event_catalog_contains_all_p1_events` → `assert 17 == 16`
- 同类还有 `test_evidence_chain_integrity` / `test_p2_committed_packages` 等

**（B）真实回归 —— 需要修**

- `test_delivery_batches.py::test_subagent_seeded_incremental_files`
  → `ArtifactIncompleteError: agent stopped without submit`（`agent_runner.py:577`）
  **subagent / 交付批次增量路径没跟上 M1-3 的 submit 契约。**
- `test_execution_orchestrator.py` 两条 deploy gap recovery 失败

> 建议：把 (A) 类断言从「硬编码计数」改成「集合包含」，否则每加一个事件就红一次，门禁会被驯化成「习惯性忽略」。

---

## 8. 对账项目自身出口 Gate

| 阶段 | 出口 Gate | 实况 | 判定 |
|---|---|---|---|
| M0 | 12 任务稳定终态 / 回放同码 / 账本无缺字段 | 冻结集 hash 匹配、5 类录制齐、taxonomy 落地 | ✅ |
| M1 | 静默成功=0 / manifest 完整率 100% / 预算耗尽发布数=0 | 代码全落地，单测锁住 | ✅ |
| M2 | 3 Profile golden / 生成·验证·Preview 同 hash | golden 有；但 **N-3c uid、N-3d 挂载残留未在生产主机验收** | ⚠️ 「真实启动」仍可能假绿 |
| M3 | `first_runnable_rate ≥ 50%`、`mean_repair_rounds ≤ 2.5`、单轮 token 中位数 ↓≥50% | 代码全落地，**三项定量指标无报告** | ⚠️ 未证 |
| M4 | 3 个 golden App 走完 生成→测试→Preview→用户操作→REVISE→第二版 | 只有 fixture 级；报告 `live_model_v2_green: false` | ⚠️ 未证 |
| M5 | 路由准确率 ≥90% / on-off 提升达阈值且 CI 不跨 0 | **消融未跑，无任何报告**；且违反「先证后扩」 | ❌ |
| M6 | preview_ready ≥60% / first_runnable ≥50% / human_intervention ≤0.3 / repair ≤2.5 | 窗口 `CLAMPED_PENDING_QUALIFICATION`，percent=0，QUAL=DISABLED | ❌ |
| Token/Cache | static 前缀稳定 / cached 可观测 / 单轮 ↓50% / 长 run 总 input ↓40% | 前缀稳定✅、解析✅、**观测出口❌、两项定量无报告** | ⚠️ 半程 |

---

## 9. 优先级建议

### P0 —— 让「Agent 是核心」这句话物理成立

| # | 动作 | 位置 | 估时 |
|---|---|---|---|
| 1 | **修中文 token 估算**：CJK 计权 + 用真实 `prompt_tokens` 闭环校正 | `compact.py:34` | 1d |
| 2 | **`cached_tokens` 落库 + 进 probe**，把成本护栏变成可度量数字 | `run_ledger` → `budget_entries`、`ops/probe_m6_canary.py` | 0.5d |
| 3 | **加受控 `web_fetch` / `web_search` 工具**（走 Permit + allowlist 代理），否则验证目标前 28 条不可达 | `tools.py` `TOOL_SPECS` | 2–3d |
| 4 | 修 `test_subagent_seeded_incremental_files` 的 submit 契约回归 | `delivery_batch_*` | 0.5d |
| 5 | 陈旧计数断言改集合包含，恢复门禁信噪比 | `tests/unit/application/*` | 0.5d |

### P1 —— 补齐 Agent 该有的机制

| # | 动作 |
|---|---|
| 6 | **Skills 改渐进披露**：系统提示只放 name+description，新增 `load_skill` 工具按需拉正文；路由改为「模型自选」或语义匹配，**至少支持中文** |
| 7 | **只读工具并发执行**（`list_files`/`glob`/`grep`/`read_file` 用 `asyncio.gather`），写类工具保持串行 |
| 8 | **autoCompact 换 LLM 摘要**：provider 已在手，`ContextCompactor` 已支持注入，只需在 `agent_runner.py:107` 换掉 heuristic |
| 9 | `context_window_tokens` 从 Settings 传入，跟随模型 |
| 10 | 跑完 M5-4 消融 + 补 M3/M4 定量出口 Gate 报告 |

### P2 —— 向 Claude Code 体验收敛

| # | 动作 |
|---|---|
| 11 | 流式输出 + 中途插话（steering），配 cancel token |
| 12 | provider 层 cache breakpoint 抽象（OpenAI no-op / Anthropic 生效） |
| 13 | 模型可调用的 Task 工具 + milestone 并行 |
| 14 | `capability_tools` 真正接入 `TOOL_SPECS`；MCP 网关接入 loop |
| 15 | volatile 后缀改 tree diff |

---

## 10. 一句话给决策层

> 别再往治理层加东西了 —— 那部分已经超配。
> 现在挡在「Agent 是核心」前面的是三件很土的事：**中文 token 算错、缓存命中看不见、Agent 上不了网**。
> 这三件加起来不到一周，修完之后 M5/M6 的门禁才有资格被谈论。
