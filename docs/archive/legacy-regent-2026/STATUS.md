# Regent 状态与变更（内部快照）

> 本文件记录 Regent 的内部开发状态、已知阻断与决策记录快照，**按日期更新**。
> 项目门面与快速开始请见 [`README.md`](./README.md)。本文件不作为对外承诺。

---

## 目标启动治理（2026-08-13）

Regent 现在将项目可行性分析作为正式执行的前置硬门，同时保留目标锁定后的“边跑边修”：

1. Goal 发布后保持 `DRAFT`，通过多轮对话确认价值、验收标准、范围、依赖、权限、风险、预算和候选路径。
2. 可行性结论统一为 `FEASIBLE`、`REVISION_REQUIRED` 或 `NOT_FEASIBLE`。
3. 只有至少完成两轮澄清、关键未知项已解决、结论为 `FEASIBLE`，并由 Goal Owner 确认当前 GoalSpec 版本和哈希后，目标才能锁定为 `FROZEN`。
4. 未通过可行性闸门时，只允许预算内追问、只读检查、估算、设计推演和明确授权的隔离验证；禁止业务代码生成、Workspace 写入、正式 Worker、生产写入和外部触达。
5. 每个 Goal 必须设置正数预算上限。欠费响应不重试；结构化输出只允许一次有界修复，且不会把完整错误输出重新灌入上下文。
6. 正式锁定后允许持续执行和修正；目标、范围、数据、权限、预算或不可逆影响发生重大变化时，必须退回重新确认。

控制台已增加“边界确认 → 可行性分析 → 目标锁定 → 正式执行”阶段导航，并仅在可行性、澄清轮次和未知项同时满足要求时显示锁定执行入口。

详细规则见 [`docs/decision-note-minimum-start-continuous-correction-budget-stop-2026-08-13.md`](./docs/decision-note-minimum-start-continuous-correction-budget-stop-2026-08-13.md)。

---

## 近期架构演进与缺陷修复（2026-08-23）

2026-08-13 之后合入的"组织修复"底座与一批缺陷修复。**接线状态（2026-08-23 复核更正）**：目标分类、组织模式选择、行为监测、行为修复环均已接入执行主链（commit `40e5378` 起接线）；修复环自动再调度与 worker 周期监测 tick 于 2026-08-23 补齐（见下）。仍未接线：`agent_invocation_guard.check_cross_deployment_invocation`。

- **Hub-and-spoke 执行纪律**：`application/agent_invocation_guard.py`；`config.max_subagent_depth` 默认 `3→1`（已落地，禁止 sub-agent 二次委派）；交付角色 `{product,tech,test,ux,ops}` 只向编排器汇报、互不调用；完整 guard 主链消费待接入。
- **规则式目标分类**：`application/goal_classifier.py`（`GoalProfile`：scale/domain/complexity/iteration_need/monitoring_need，不调用 LLM）→ `goal.metadata_json["goal_profile"]`；`application/organization_mode_selector.py` 据 profile 推荐 `WATERFALL/AGILE/HUB_SPOKE/BATCH`（推荐而非强制）。
- **运行时行为监测**：`application/runtime_behavior_monitor.py` 独立后台观察已发布预览（内容体量/对话真实感/角色多样性/世界观一致性）；`domain=interactive-app` 触发 **SPA JS 深度分析**（抓取脚本、统计角色/场景/周期/对话护栏/角色深度）。不经新路由暴露。触发点：`PREVIEW_DEPLOYMENT_SUCCEEDED` 回调 + `application/behavior_monitor_tick.py` worker 周期 tick（默认 600s，`behavior_monitor_enabled` 可关）。
- **行为修复环（已闭环，2026-08-23）**：`behavior_repair_loop.py` 消费观测，REPAIR 决策合并写入 `session_steer_brief`（用户/系统 steering 优先保留），并经 `GoalExecutionService.start`（`guidance-continue:behavior-repair:` 幂等键，与用户 RESUME 同通道）自动再调度。护栏 = 目标 ACTIVE + 无存活 run + `org_mode.max_iterations` 上限（缺省 3）+ 预算未 blocked，全部在 goal 行锁事务内原子判定；另有 `behavior_repair_retrigger_claim`（TTL 300s）防双通道重复触发。同时修复 orchestrator/tick 监测元数据不落库的隐性 bug。
- **执行编排器精简**：`execution_orchestrator.py` 移除 7 个旧处理器，新增"直达生成"旁路（`_is_direct_generation_goal` / `_bypass_pipeline_to_generation`）。
- **缺陷修复**：#8 冻结计划白名单（`planned_path_policy` 允许 `source/`/`.sh`/`.conf`）；#9/#11 静态产物路由（`runtime_preview`/`deployment` 深搜 `source/static/index.html`）；#12 预算账目悬空 `run_id` 置空（`budget_ledger`）；ship-first 工作区（`workspace_writer` REPLACE→CREATE 降级、`expected_previous_hash` 仅告警、丢弃 `.regent_*` byproduct）；Agent 防自循环（滑动窗口指纹 warn@3/ask@6）；预算预留键"下一空闲后缀"单次查询；引导澄清轮次计入 `clarification_rounds`；预览资源按扩展名白名单（`api/main.py`）；Docker 构建 hatchling 冲突修复；控制台对话化（移除 ~147 行）。
- **迁移 head**：`20260810_0047`（0044 预算预留 / 0045 LearningUpdate / 0046 OrganizationExperiment / 0047 ExecutionEvent）。

