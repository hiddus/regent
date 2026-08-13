# Regent

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

**Regent 是围绕经营目标持续工作的智能体团队。**它连接业务数据，主动发现机会、执行获授权的行动，并根据真实经营结果持续调整。

当前商业切入点是**互联网产品增长经营**：先围绕一个明确增长指标，在 6–8 周付费试点内完成经营体检、机会发现、低风险实验、结果验证和周期复盘。应用生成是团队可调用的能力，不是 Regent 本身。

需要明确区分：Regent 3.0 的愿景是能够长期自主探索、组织和进化的智能体经营团队；当前已经具备的是受治理的目标执行、应用生成、验证、预览发布和初步观测/决策闭环。持续在线经营学习、自由拓扑的生产权限继承，以及景区、智慧城市等完整行业经营能力仍在建设中，不作为当前销售承诺。

首期客户获得：一份 Goal Charter、一个目标与护栏指标、限定数据和行动权限、预算封顶、每周经营报告，以及试点结束时可审计的扩大、修订、停止或移交建议。定价建议采用“接入与目标设计费 + 团队服务费 + 超额资源实耗 + 可选的可归因结果奖金”，不按 Agent 数量收费。

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

## 架构边界

1. Core Kernel：状态机、治理、证据、审计、恢复、预算和安全边界。
2. Certified Capability Pool：可声明、可验证、可替换的通用能力。
3. Generated Apps：由 Core 根据目标、证据与约束生成，不由 Core 预置各种业务页面。

## 开发入口

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

编码冲突时：产品语义以 PRD 为准，技术实现以 Technical-Spec 为准，阶段顺序以 Plan 为准；任何冲突必须通过 ADR 或 DecisionRecord 解决。

## 仓库结构

| 路径 | 说明 | 详情 |
|---|---|---|
| `core/` | Regent 后端核心（FastAPI + Worker） | [core/README.md](./core/README.md) |
| `capabilities/` | 认证能力池 | [capabilities/README.md](./capabilities/README.md) |
| `apps/regent-console/` | Web 控制台 | [apps/regent-console/README.md](./apps/regent-console/README.md) |
| `apps/regent-desktop/` | 桌面端（探索性非目标） | [apps/regent-desktop/README.md](./apps/regent-desktop/README.md) |
| `tests/` | architecture / integration / unit | [tests/README.md](./tests/README.md) |
| `fixtures/` | 评测固定数据 | [fixtures/README.md](./fixtures/README.md) |
| `scripts/` | 仓库级辅助脚本 | [scripts/README.md](./scripts/README.md) |
| `ops/` | 运维脚本与门禁 | [ops/README.md](./ops/README.md) |
| `deploy/` | 部署相关 | [deploy/README.md](./deploy/README.md) |
| `docs/` | 规范、计划、审计 | [docs/README.md](./docs/README.md) |
| `compose.yaml` | **本地** Compose（与服务器 S0 手工编排不同，见 deployment.md） | — |
