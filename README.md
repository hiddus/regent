# Regent

Regent 是一个可治理、可审计、可恢复的自主产品生成 Core。它从产品目标出发，获取证据、形成候选假设、修订需求、解析能力、生成应用、完成隔离构建与发布，并根据真实观测决定继续、修订或停止。

## 当前状态（2026-07-31）

- P0 已形成可运行闭环：目标、工作项、执行、审批、证据、观测、恢复与审计。
- P1 已完成至 `0022`：GoalSpec 快照启动后由 Worker 持久化生成、检查并发布 Preview；对话可查询进度、失败可重试。
  - 主链路采用**快照启动 + 事后纠偏**，见 [`docs/decision-note-auto-start-journey-2026-07-31.md`](./docs/decision-note-auto-start-journey-2026-07-31.md)。
- GQ-0～GQ-4 控制流：**已实现但默认不可启用**（`canary_gate=False`、`canary_percent=0`、默认 `artifact-backed`）。这是规范门禁，不是缺陷。
- 对话式交付 CD-0…CD-5 代码侧已落地；**下一步 ACTIVE**：[`docs/conversational-delivery-next-plan-2026-07-31.md`](./docs/conversational-delivery-next-plan-2026-07-31.md)（CD-6…12 重订）。GQ-4 仍 PENDING。
- 对齐审计 F-1…F-9 已闭环；§8 登记 N-3 族阻断真执行（见下表）。

### 已知阻断（2026-07-31；CD-6 须全绿）

| # | 问题 | 影响 |
|---|---|---|
| N-3 | 构建镜像 ENTRYPOINT 吞掉 `sh -lc`；须专用 agent-exec 镜像 | 命令未真执行 |
| **N-3c** | worker uid 65534 vs 沙箱 65532 → 写盘 EACCES | `echo` 可假绿 |
| **N-3d** | 容器路径当 `--mount src` → 常静默挂空目录 | 看不到 workspace 文件 |
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
- [下一步 CD-6…12](./docs/conversational-delivery-next-plan-2026-07-31.md)（ACTIVE 重订）
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