Hive 固定模板 `pm-dev-independent-qa-v1` 作为独立 opt-in 模板并存，与 hub-and-spoke 动态纪律不互斥。对外表述口径：运行时自修复环已端到端（护栏内、可审计）；组织模式自适应重评估与跨部署调用环检测仍属未竟项，不得宣称。文档同步见 PRD §0.5、Tech-Spec §0.1/§4.6/§21/§25、Plan §15。

---

## 当前状态（2026-08-10）

- P0 已形成可运行闭环：目标、工作项、执行、审批、证据、观测、恢复与审计。
- P1 已完成至 `0022`：GoalSpec 快照启动后由 Worker 持久化生成、检查并发布 Preview；对话可查询进度、失败可重试。
  - 主链路采用**快照启动 + 事后纠偏**，见 [`docs/decision-note-auto-start-journey-2026-07-31.md`](./docs/decision-note-auto-start-journey-2026-07-31.md)。
- **生成策略事实源**：代码默认是 `agentic`，默认 canary 为 `0%`；部署环境可以覆盖。`/health/ready` 与 `/v1/health` 返回不含密钥的 `runtime_profile`，文档不再把历史观察窗当作当前生产事实。
- **Prompt cache / token 成本**：agentic 上下文改为「稳定前缀 + 对话 + 易变后缀」，workspace 默认仅路径树；解析并累计 `cached_tokens`。计划：[`docs/token-cost-cache-fix-plan-2026-08-01.md`](./docs/token-cost-cache-fix-plan-2026-08-01.md)。
- **交付缺口**：进度停滞与交付缺口优先自动续跑 / 软暂停（对话补充方向），**不**对缺口卡弹出「总是允许」；真审批仍限于发布、质量门、外部效应、Permit。
- Agent 内核 M0–M5 工程接线已落地，**W4 收口**（CJK token、质量门、live golden lane）；M6 观察窗 **CLAMPED_PENDING_QUALIFICATION**（canary off；watch **HALTED**；下一步须 **Offline Qual**；见 `docs/m6-canary-window-2026-08-01.json`）。GQ-4 仍 PENDING。
- **混合控制平面 H0–H2（2026-08-03 落地）**：abort / permission / ask 工具 / result surface / 只读时间线，见 [`docs/decision-note-hybrid-h0-control-plane-2026-08-03.md`](./docs/decision-note-hybrid-h0-control-plane-2026-08-03.md) 与 [`docs/execution-plan-hybrid-control-experience-ops-2026-08-03.md`](./docs/execution-plan-hybrid-control-experience-ops-2026-08-03.md)。
- **Session Work Plan（W0–W4，2026-08-03）**：Step-0 门禁 + 计划审批，见 [`docs/decision-note-session-work-plan-2026-08-03.md`](./docs/decision-note-session-work-plan-2026-08-03.md) 与 [`docs/execution-plan-session-work-plan-2026-08-03.md`](./docs/execution-plan-session-work-plan-2026-08-03.md)。
- **控制台可观测性（2026-08-02）**：SSE 为主 + ProgressEvent + 活动 API，见 [`docs/console-observability-gap-2026-08-02.md`](./docs/console-observability-gap-2026-08-02.md)。
- **交付缺口恢复 / 诊断交付（2026-08-02/03）**：A0 退出禁止静默续跑，见 [`docs/decision-note-delivery-machine-invariants-2026-08-02.md`](./docs/decision-note-delivery-machine-invariants-2026-08-02.md)。
- 对齐审计 F-1…F-9 已闭环；N-3 entrypoint 已修复；DinD / uid / 路径映射等残留见下表。

