# Regent

Regent 是一个可治理、可审计、可恢复的自主产品生成 Core。它从产品目标出发，获取证据、形成候选假设、修订需求、解析能力、生成应用、完成隔离构建与发布，并根据真实观测决定继续、修订或停止。

## 当前状态（2026-08-01）

- P0 已形成可运行闭环：目标、工作项、执行、审批、证据、观测、恢复与审计。
- P1 已完成至 `0022`：GoalSpec 快照启动后由 Worker 持久化生成、检查并发布 Preview；对话可查询进度、失败可重试。
  - 主链路采用**快照启动 + 事后纠偏**，见 [`docs/decision-note-auto-start-journey-2026-07-31.md`](./docs/decision-note-auto-start-journey-2026-07-31.md)。
- **生成策略**：代码默认仍为 `artifact-backed`；GQ-4（默认切 `agentic`）**未晋级**。生产已开 **M6 5% agentic canary**（`canary_gate=true`、`canary_percent=5`），见 [`docs/m6-canary-window-2026-08-01.json`](./docs/m6-canary-window-2026-08-01.json)；观察与窗末决策见 [`docs/m6-canary-watch-plan-2026-08-01.md`](./docs/m6-canary-watch-plan-2026-08-01.md)。成本/质量护栏未过前不扩 10%、不翻转默认策略。
- **Prompt cache / token 成本**：agentic 上下文改为「稳定前缀 + 对话 + 易变后缀」，workspace 默认仅路径树；解析并累计 `cached_tokens`。计划：[`docs/token-cost-cache-fix-plan-2026-08-01.md`](./docs/token-cost-cache-fix-plan-2026-08-01.md)。
- **交付缺口**：进度停滞与交付缺口优先自动续跑 / 软暂停（对话补充方向），**不**对缺口卡弹出「总是允许」；真审批仍限于发布、质量门、外部效应、Permit。
- Agent 内核 M0–M5 工程接线已落地；M6 观察窗 ACTIVE。GQ-4 仍 PENDING。
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
- [M6 Canary 观察窗](./docs/m6-canary-watch-plan-2026-08-01.md)（ACTIVE）
- [Agent 内核可执行修复计划](./docs/agent-core-restoration-executable-plan-2026-08-01.md)
- [Token / Prompt Cache 修复计划](./docs/token-cost-cache-fix-plan-2026-08-01.md)
- [下一步 CD-6…12](./docs/conversational-delivery-next-plan-2026-07-31.md)
- [CD-6 执行级工作包](./docs/cd6-execution-plan-2026-07-31.md)
- [对话式交付 CD-0…5](./docs/conversational-delivery-plan-2026-07-31.md)
- [永久定义（唯一规范源）](./docs/definitions/REGENT-DEFINITION-1.0.txt)（FROZEN）
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