### 已知阻断（更新于 2026-08-01）

| # | 问题 | 影响 |
|---|---|---|
| N-3 | 构建镜像 ENTRYPOINT 吞掉 `sh -lc`（**已修复 2026-08-01**：`core/src/regent/infrastructure/sandbox.py:237-245` 已显式传 `--entrypoint sh`） | 命令未真执行 → 已收敛 |
| **N-3c** | worker uid 65534 vs 沙箱 65532 → 写盘 EACCES（残留，未强制校验） | `echo` 可假绿；待生产主机验收 |
| **N-3d** | 容器路径当 `--mount src` → 常静默挂空目录（残留，已 fail-closed 兜底） | 看不到 workspace 文件；待生产主机验收 |
| N-3b | compose 无 docker.sock | 容器化 worker 无法 DinD |
| N-2 | 运维未声明 sandbox 模式 / 支持矩阵 | Path B/A 配套缺失 |
| — | F-1/F-3 等修复缺 T1–T6 守卫 | 已修问题可复发；CD-6.5 补齐 |

工作包展开：[`docs/cd6-execution-plan-2026-07-31.md`](./docs/cd6-execution-plan-2026-07-31.md)。

---

## 更多开发入口

- [产品定义与需求](./Regent-PRD.md)（CURRENT）
- [技术架构与实施规范](./Regent-Technical-Spec.md)（CURRENT；§21 双列 API 对照）
- [测量与决策框架](./Regent-Measurement-Decision-Framework.md)（CURRENT）
- [交付计划](./Regent-Plan.md)（ACTIVE，**唯一编码执行清单**）
- [M6 Canary 观察窗](./docs/m6-canary-watch-plan-2026-08-01.md)（HALTED_PENDING_QUALIFICATION）
- [Agent 内核可执行修复计划](./docs/agent-core-restoration-executable-plan-2026-08-01.md)
- [Token / Prompt Cache 修复计划](./docs/token-cost-cache-fix-plan-2026-08-01.md)
- [混合控制平面 H0–H2](./docs/decision-note-hybrid-h0-control-plane-2026-08-03.md)（2026-08-03）
- [Session Work Plan W0–W4](./docs/execution-plan-session-work-plan-2026-08-03.md)（2026-08-03）
- [控制台可观测性](./docs/console-observability-gap-2026-08-02.md)（2026-08-02）
- [下一步 CD-6…12](./docs/conversational-delivery-next-plan-2026-07-31.md)
- [CD-6 执行级工作包](./docs/cd6-execution-plan-2026-07-31.md)
- [对话式交付 CD-0…5](./docs/conversational-delivery-plan-2026-07-31.md)
- [永久定义（唯一规范源）](./docs/definitions/REGENT-DEFINITION-3.0.txt)（FROZEN）
- [部署（两套路径）](./docs/deployment.md)
- [文档索引](./docs/README.md)
